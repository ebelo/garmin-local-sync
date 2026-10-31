"""Create a small, entirely synthetic GeoPackage without credentials or network."""

import argparse
import datetime as dt
import json
import os
from pathlib import Path

from .config import UTC
from .formats import sample_row
from .storage import Store, status
from .validation import check_store


def create_example(path):
    path = Path(path).expanduser().resolve()
    if path.exists():
        raise FileExistsError("Refusing to replace an existing example/database")
    store = Store(path)
    base = dt.datetime(2024, 1, 15, 10, tzinfo=UTC)
    routes = [
        (
            1001,
            "Synthetic separated segments",
            [(20, 10, 100, 0), (20.001, 10.001, None, 0), (20.002, 10.002, 103, 1), (20.003, 10.003, 104, 1)],
        ),
        (1002, "Synthetic date-line crossing", [(179.5, -10, 5, 0), (-179.5, -10, 6, 0)]),
        (1003, "Synthetic zero-coordinate observation", [(0, 0, None, 0)]),
        (1004, "Synthetic non-geographic activity", [(None, None, None, 0)]),
    ]
    try:
        for aid, name, coordinates in routes:
            with store.db:
                store.save_summary(
                    {
                        "activityId": aid,
                        "activityName": name,
                        "ownerId": 100,
                        "startTimeLocal": "2024-01-15 10:00:00",
                        "startTimeGMT": "2024-01-15 10:00:00",
                        "activityType": {"typeKey": "synthetic"},
                        "duration": 600,
                        "distance": 500,
                    }
                )
            samples = [
                sample_row(
                    aid,
                    i,
                    {
                        "timestamp": base + dt.timedelta(seconds=i * 30),
                        "latitude": lat,
                        "longitude": lon,
                        "altitude": altitude,
                        "heart_rate": 100 + i,
                    },
                    segment,
                )
                for i, (lon, lat, altitude, segment) in enumerate(coordinates)
            ]
            store.save_activity(aid, {"synthetic": True}, samples, [], "synthetic")
        store.response(
            "daily",
            "2024-01-15/2024-01-15",
            {
                "data": [
                    {
                        "calendarDate": "2024-01-15",
                        "includesWellnessData": True,
                        "movement": {"steps": {"value": 8200}},
                        "heartRate": {"restingValue": 55},
                    }
                ]
            },
            "2024-01-15",
            "2024-01-15",
        )
        store.response(
            "sleep",
            "2024-01-15/2024-01-15",
            [{"calendarDate": "2024-01-15", "sleepTimeSeconds": 27000}],
            "2024-01-15",
            "2024-01-15",
        )
        result = {"synthetic": True, **status(store), "check": check_store(store)}
        if not result["check"]["valid"]:
            raise RuntimeError("Synthetic example failed its geographic validation")
        return result
    finally:
        store.close()


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(create_example(args.output), indent=2))
        return 0
    except (OSError, RuntimeError) as error:
        parser.exit(1, f"Example not created: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
