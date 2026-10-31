import json
import math
import sqlite3
from dataclasses import replace

import pytest
from osgeo import ogr
from osgeo_utils.samples.validate_gpkg import check

from garmin_local_sync import cli
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


def legacy_database(path):
    create_example(path)
    ds = ogr.Open(str(path), 1)
    index = next(i for i in range(ds.GetLayerCount()) if ds.GetLayer(i).GetName() == "activity_tracks_3d")
    ds.DeleteLayer(index)
    ds = None
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE activities DROP COLUMN geometry_3d_synced_at")
        db.execute("UPDATE schema_version SET version=2")
    return path


def source_snapshot(path):
    with sqlite3.connect(path) as db:
        result = {}
        for table in (
            "activities",
            "activity_samples",
            "activity_fit_messages",
            "api_responses",
            "wellness_records",
            "daily_metrics",
        ):
            columns = [
                r[1] for r in db.execute(f"PRAGMA table_info({table})") if r[1] != "geometry_3d_synced_at"
            ]
            result[table] = db.execute(f"SELECT {','.join(columns)} FROM {table} ORDER BY 1").fetchall()
    ds = ogr.Open(str(path))
    for name in ("activity_tracks", "activity_starts"):
        result[name] = [
            (f.items(), bytes(f.GetGeometryRef().ExportToIsoWkb())) for f in ds.GetLayerByName(name)
        ]
    return result


def test_offline_upgrade_rebuild_is_incremental_and_preserves_existing_data(tmp_path, monkeypatch, capsys):
    path = legacy_database(tmp_path / "legacy.gpkg")
    snapshot = source_snapshot(path)
    before = path.read_bytes()
    store = GeoPackageRepository(path, read_only=True)
    assert store.schema_version == 2
    assert check_store(store)["valid"]
    store.close()
    assert path.read_bytes() == before

    def no_api(*args):
        raise AssertionError("Offline rebuild must not authenticate or use Garmin")

    monkeypatch.setattr(cli, "Api", no_api)
    args = ["rebuild-3d", "--database", str(path), "--root", str(tmp_path)]
    assert cli.main(args + ["--activity-limit", "1"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["activities_processed"] == 1
    assert report["pending_3d_activities"] == 3
    assert cli.main(args) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["activities_processed"] == 3
    assert report["pending_3d_activities"] == 0
    assert cli.main(args) == 0
    assert json.loads(capsys.readouterr().out)["activities_processed"] == 0
    assert source_snapshot(path) == snapshot
    assert not check(str(path), extra_checks=True, warning_as_error=True)


@pytest.mark.parametrize("after_layer", [False, True])
def test_interrupted_migration_retries_without_duplicate_columns(tmp_path, monkeypatch, after_layer):
    path = legacy_database(tmp_path / "legacy.gpkg")
    original = GeoPackageRepository._ensure_3d_layer

    def fail(self):
        if after_layer:
            original(self)
        raise RuntimeError("injected layer creation failure")

    monkeypatch.setattr(GeoPackageRepository, "_ensure_3d_layer", fail)
    with pytest.raises(RuntimeError, match="injected"):
        GeoPackageRepository(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT version FROM schema_version").fetchone()[0] == 2
    monkeypatch.setattr(GeoPackageRepository, "_ensure_3d_layer", original)
    store = GeoPackageRepository(path)
    assert store.schema_version == 3
    assert store.pending_3d_count() == 4
    assert store.rebuild_3d()["activities_processed"] == 4
    assert check_store(store)["valid"]
    store.close()


def test_failed_rebuild_keeps_checkpoint_pending_and_resume_retries_only_failed_item(tmp_path, monkeypatch):
    path = legacy_database(tmp_path / "legacy.gpkg")
    store = GeoPackageRepository(path)
    original = store._write_geometries

    def fail(aid, geometries):
        if aid == 1001:
            raise RuntimeError("injected geometry failure")
        original(aid, geometries)

    monkeypatch.setattr(store, "_write_geometries", fail)
    report = store.rebuild_3d()
    assert report["activities_failed"] == 1
    assert report["pending_3d_activities"] == 1
    assert store.has_failure("geometry3d:1001")
    monkeypatch.setattr(store, "_write_geometries", original)
    report = store.rebuild_3d()
    assert report["activities_processed"] == 1
    assert report["pending_3d_activities"] == 0
    assert not store.has_failure("geometry3d:1001")
    assert check_store(store)["valid"]
    store.close()


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


def test_rebuild_of_a_missing_database_does_not_create_it(tmp_path):
    root = tmp_path / "missing"
    assert cli.main(["rebuild-3d", "--root", str(root)]) == 1
    assert not root.exists()


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


def test_rebuild_refuses_an_active_writer_before_migrating(tmp_path, capsys):
    import fcntl

    path = legacy_database(tmp_path / "legacy.gpkg")
    before = path.read_bytes()
    with path.with_suffix(".gpkg.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert cli.main(["rebuild-3d", "--database", str(path), "--root", str(tmp_path)]) == 1
    assert "Another sync/check" in capsys.readouterr().err
    assert path.read_bytes() == before


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
