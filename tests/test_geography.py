import json
import math
import struct
from itertools import pairwise

import pytest
from fitdecode.utils import compute_crc
from osgeo import ogr

from garmin_local_sync.example import create_example
from garmin_local_sync.formats import parse_archive, parse_xml, sample_row
from garmin_local_sync.geography import activity_geometries
from garmin_local_sync.storage import Store
from garmin_local_sync.validation import check_store


def coordinates(geometry):
    return json.loads(geometry.ExportToJson())["coordinates"] if geometry else None


def rows(points):
    return [
        sample_row(1, i, {"longitude": x, "latitude": y}, segment) for i, (x, y, segment) in enumerate(points)
    ]


@pytest.mark.parametrize("a,b", [(179, -179), (-179, 179), (180, -179), (-180, 179)])
def test_date_line_splits_short_crossing_in_both_directions(a, b):
    track, _ = activity_geometries(rows([(a, 10, 0), (b, 12, 0)]))
    assert track is not None and track.IsValid()
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
        (a, i, t, *row[3:])
        for row, t in zip(
            samples,
            [
                "2024-01-01T00:00:00+00:00",
                "2024-01-01T00:01:00+00:00",
                "2023-12-31T23:59:00+00:00",
                "2024-01-01T00:02:00+00:00",
            ],
            strict=True,
        )
        for a, i in [row[:2]]
    ]
    track, _ = activity_geometries(samples)
    assert coordinates(track) == [[[1, 1], [2, 2]], [[3, 3], [4, 4]]]


def test_gpx_segments_preserve_real_sample_count_and_unknown_elevation():
    data = b"""<gpx><trk><trkseg><trkpt lat="1" lon="2"><ele>40</ele></trkpt>
    <trkpt lat="2" lon="3"/></trkseg><trkseg><trkpt lat="3" lon="4"/>
    <trkpt lat="4" lon="5"/></trkseg></trk></gpx>"""
    samples, _ = parse_xml(data, 1)
    assert len(samples) == 4
    assert [s[13] for s in samples] == [0, 0, 1, 1]
    assert [s[5] for s in samples] == [40, None, None, None]
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
    samples, _ = parse_xml(data, 1)
    assert samples[0][2] == "2024-01-01T10:00:00+00:00"
    assert samples[0][3:6] == (45, 90, 123)
    assert samples[0][8] == 120
    assert [s[13] for s in samples] == [0, 1]
    assert samples[1][3:5] == (None, None)


def test_xml_time_without_offset_is_not_invented_as_utc():
    samples, _ = parse_xml(b'<gpx><trkpt lat="1" lon="2"><time>2024-01-01T10:00:00</time></trkpt></gpx>', 1)
    assert samples[0][2] is None
    assert json.loads(samples[0][12])["timestamp_unzoned"] == "2024-01-01T10:00:00"


def test_original_xml_with_utf8_bom_is_detected():
    rows, _, fmt = parse_archive(b'\xef\xbb\xbf<gpx><trkpt lat="1" lon="2"/></gpx>', 1)
    assert fmt == "xml" and rows[0][3:5] == (1, 2)


def test_malformed_xml_is_a_recoverable_item_error():
    with pytest.raises(ValueError, match="Invalid GPX/TCX XML"):
        parse_archive(b"<gpx><broken>", 1)


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
    with pytest.raises(DecodeError, match="Invalid GPX/TCX XML"):
        OriginalActivityDecoder().decode(blob, 1)


def test_fit_semicircles_altitude_and_timer_stop_with_real_binary_decoder():
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
    samples, messages, fmt = parse_archive(blob, 1)
    assert fmt == "fit" and len(samples) == 4
    assert samples[0][3:5] == pytest.approx((45, 90), abs=1e-6)
    assert samples[0][5] == 100 and samples[1][5] is None
    assert [s[13] for s in samples] == [0, 0, 1, 1]
    assert samples[0][2].endswith("+00:00")
    assert any(m[2] == "event" for m in messages)


def test_nonfinite_elevation_is_null_with_diagnostic_preserved():
    sample = sample_row(1, 0, {"latitude": 1, "longitude": 2, "altitude": float("nan")})
    assert sample[5] is None
    assert json.loads(sample[12])["_nonfinite_values"] == {"altitude": "nan"}


def test_example_passes_independent_gdal_conformance_validator(tmp_path):
    from osgeo_utils.samples.validate_gpkg import check

    path = tmp_path / "example.gpkg"
    result = create_example(path)
    assert result["activity_samples"] == 8 and result["activity_tracks"] == 2
    assert result["check"]["valid"]
    assert not check(str(path), extra_checks=True, warning_as_error=True)


def test_geography_check_detects_a_corrupt_spatial_index(tmp_path):
    path = tmp_path / "example.gpkg"
    create_example(path)
    store = Store(path)
    with store.db:
        store.db.execute("UPDATE rtree_activity_starts_geom SET minx=0,maxx=0,miny=0,maxy=0")
    report = check_store(store)
    store.close()
    assert not report["valid"]
    assert any("index does not contain" in e for e in report["errors"])


def test_spatial_index_is_updated_when_an_activity_is_refreshed(tmp_path):
    path = tmp_path / "example.gpkg"
    create_example(path)
    store = Store(path)
    before = store.db.execute("SELECT count(*) FROM activity_tracks").fetchone()[0]
    store.save_activity(
        1001,
        {},
        [
            sample_row(1001, i, {"longitude": x, "latitude": y})
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
