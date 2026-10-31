import datetime as dt
import json
import re
from collections import Counter
from pathlib import Path

from garmin_local_sync import backfill, cli
from garmin_local_sync.storage import Store
from garmin_local_sync.sync import sync_activity_workouts, sync_wellness


def test_all_data_covers_each_read_endpoint_and_reuses_checkpoints(tmp_path, monkeypatch):
    calls = []

    class FakeApi:
        def __init__(self, *args):
            pass

        def authenticate(self):
            pass

        def get(self, path, params=None, binary=False):
            calls.append((path, params))
            if "activitylist-service" in path:
                assert params["excludeChildren"] == "false"
                return [
                    {
                        "activityId": 1,
                        "startTimeLocal": "2020-01-01 12:00:00",
                        "activityType": {"typeKey": "strength_training"},
                    }
                ]
            if path.endswith("/workouts"):
                return [{"workoutName": "Synthetic workout", "steps": [{"index": 1}]}]
            if "/activity-service/activity/" in path:
                return {"metadataDTO": {"manualActivity": True}}
            if binary:
                from garmin_local_sync.api import ApiError

                raise ApiError(404, path)
            if "/weight/all/" in path:
                return [
                    {"calendarDate": "2020-01-01", "timestampGMT": 100, "weight": 70},
                    {"calendarDate": "2020-01-01", "timestampGMT": 200, "weight": 71},
                ]
            if "menstrualcycle" in path:
                return None  # HTTP 204 is an archived successful absence, not a failed day.
            return {"calendarDate": "2020-01-01", "value": 123, "readings": [[100, 70]]}

    monkeypatch.setattr(cli, "Api", FakeApi)
    args = [
        "sync",
        "--all-data",
        "--root",
        str(tmp_path),
        "--start",
        "2020-01-01",
        "--end",
        "2020-01-01",
        "--refresh-days",
        "0",
    ]
    assert cli.main(args) == 0
    # Twenty-six documented read operations; auth is transport, not a data endpoint.
    reference = json.loads((Path(__file__).parent / "fixtures/personal-api-paths.json").read_text())
    actual = {p for p, _ in calls}
    assert len(actual) == len(reference["get_paths"]) == 26
    for template in reference["get_paths"]:
        pattern = re.sub(r"\{[^}]+\}", r"[^/]+", template)
        assert sum(bool(re.fullmatch(pattern, p)) for p in actual) == 1, template
    first_calls = Counter(p for p, _ in calls)
    assert cli.main(args) == 0
    for path, count in Counter(p for p, _ in calls).items():
        assert count == first_calls[path] * (
            2
            if any(x in path for x in ["activitylist-service", "userprofile-service", "heartRateZones"])
            else 1
        )
    with StoreContext(tmp_path / "garmin.gpkg") as store:
        assert (
            store.db.execute(
                "SELECT count(*) FROM wellness_records WHERE dataset='weight_entries'"
            ).fetchone()[0]
            == 2
        )
        assert (
            store.db.execute(
                "SELECT count(*) FROM api_responses WHERE dataset LIKE 'menstrual_%' AND payload_json='null'"
            ).fetchone()[0]
            == 2
        )
        assert store.db.execute("SELECT count(*) FROM sync_errors").fetchone()[0] == 0
        assert store.db.execute("SELECT count(DISTINCT dataset) FROM api_responses").fetchone()[0] == 23


class StoreContext:
    def __init__(self, path):
        self.store = Store(path)

    def __enter__(self):
        return self.store

    def __exit__(self, *args):
        self.store.close()


def test_full_detail_options_and_weight_measurement_identity(tmp_path):
    calls = {}
    hits = []

    class FakeApi:
        def get(self, path, params=None):
            calls[path] = params
            hits.append(path)
            return None

    with StoreContext(tmp_path / "test.gpkg") as store:
        day = dt.date(2020, 1, 1)
        sync_wellness(FakeApi(), store, day, day, detailed=True)
        assert calls["/periodichealth-service/menstrualcycle/dayview/2020-01-01"]["includeAnalysis"] == "true"
        assert calls["/nutrition-service/food/logs/2020-01-01"]["includeMedia"] == "true"
        assert calls["/usersummary-service/usersummary/daily"] == {"calendarDate": "2020-01-01"}
        # Empty results still checkpoint and prevent expensive repeated date scans.
        count = len(hits)
        sync_wellness(FakeApi(), store, day, day, detailed=True)
        assert len(hits) == count


def test_activity_workouts_retry_independently_of_original_file(tmp_path):
    from garmin_local_sync.api import ApiError

    class FakeApi:
        fail = True

        def get(self, path):
            if self.fail:
                raise ApiError(500, path)
            return []

    with StoreContext(tmp_path / "test.gpkg") as store:
        with store.db:
            store.save_summary({"activityId": 1, "activityType": {}})
        api = FakeApi()
        assert sync_activity_workouts(api, store, refresh_days=0) == 0
        assert store.db.execute("SELECT count(*) FROM sync_errors").fetchone()[0] == 1
        api.fail = False
        assert sync_activity_workouts(api, store, refresh_days=0) == 1
        assert store.db.execute("SELECT count(*) FROM sync_errors").fetchone()[0] == 0
        assert sync_activity_workouts(api, store, refresh_days=0) == 0


def test_backfill_forwards_all_data_options_only_to_sync(tmp_path, monkeypatch):
    from types import SimpleNamespace

    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(
            returncode=0, stdout=json.dumps({"activities": 1, "activities_imported": 1, "sync_errors": 0})
        )

    monkeypatch.setattr(backfill.subprocess, "run", run)
    assert backfill.worker(tmp_path, all_data=True, refresh_days=0) == 0
    assert "--all-data" in calls[0]
    assert calls[0][calls[0].index("--refresh-days") + 1] == "0"
    assert all("--all-data" not in command for command in calls[1:])
    report = json.loads((tmp_path / "backfill-status.json").read_text())
    assert report["all_data"] is True
