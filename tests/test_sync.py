import datetime as dt
import io
import json
import zipfile

import pytest
from osgeo import ogr

from garmin_local_sync.api import Api, ApiError
from garmin_local_sync.formats import parse_archive, sample_row
from garmin_local_sync.storage import Store
from garmin_local_sync.sync import covered, intervals, sync_wellness
from garmin_local_sync.util import dumps


@pytest.fixture
def store(tmp_path):
    db = Store(tmp_path / "test.gpkg")
    yield db
    db.close()


def summary(aid=1):
    return {
        "activityId": aid,
        "activityName": "Test",
        "startTimeLocal": "2026-10-29 10:00:00",
        "startTimeGMT": "2026-10-29 09:00:00",
        "activityType": {"typeKey": "hiking"},
        "distance": 1500,
        "duration": 3600,
    }


GPX = b"""<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>
<trkpt lat="46.2" lon="7.3"><ele>500</ele><time>2026-10-29T09:00:00Z</time></trkpt>
<trkpt lat="46.3" lon="7.4"><ele>600</ele><time>2026-10-29T10:00:00Z</time></trkpt>
</trkseg></trk></gpx>"""


def test_archive_to_real_gpkg_is_idempotent_and_axis_order_is_correct(store):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("../../track.gpx", GPX)  # ZIP members are read in memory, never extracted.
    rows, messages, fmt = parse_archive(archive.getvalue(), 1)
    with store.db:
        store.save_summary(summary())
    store.save_activity(1, {"detail": True}, rows, messages, fmt)
    store.save_activity(1, {"detail": True}, rows, messages, fmt)
    assert store.db.execute("SELECT count(*) FROM activity_samples").fetchone()[0] == 2
    assert store.db.execute("SELECT count(*) FROM activity_tracks").fetchone()[0] == 1
    assert store.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not store.db.execute("PRAGMA foreign_key_check").fetchall()
    ds = ogr.Open(str(store.path))
    feature = ds.GetLayerByName("activity_tracks").GetNextFeature()
    coords = json.loads(feature.GetGeometryRef().ExportToJson())["coordinates"]
    assert coords == [[[7.3, 46.2], [7.4, 46.3]]]
    assert rows[0][5] == 500
    assert rows[0][2] == "2026-10-29T09:00:00+00:00"


def test_summary_refresh_preserves_import_checkpoint(store):
    with store.db:
        store.save_summary(summary())
    store.save_activity(1, {}, [], [], "manual")
    changed = summary()
    changed["activityName"] = "Renamed"
    with store.db:
        store.save_summary(changed)
    name, synced = store.db.execute("SELECT name,samples_synced_at FROM activities").fetchone()
    assert name == "Renamed" and synced


def test_account_key_change_cannot_mix_different_owners(store):
    a = summary()
    a["ownerId"] = 123
    with store.db:
        store.save_summary(a)
    b = summary(2)
    b["ownerId"] = 456
    with pytest.raises(RuntimeError, match="different Garmin account"):
        store.save_summary(b)
    assert store.db.execute("SELECT count(*) FROM activities").fetchone()[0] == 1


def test_failed_geometry_does_not_mark_activity_complete(store, monkeypatch):
    with store.db:
        store.save_summary(summary())

    def fail(*args):
        raise RuntimeError("injected failure")

    monkeypatch.setattr(store, "save_geometry", fail)
    with pytest.raises(RuntimeError):
        store.save_activity(1, {}, [], [], "fit")
    assert store.db.execute("SELECT samples_synced_at FROM activities").fetchone()[0] is None


def test_health_overlapping_windows_replace_corrections_and_removed_data(store):
    data = {
        "data": [
            {"calendarDate": "2026-10-29", "uuid": "old", "movement": {"steps": {"value": 10}}},
            {"calendarDate": "2026-10-30", "uuid": "gone", "movement": {"steps": {"value": 20}}},
        ]
    }
    store.response("daily", "2026-10-29/2026-10-30", data, "2026-10-29", "2026-10-30")
    store.response(
        "daily",
        "2026-10-29/2026-10-30",
        {"data": [{"calendarDate": "2026-10-29", "uuid": "new", "movement": {"steps": {"value": 99}}}]},
        "2026-10-29",
        "2026-10-30",
    )
    assert store.db.execute("SELECT calendar_date,steps FROM daily_analytics").fetchall() == [
        ("2026-10-29", 99)
    ]
    assert store.db.execute("SELECT count(*) FROM wellness_records").fetchone()[0] == 1


def test_health_error_is_not_checkpointed_and_next_run_retries(store):
    class FakeApi:
        failed = True

        def get(self, path, params=None):
            if self.failed:
                raise ApiError(400, path)
            return [{"calendarDate": "2020-01-01", "totalSteps": 123}]

    api = FakeApi()
    start = dt.date(2020, 1, 1)
    sync_wellness(api, store, start, start, datasets="steps")
    assert store.db.execute("SELECT count(*) FROM api_responses").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM sync_errors").fetchone()[0] == 1
    api.failed = False
    sync_wellness(api, store, start, start, datasets="steps")
    assert store.db.execute("SELECT count(*) FROM sync_errors").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM api_responses").fetchone()[0] == 1


def test_failed_refresh_of_previously_complete_history_is_retried(store):
    store.response("steps", "2020-01-01/2020-01-01", [], "2020-01-01", "2020-01-01")
    store.error("steps:2020-01-01/2020-01-01", ApiError(400, "/test"))

    class FakeApi:
        calls = 0

        def get(self, path, params=None):
            self.calls += 1
            return [{"calendarDate": "2020-01-01", "totalSteps": 999}]

    api = FakeApi()
    day = dt.date(2020, 1, 1)
    sync_wellness(api, store, day, day, datasets="steps")
    assert api.calls == 1
    assert store.db.execute("SELECT count(*) FROM sync_errors").fetchone()[0] == 0


def test_failed_large_date_range_splits_and_resumes_from_subranges(store):
    class FakeApi:
        calls = []

        def get(self, path, params=None):
            self.calls.append((params["startDate"], params["endDate"]))
            if params["startDate"] != params["endDate"]:
                raise ApiError(500, path)
            return {"data": [{"calendarDate": params["startDate"], "includesWellnessData": True}]}

    api = FakeApi()
    start, end = dt.date(2020, 1, 1), dt.date(2020, 1, 4)
    sync_wellness(api, store, start, end, datasets="daily")
    assert covered(store, "daily", start.isoformat(), end.isoformat())
    assert len(api.calls) == 7
    assert store.db.execute("SELECT count(*) FROM sync_errors").fetchone()[0] == 0
    sync_wellness(api, store, start, end, datasets="daily")
    assert len(api.calls) == 7


def test_historical_coverage_requires_no_gaps(store):
    store.response("daily", "a", {"data": []}, "2020-01-01", "2020-01-02")
    store.response("daily", "b", {"data": []}, "2020-01-04", "2020-01-05")
    assert not covered(store, "daily", "2020-01-01", "2020-01-05")
    store.response("daily", "c", {"data": []}, "2020-01-03", "2020-01-03")
    assert covered(store, "daily", "2020-01-01", "2020-01-05")


def test_intervals_are_inclusive_and_non_overlapping():
    assert list(intervals(dt.date(2024, 2, 28), dt.date(2024, 3, 2), 2)) == [
        ("2024-02-28", "2024-02-29"),
        ("2024-03-01", "2024-03-02"),
    ]


def test_invalid_gps_stays_null_and_never_becomes_bogus_track(store):
    with store.db:
        store.save_summary(summary())
    rows = [sample_row(1, 0, {"latitude": 200, "longitude": 7, "heart_rate": 100})]
    store.save_activity(1, {}, rows, [], "fit")
    assert store.db.execute("SELECT latitude,longitude,heart_rate_bpm FROM activity_samples").fetchone() == (
        None,
        None,
        100,
    )
    assert store.db.execute("SELECT count(*) FROM activity_tracks").fetchone()[0] == 0


def test_fit_time_metadata_serializes():
    assert json.loads(dumps({"clock": dt.time(9, 30)}))["clock"] == "09:30:00"


def test_null_enhanced_fit_fields_fall_back_to_legacy_measurements():
    row = sample_row(1, 0, {"enhanced_altitude": None, "altitude": 500, "enhanced_speed": None, "speed": 3.5})
    assert row[5] == 500 and row[7] == 3.5


def test_multiple_wellness_activities_on_same_day_are_preserved(store):
    data = [
        {"calendarDate": "2020-01-01", "activityUuid": {"uuid": "one"}},
        {"calendarDate": "2020-01-01", "activityUuid": {"uuid": "two"}},
    ]
    store.response("wellness_activities", "2020-01-01/2020-01-01", data, "2020-01-01", "2020-01-01")
    assert store.db.execute("SELECT count(*) FROM wellness_records").fetchone()[0] == 2


def test_api_refuses_world_readable_credentials(tmp_path):
    key = tmp_path / "key"
    key.write_text("test-only-not-a-real-key")
    key.chmod(0o644)
    with pytest.raises(RuntimeError, match="private"):
        Api(key)


def test_401_reauths_once_and_does_not_follow_redirects(tmp_path, monkeypatch):
    key = tmp_path / "key"
    key.write_text("test-only-not-a-real-key")
    key.chmod(0o600)
    api = Api(key, delay=0)
    calls, auth = [], []

    class Response:
        def __init__(self, code):
            self.status_code = code
            self.headers = {}

        def json(self):
            return {"ok": True}

    class Session:
        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            return Response(401 if len(calls) == 1 else 200)

    api.session = Session()

    def authenticate(force=False):
        auth.append(force)
        api.token = "test-token"

    monkeypatch.setattr(api, "authenticate", authenticate)
    assert api.get("/test") == {"ok": True}
    assert auth == [False, True]
    assert all(c[2]["allow_redirects"] is False for c in calls)


def test_http_204_means_successful_absence_of_health_data(tmp_path, monkeypatch):
    key = tmp_path / "key"
    key.write_text("test-only-not-a-real-key")
    key.chmod(0o600)
    api = Api(key, delay=0)

    class Response:
        status_code = 204

    monkeypatch.setattr(api, "authenticate", lambda: None)
    monkeypatch.setattr(api, "_request", lambda *a, **kw: Response())
    api.token = "test-token"
    assert api.get("/test") is None


def test_multiple_archive_files_have_unique_message_and_sample_indices():
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("one.gpx", GPX)
        z.writestr("two.gpx", GPX)
    rows, _, _ = parse_archive(archive.getvalue(), 1)
    assert len(rows) == 4  # No fabricated sensor row at a file boundary.
    assert [r[1] for r in rows] == list(range(4))
    assert [r[13] for r in rows] == [0, 0, 1, 1]
