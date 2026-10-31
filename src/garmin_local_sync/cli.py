"""Command-line entry point."""

import argparse
import contextlib
import datetime as dt
import fcntl
import json
import math
import os
import sys
from pathlib import Path

from .adapters.decoding import OriginalActivityDecoder
from .adapters.filesystem import FileArchiveStore
from .adapters.garmin import GarminSource
from .adapters.geopackage import GeoPackageRepository, status
from .adapters.http import Api
from .adapters.validation import check_store
from .application.sync import SyncService
from .config import DEFAULT_KEY, DEFAULT_ROOT
from .domain.models import SyncOptions
from .util import atomic_write, dumps, now


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["sync", "status", "check"], nargs="?", default="sync")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="Private archive/data directory")
    parser.add_argument("--database", type=Path, help="GeoPackage path; default ROOT/garmin.gpkg")
    parser.add_argument("--api-key-file", type=Path, default=DEFAULT_KEY)
    parser.add_argument(
        "--start", type=dt.date.fromisoformat, help="First health date; default earliest indexed activity"
    )
    parser.add_argument("--end", type=dt.date.fromisoformat, default=dt.date.today())
    parser.add_argument("--activity-limit", type=int, help="Process only the latest N activities")
    parser.add_argument("--skip-activities", action="store_true")
    parser.add_argument("--skip-health", action="store_true")
    parser.add_argument(
        "--all-data",
        action="store_true",
        help="Fetch every documented read endpoint, including daily details, workouts and child activities",
    )
    parser.add_argument(
        "--activity-workouts", action="store_true", help="Archive performed activity workouts"
    )
    parser.add_argument(
        "--daily-details",
        action="store_true",
        help="Also fetch all supported detailed daily health endpoints",
    )
    parser.add_argument(
        "--datasets", help="Comma-separated health datasets; default all range-summary datasets"
    )
    parser.add_argument(
        "--refresh", action="store_true", help="Refetch completed history as well as recent data"
    )
    parser.add_argument(
        "--refresh-days", type=int, default=7, help="Refetch recent days to pick up late changes"
    )
    parser.add_argument("--delay", type=float, default=1.0, help="Minimum time between API requests, seconds")
    args = parser.parse_args(argv)
    if (
        (args.activity_limit is not None and args.activity_limit <= 0)
        or args.refresh_days < 0
        or args.delay < 0
        or not math.isfinite(args.delay)
    ):
        parser.error("Limits must be positive; delay/refresh-days cannot be negative")
    if args.start and args.start > args.end:
        parser.error("--start must be on/before --end")
    if args.all_data and args.datasets:
        parser.error("--all-data cannot be combined with a dataset subset")
    if args.datasets:
        from .domain.datasets import DETAIL_DATASETS, RANGE_DATASETS

        unknown = set(args.datasets.split(",")) - RANGE_DATASETS.keys() - DETAIL_DATASETS.keys()
        if unknown:
            parser.error("Unknown datasets: " + ", ".join(sorted(unknown)))
    return args


@contextlib.contextmanager
def database_lock(database, read_only):
    lockpath = database.with_suffix(database.suffix + ".lock")
    if read_only and not lockpath.exists():
        yield
        return
    if not read_only:
        lockpath.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    mode = "r" if read_only else "a"
    with lockpath.open(mode) as lock:
        try:
            fcntl.flock(lock, (fcntl.LOCK_SH if read_only else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another sync/check is using this GeoPackage") from None
        yield


def execute(args, root, store):
    if args.command == "status":
        print(json.dumps(status(store), indent=2))
        return 0
    if args.command == "check":
        report = check_store(store)
        print(json.dumps(report, indent=2))
        return 0 if report["valid"] else 1
    api = Api(args.api_key_file, args.delay)
    api.authenticate()
    service = SyncService(
        GarminSource(api),
        store,
        FileArchiveStore(root),
        OriginalActivityDecoder(),
        progress=lambda message: print(message, flush=True),
    )
    report = service.run(
        SyncOptions(
            end=args.end,
            start=args.start,
            activity_limit=args.activity_limit,
            skip_activities=args.skip_activities,
            skip_health=args.skip_health,
            all_data=args.all_data,
            activity_workouts=args.activity_workouts,
            daily_details=args.daily_details,
            datasets=args.datasets,
            refresh_days=args.refresh_days,
            refresh=args.refresh,
        )
    )
    atomic_write(root / "last-sync.json", dumps({"finished_at": now(), **report}).encode())
    print(json.dumps(report, indent=2), flush=True)
    return 2 if report["sync_errors"] else 0


def run(argv=None):
    os.umask(0o077)
    args = parse_args(argv)
    root = args.root.expanduser().resolve()
    read_only = args.command in ("status", "check")
    database = args.database.expanduser().resolve() if args.database else root / "garmin.gpkg"
    if not read_only:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with database_lock(database, read_only):
        store = GeoPackageRepository(database, read_only=read_only)
        try:
            return execute(args, root, store)
        finally:
            store.close()


def main(argv=None):
    try:
        return run(argv)
    except KeyboardInterrupt:
        print("Interrupted; completed work is saved. Rerun to resume.", file=sys.stderr)
        return 130
    except (RuntimeError, OSError) as error:
        print(f"Sync stopped: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
