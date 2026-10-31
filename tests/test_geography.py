import io
import json
import math
import struct
import zipfile
from dataclasses import replace
from itertools import pairwise

import pytest
from fitdecode.utils import compute_crc
from osgeo import ogr

from garmin_local_sync.adapters.decoding import parse_archive, parse_xml, sample_from_values
from garmin_local_sync.adapters.geometry import activity_geometries
from garmin_local_sync.adapters.geopackage import GeoPackageRepository
from garmin_local_sync.adapters.validation import check_store
from garmin_local_sync.example import create_example


def coordinates(geometry):
    return json.loads(geometry.ExportToJson())["coordinates"] if geometry else None


def rows(points):
    return [
        sample_from_values(i, {"longitude": x, "latitude": y}, segment)
        for i, (x, y, segment) in enumerate(points)
    ]


@pytest.mark.parametrize("a,b", [(179, -179), (-179, 179), (180, -179), (-180, 179)])
def test_date_line_splits_short_crossing_in_both_directions(a, b):
    track, _ = activity_geometries(rows([(a, 10, 0), (b, 12, 0)]))
    assert track is not None
    assert track.IsValid()
    for segment in coordinates(track):
        assert all(abs(x[0] - y[0]) <= 180 for x, y in pairwise(segment))
    assert math.isclose(
        sum(abs(s[-1][0] - s[0][0]) for s in coordinates(track)), min(abs(b - a), 360 - abs(b - a))
    )


def test_missing_gps_and_explicit_segments_are_not_bridged():
    track, _ = activity_geometries(
        rows([(1, 2, 0), (2, 3, 0), (None, None, 0), (4, 5, 0), (5, 6, 0), (6, 7, 1), (7, 8, 1)])
    )
    assert coordinates(track) == [[[1, 2], [2, 3]], [[4, 5], [5, 6]], [[6, 7], [7, 8]]]


def test_zero_coordinates_are_valid_but_a_stationary_point_is_not_a_line():
    track, start = activity_geometries(rows([(0, 0, 0), (0, 0, 0)]))
    assert track is None
    assert coordinates(start) == [0, 0]


def test_backwards_timestamps_break_geometry_without_reordering_samples():
    samples = rows([(1, 1, 0), (2, 2, 0), (3, 3, 0), (4, 4, 0)])
    samples = [
        replace(sample, timestamp_utc=t)
        for sample, t in zip(
            samples,
            [
                "2024-01-01T00:00:00+00:00",
                "2024-01-01T00:01:00+00:00",
                "2023-12-31T23:59:00+00:00",
                "2024-01-01T00:02:00+00:00",
            ],
            strict=True,
        )
    ]
    track, _ = activity_geometries(samples)
    assert coordinates(track) == [[[1, 1], [2, 2]], [[3, 3], [4, 4]]]


def test_gpx_segments_preserve_real_sample_count_and_unknown_elevation():
    data = b"""<gpx><trk><trkseg><trkpt lat="1" lon="2"><ele>40</ele></trkpt>
    <trkpt lat="2" lon="3"/></trkseg><trkseg><trkpt lat="3" lon="4"/>
    <trkpt lat="4" lon="5"/></trkseg></trk></gpx>"""
    samples, _ = parse_xml(data)
    assert len(samples) == 4
    assert [s.segment_index for s in samples] == [0, 0, 1, 1]
    assert [s.altitude_m for s in samples] == [40, None, None, None]
    track, _ = activity_geometries(samples)
    assert track.GetCoordinateDimension() == 2
    assert coordinates(track) == [[[2, 1], [3, 2]], [[4, 3], [5, 4]]]


def test_tcx_tracks_are_segmented_and_utc_offsets_are_normalized():
    data = b"""<TrainingCenterDatabase><Activities><Activity><Lap><Track>
    <Trackpoint><Time>2024-01-01T11:00:00+01:00</Time><Position>
    <LatitudeDegrees>45</LatitudeDegrees><LongitudeDegrees>90</LongitudeDegrees></Position>
    <AltitudeMeters>123</AltitudeMeters><HeartRateBpm><Value>120</Value></HeartRateBpm></Trackpoint>
    </Track><Track><Trackpoint><Time>2024-01-01T10:01:00Z</Time></Trackpoint>
    </Track></Lap></Activity></Activities></TrainingCenterDatabase>"""
    samples, _ = parse_xml(data)
    first = samples[0]
    assert first.timestamp_utc == "2024-01-01T10:00:00+00:00"
    assert (first.latitude, first.longitude, first.altitude_m) == (45, 90, 123)
    assert first.heart_rate_bpm == 120
    assert [s.segment_index for s in samples] == [0, 1]
    assert (samples[1].latitude, samples[1].longitude) == (None, None)


def test_xml_time_without_offset_is_not_invented_as_utc():
    samples, _ = parse_xml(b'<gpx><trkpt lat="1" lon="2"><time>2024-01-01T10:00:00</time></trkpt></gpx>')
    assert samples[0].timestamp_utc is None
    assert samples[0].raw["timestamp_unzoned"] == "2024-01-01T10:00:00"


def test_original_xml_with_utf8_bom_is_detected():
    decoded = parse_archive(b'\xef\xbb\xbf<gpx><trkpt lat="1" lon="2"/></gpx>')
    assert decoded.source_format == "xml"
    assert (decoded.samples[0].latitude, decoded.samples[0].longitude) == (1, 2)


def test_malformed_xml_is_a_recoverable_item_error():
    with pytest.raises(ValueError, match="Invalid GPX/TCX XML"):
        parse_archive(b"<gpx><broken>")


@pytest.mark.parametrize(
    "declaration",
    [
        '<!ENTITY secret SYSTEM "file:///etc/passwd">',
        '<!ENTITY secret "expanded entity content">',
    ],
)
def test_xml_entities_are_rejected_as_recoverable_decode_errors(declaration):
    from garmin_local_sync.adapters.decoding import OriginalActivityDecoder
    from garmin_local_sync.domain.errors import DecodeError

    blob = f'<!DOCTYPE gpx [{declaration}]><gpx><trkpt lat="1" lon="2"><name>&secret;</name></trkpt></gpx>'.encode()
    decoder = OriginalActivityDecoder()
    with pytest.raises(DecodeError, match="Invalid GPX/TCX XML"):
        decoder.decode(blob, 1)


def synthetic_fit():
    # Minimal synthetic FIT file, constructed from the published binary layout.
    definition = (
        bytes([0x40, 0, 0])
        + struct.pack("<HB", 20, 5)
        + bytes([253, 4, 0x86, 0, 4, 0x85, 1, 4, 0x85, 2, 2, 0x84, 3, 1, 0x02])
    )

    def record(second, lat, lon, altitude):
        return b"\x00" + struct.pack(
            "<IiiHB",
            1_100_000_000 + second,
            round(lat * 2**31 / 180),
            round(lon * 2**31 / 180),
            altitude,
            120,
        )

    event_def = bytes([0x41, 0, 0]) + struct.pack("<HB", 21, 3) + bytes([253, 4, 0x86, 0, 1, 0, 1, 1, 0])
    data = (
        definition
        + record(0, 45, 90, 3000)
        + record(10, 45.001, 90.001, 0xFFFF)
        + event_def
        + b"\x01"
        + struct.pack("<IBB", 1_100_000_015, 0, 4)
        + record(20, 45.002, 90.002, 3005)
        + record(30, 45.003, 90.003, 3010)
    )
    head = struct.pack("<BBHI4s", 14, 0x20, 2184, len(data), b".FIT")
    head += struct.pack("<H", compute_crc(head))
    blob = head + data
    blob += struct.pack("<H", compute_crc(blob))
    return blob


def test_fit_semicircles_altitude_and_timer_stop_with_real_binary_decoder():
    blob = synthetic_fit()
    decoded = parse_archive(blob)
    samples, messages = decoded.samples, decoded.messages
    assert decoded.source_format == "fit"
    assert len(samples) == 4
    assert (samples[0].latitude, samples[0].longitude) == pytest.approx((45, 90), abs=1e-6)
    assert samples[0].altitude_m == 100
    assert samples[1].altitude_m is None
    assert [s.segment_index for s in samples] == [0, 0, 1, 1]
    assert samples[0].timestamp_utc.endswith("+00:00")
    assert any(m.message_type == "event" for m in messages)


def test_multiple_fit_files_preserve_message_order_and_segment_boundaries():
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("one.fit", synthetic_fit())
        output.writestr("two.fit", synthetic_fit())
    decoded = parse_archive(archive.getvalue())
    assert [s.index for s in decoded.samples] == list(range(8))
    assert [s.segment_index for s in decoded.samples] == [0, 0, 1, 1, 2, 2, 3, 3]
    assert [m.index for m in decoded.messages] == [0, 1]
    assert [m.message_type for m in decoded.messages] == ["event", "event"]


def test_nonfinite_elevation_is_null_with_diagnostic_preserved():
    sample = sample_from_values(0, {"latitude": 1, "longitude": 2, "altitude": float("nan")})
    assert sample.altitude_m is None
    assert sample.raw["_nonfinite_values"] == {"altitude": "nan"}


def test_example_passes_independent_gdal_conformance_validator(tmp_path):
    from osgeo_utils.samples.validate_gpkg import check

    path = tmp_path / "example.gpkg"
    result = create_example(path)
    assert result["activity_samples"] == 8
    assert result["activity_tracks"] == 2
    assert result["check"]["valid"]
    assert not check(str(path), extra_checks=True, warning_as_error=True)


def test_geography_check_detects_a_corrupt_spatial_index(tmp_path):
    path = tmp_path / "example.gpkg"
    create_example(path)
    store = GeoPackageRepository(path)
    with store.db:
        store.db.execute("UPDATE rtree_activity_starts_geom SET minx=0,maxx=0,miny=0,maxy=0")
    report = check_store(store)
    store.close()
    assert not report["valid"]
    assert any("index does not contain" in e for e in report["errors"])


def test_spatial_index_is_updated_when_an_activity_is_refreshed(tmp_path):
    path = tmp_path / "example.gpkg"
    create_example(path)
    store = GeoPackageRepository(path)
    before = store.db.execute("SELECT count(*) FROM activity_tracks").fetchone()[0]
    store.save_activity(
        1001,
        {},
        [
            sample_from_values(i, {"longitude": x, "latitude": y})
            for i, (x, y) in enumerate([(-70, -40), (-69, -39)])
        ],
        [],
        "synthetic",
    )
    assert store.db.execute("SELECT count(*) FROM activity_tracks").fetchone()[0] == before
    assert check_store(store)["valid"]
    ds = ogr.Open(str(path))
    layer = ds.GetLayerByName("activity_tracks")
    layer.SetAttributeFilter("activity_id=1001")
    feature = layer.GetNextFeature()
    assert coordinates(feature.GetGeometryRef()) == [[[-70, -40], [-69, -39]]]
    ds = None
    store.close()
