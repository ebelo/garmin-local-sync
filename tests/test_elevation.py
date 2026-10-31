import json
import math
import sqlite3
from dataclasses import replace

import pytest
from osgeo import ogr
from osgeo_utils.samples.validate_gpkg import check

from garmin_local_sync.adapters.geometry import ELEVATION_METADATA, activity_geometries, activity_track_3d
from garmin_local_sync.adapters.geopackage import GeoPackageRepository
from garmin_local_sync.adapters.validation import check_store
from garmin_local_sync.domain.models import Sample
from garmin_local_sync.example import create_example


def observations(points):
    return [Sample(i, latitude=y, longitude=x, altitude_m=z) for i, (x, y, z) in enumerate(points)]


def coordinates(geometry):
    return json.loads(geometry.ExportToJson())["coordinates"] if geometry else None


def test_missing_elevation_splits_3d_but_retains_complete_horizontal_route():
    samples = observations([(1, 2, -10), (2, 3, 0), (3, 4, None), (4, 5, 30), (5, 6, 40)])
    track = activity_track_3d(samples)
    horizontal, _ = activity_geometries(samples)
    assert coordinates(track) == [[[1, 2, -10], [2, 3, 0]], [[4, 5, 30], [5, 6, 40]]]
    assert coordinates(horizontal) == [[[1, 2], [2, 3], [3, 4], [4, 5], [5, 6]]]
    assert samples[2].altitude_m is None


@pytest.mark.parametrize("missing", [None, math.nan, math.inf, -math.inf, True])
def test_nonfinite_or_non_numeric_elevation_never_becomes_a_3d_vertex(missing):
    samples = observations([(1, 2, 10), (2, 3, missing), (3, 4, 30)])
    assert activity_track_3d(samples) is None


@pytest.mark.parametrize("direction", [1, -1])
def test_date_line_z_is_interpolated_only_for_derived_boundary_vertices(direction):
    samples = observations([(direction * 179, 10, 100), (-direction * 179, 12, 200)])
    track = activity_track_3d(samples)
    assert coordinates(track) == [
        [[direction * 179, 10, 100], [direction * 180, 11, 150]],
        [[-direction * 180, 11, 150], [-direction * 179, 12, 200]],
    ]
    assert [s.altitude_m for s in samples] == [100, 200]
    assert len(samples) == 2


def test_finite_extreme_heights_do_not_overflow_boundary_interpolation():
    track = activity_track_3d(observations([(179, 10, -1e308), (-179, 12, 1e308)]))
    assert coordinates(track)[0][-1][2] == 0
    assert coordinates(track)[1][0][2] == 0


@pytest.mark.parametrize("break_kind", ["gps", "segment", "backwards_time"])
def test_3d_preserves_other_segment_breaks(break_kind):
    samples = observations([(1, 2, 10), (2, 3, 20), (3, 4, 30), (4, 5, 40)])
    if break_kind == "gps":
        samples.insert(2, Sample(10, altitude_m=25))
    elif break_kind == "segment":
        samples[2:] = [replace(s, segment_index=1) for s in samples[2:]]
    else:
        samples = [replace(s, timestamp_utc=t) for s, t in zip(samples, ["b", "c", "a", "d"], strict=True)]
    assert coordinates(activity_track_3d(samples)) == [[[1, 2, 10], [2, 3, 20]], [[3, 4, 30], [4, 5, 40]]]


def test_repeated_xyz_is_deduplicated_but_vertical_movement_is_preserved(tmp_path):
    samples = observations([(0, 0, 100), (0, 0, 100), (0, 0, 110)])
    assert coordinates(activity_track_3d(samples)) == [[[0, 0, 100], [0, 0, 110]]]
    horizontal, _ = activity_geometries(samples)
    assert horizontal is None
    store = GeoPackageRepository(tmp_path / "vertical.gpkg")
    try:
        store.save_summaries([])
        with store.db:
            store.save_summary({"activityId": 1, "activityType": {}})
        store.save_activity(1, {}, samples, [], "synthetic")
        report = check_store(store)
        assert report["valid"], report["errors"]
        assert report["geographic_layers"]["activity_tracks_3d"] == 1
    finally:
        store.close()
    # GEOS checks only XY; the independent GeoPackage validator accepts XYZ paths.
    assert not check(str(tmp_path / "vertical.gpkg"), extra_checks=True, warning_as_error=True)


def test_elevation_layer_registration_metadata_and_reference_are_persistent(tmp_path):
    path = tmp_path / "example.gpkg"
    create_example(path)
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT geometry_type_name,srs_id,z,m FROM gpkg_geometry_columns WHERE table_name='activity_tracks_3d'"
        ).fetchone() == ("MULTILINESTRING", 4326, 1, 0)
        assert db.execute(
            "SELECT reference_scope FROM gpkg_metadata_reference WHERE table_name='activity_tracks_3d'"
        ).fetchone() == ("table",)
    ds = ogr.Open(str(path))
    layer = ds.GetLayerByName("activity_tracks_3d")
    assert ELEVATION_METADATA.items() <= layer.GetMetadata().items()
    assert layer.GetSpatialRef().GetAuthorityCode(None) == "4326"
    assert layer.GetMetadataItem("vertical_crs") == "EPSG:5714"
    assert layer.GetMetadataItem("vertical_crs_assignment").startswith("descriptive")
    ds = None


def test_3d_validation_detects_a_finite_but_wrong_recorded_height(tmp_path):
    path = tmp_path / "example.gpkg"
    create_example(path)
    ds = ogr.Open(str(path), 1)
    layer = ds.GetLayerByName("activity_tracks_3d")
    feature = layer.GetNextFeature()
    geometry = feature.GetGeometryRef().Clone()
    line = geometry.GetGeometryRef(0)
    x, y, z = line.GetPoint(0)
    line.SetPoint(0, x, y, z + 1)
    feature.SetGeometry(geometry)
    layer.SetFeature(feature)
    feature = None
    layer = None
    ds = None
    store = GeoPackageRepository(path, read_only=True)
    report = check_store(store)
    store.close()
    assert not report["valid"]
    assert any("differs from source samples" in message for message in report["errors"])


@pytest.mark.parametrize("after_geometry", [False, True])
def test_interrupted_refresh_retries_all_geometry_and_source_data(tmp_path, monkeypatch, after_geometry):
    path = tmp_path / "example.gpkg"
    create_example(path)
    store = GeoPackageRepository(path)
    samples = observations([(10, 20, 100.123456789), (11, 21, 120.987654321)])
    original = store._write_geometries

    def fail(aid, geometries):
        if after_geometry:
            original(aid, geometries)
        raise RuntimeError("injected geometry failure")

    try:
        monkeypatch.setattr(store, "_write_geometries", fail)
        with pytest.raises(RuntimeError, match="injected geometry failure"):
            store.save_activity(1001, {}, samples, [], "synthetic")
        assert store.db.execute(
            "SELECT samples_synced_at FROM activities WHERE activity_id=1001"
        ).fetchone() == (None,)
        assert check_store(store)["incomplete_activities"] == 1
        monkeypatch.setattr(store, "_write_geometries", original)
        store.save_activity(1001, {}, samples, [], "synthetic")
        report = check_store(store)
        assert report["valid"], report["errors"]
        assert report["incomplete_activities"] == 0
        assert store.db.execute(
            "SELECT longitude,latitude,altitude_m FROM activity_samples "
            "WHERE activity_id=1001 ORDER BY sample_index"
        ).fetchall() == [(10, 20, 100.123456789), (11, 21, 120.987654321)]
        ds = ogr.Open(str(path))
        for name, expected in (
            ("activity_tracks", [(10, 20), (11, 21)]),
            ("activity_tracks_3d", [(10, 20, 100.123456789), (11, 21, 120.987654321)]),
            ("activity_starts", [(10, 20)]),
        ):
            layer = ds.GetLayerByName(name)
            layer.SetAttributeFilter("activity_id=1001")
            assert layer.GetFeatureCount() == 1
            feature = layer.GetNextFeature()
            geometry = feature.GetGeometryRef()
            points = (
                geometry.GetPoints() if name == "activity_starts" else geometry.GetGeometryRef(0).GetPoints()
            )
            assert points == expected
        feature = layer = ds = None
    finally:
        store.close()


def test_3d_validation_detects_a_missing_track_for_completed_source(tmp_path):
    path = tmp_path / "example.gpkg"
    create_example(path)
    ds = ogr.Open(str(path), 1)
    layer = ds.GetLayerByName("activity_tracks_3d")
    feature = layer.GetNextFeature()
    layer.DeleteFeature(feature.GetFID())
    feature = layer = ds = None
    store = GeoPackageRepository(path, read_only=True)
    try:
        report = check_store(store)
        assert not report["valid"]
        assert any("differs from source samples" in message for message in report["errors"])
    finally:
        store.close()


@pytest.mark.parametrize("value", [True, "invalid height"])
def test_invalid_numeric_elevation_is_null_in_storage_without_losing_raw_provenance(tmp_path, value):
    from garmin_local_sync.adapters.decoding import sample_from_values

    store = GeoPackageRepository(tmp_path / "invalid.gpkg")
    with store.db:
        store.save_summary({"activityId": 1, "activityType": {}})
    samples = [sample_from_values(i, {"latitude": i, "longitude": i, "altitude": value}) for i in range(2)]
    store.save_activity(1, {}, samples, [], "synthetic")
    assert store.db.execute("SELECT altitude_m FROM activity_samples").fetchall() == [(None,), (None,)]
    assert (
        json.loads(store.db.execute("SELECT raw_json FROM activity_samples LIMIT 1").fetchone()[0])[
            "altitude"
        ]
        == value
    )
    assert check_store(store)["valid"]
    store.close()


def test_validator_rejects_vertical_metadata_that_claims_ellipsoidal_heights(tmp_path):
    path = tmp_path / "example.gpkg"
    create_example(path)
    ds = ogr.Open(str(path), 1)
    layer = ds.GetLayerByName("activity_tracks_3d")
    layer.SetMetadataItem("vertical_crs", "EPSG:4979")
    layer = None
    ds = None
    store = GeoPackageRepository(path, read_only=True)
    report = check_store(store)
    store.close()
    assert not report["valid"]
    assert any("elevation-reference metadata" in message for message in report["errors"])
