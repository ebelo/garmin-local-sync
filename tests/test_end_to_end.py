import io
import json
import zipfile
from collections import Counter

from garmin_local_sync import cli
from garmin_local_sync.adapters.geopackage import GeoPackageRepository
from garmin_local_sync.adapters.http import ApiError
from garmin_local_sync.adapters.validation import check_store


def test_complete_cli_import_retries_failed_activity_then_becomes_incremental(tmp_path, monkeypatch):
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w") as archive:
        archive.writestr(
            "synthetic.gpx",
            """<gpx><trk><trkseg>
            <trkpt lat="10" lon="20"><time>2024-01-01T10:00:00Z</time><ele>100</ele></trkpt>
            <trkpt lat="10.01" lon="20.01"><time>2024-01-01T10:01:00Z</time></trkpt>
            </trkseg></trk></gpx>""",
        )
    payload = blob.getvalue()
    summaries = [
        {
            "activityId": aid,
            "activityName": "Synthetic",
            "ownerId": 100,
            "startTimeLocal": "2024-01-01 10:00:00",
            "startTimeGMT": "2024-01-01 10:00:00",
            "activityType": {"typeKey": "hiking"},
            "distance": 1000,
            "duration": 600,
        }
        for aid in (901, 902)
    ]

    class FakeApi:
        fail_second = True
        downloads = []

        def __init__(self, *args, **kwargs):
            pass

        def authenticate(self):
            pass

        def get(self, path, params=None, binary=False):
            if "activitylist-service" in path:
                return summaries if params["start"] == 0 else []
            if "download-service" in path:
                aid = int(path.rsplit("/", 1)[-1])
                self.downloads.append(aid)
                if aid == 902 and self.fail_second:
                    raise ApiError(500, path)
                return payload
            if path.startswith("/activity-service/"):
                return {"metadataDTO": {"manualActivity": False}, "synthetic": True}
            if "dailysummaries" in path:
                return {"data": [{"calendarDate": "2024-01-01", "movement": {"steps": {"value": 1000}}}]}
            if "dailySleepSummariesByDate" in path:
                return [{"calendarDate": "2024-01-01", "sleepTimeSeconds": 27000}]
            return {"synthetic": True}

    monkeypatch.setattr(cli, "Api", FakeApi)
    root = tmp_path / "data"
    args = [
        "sync",
        "--root",
        str(root),
        "--api-key-file",
        str(tmp_path / "unused-key"),
        "--start",
        "2024-01-01",
        "--end",
        "2024-01-01",
        "--datasets",
        "daily,sleep",
        "--refresh-days",
        "0",
    ]
    assert cli.main(args) == 2
    FakeApi.fail_second = False
    assert cli.main(args) == 0
    assert Counter(FakeApi.downloads) == Counter({901: 1, 902: 2})
    assert cli.main(args) == 0
    assert Counter(FakeApi.downloads) == Counter({901: 1, 902: 2})
    path = root / "garmin.gpkg"
    store = GeoPackageRepository(path, read_only=True)
    assert store.db.execute("SELECT count(*) FROM activity_samples").fetchone()[0] == 4
    assert store.db.execute("SELECT count(*) FROM activity_tracks").fetchone()[0] == 2
    assert store.db.execute("SELECT count(*) FROM sync_errors").fetchone()[0] == 0
    assert check_store(store)["valid"]
    store.close()
    assert json.loads((root / "last-sync.json").read_text())["activities_imported"] == 2
    assert cli.main(["check", "--root", str(root)]) == 0
