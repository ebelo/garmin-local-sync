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

from .api import Api
from .config import DEFAULT_KEY, DEFAULT_ROOT
from .storage import Store, status
from .sync import sync_activities, sync_activity_list, sync_wellness
from .util import atomic_write, dumps, now
from .validation import check_store


def run(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["sync", "status", "check"], nargs="?", default="sync")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="Private archive/data directory")
    parser.add_argument("--database", type=Path, help="GeoPackage path; default ROOT/garmin.gpkg")
    parser.add_argument("--api-key-file", type=Path, default=DEFAULT_KEY)
    parser.add_argument(
        "--start", type=dt.date.fromisoformat, help="First health date; default earliest indexed activity"
    )
    parser.add_argument("--end", type=dt.date.fromisoformat, default=dt.date.today())
    parser.add_argument("--activity-limit", type=int, help="Import only the latest N activities (smoke test)")
    parser.add_argument("--skip-activities", action="store_true")
    parser.add_argument("--skip-health", action="store_true")
    parser.add_argument(
        "--daily-details",
        action="store_true",
        help="Also fetch daily HR/sleep/HRV/SpO2/nutrition/fitness-age detail",
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
    if args.datasets:
        from .datasets import DETAIL_DATASETS, RANGE_DATASETS

        unknown = set(args.datasets.split(",")) - RANGE_DATASETS.keys() - DETAIL_DATASETS.keys()
        if unknown:
            parser.error("Unknown datasets: " + ", ".join(sorted(unknown)))
    root = args.root.expanduser().resolve()
    read_only = args.command in ("status", "check")
    database = args.database.expanduser().resolve() if args.database else root / "garmin.gpkg"
    lockpath = database.with_suffix(database.suffix + ".lock")
    if not read_only:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        lockpath.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_context = (
        lockpath.open("r" if read_only else "a")
        if lockpath.exists() or not read_only
        else contextlib.nullcontext(None)
    )
    with lock_context as lock:
        if lock is not None:
            try:
                fcntl.flock(lock, (fcntl.LOCK_SH if read_only else fcntl.LOCK_EX) | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Another sync/check is using this GeoPackage") from None
        store = Store(database, read_only=read_only)
        try:
            if args.command == "status":
                print(json.dumps(status(store), indent=2))
                return 0
            if args.command == "check":
                report = check_store(store)
                print(json.dumps(report, indent=2))
                return 0 if report["valid"] else 1
            api = Api(args.api_key_file, args.delay)
            api.authenticate()
            if not args.skip_activities:
                sync_activity_list(api, store, args.activity_limit)
            if not args.skip_health:
                earliest = store.db.execute("SELECT min(start_time_local) FROM activities").fetchone()[0]
                start = args.start or (
                    dt.date.fromisoformat(earliest[:10]) if earliest else args.end - dt.timedelta(days=90)
                )
                print(f"Health date range: {start} through {args.end}", flush=True)
                for name, path in [
                    ("profile", "/userprofile-service/userprofile/settings"),
                    ("heart_rate_zones", "/biometric-service/heartRateZones/v2"),
                ]:
                    data = api.get(path)
                    store.response(name, "current", data)
                sync_wellness(
                    api,
                    store,
                    start,
                    args.end,
                    args.refresh_days,
                    args.refresh,
                    args.daily_details,
                    args.datasets,
                    discover_start=args.start is None,
                )
            if not args.skip_activities:
                sync_activities(api, store, root, args.refresh_days, args.activity_limit, args.refresh)
            report = status(store)
            atomic_write(root / "last-sync.json", dumps({"finished_at": now(), **report}).encode())
            print(json.dumps(report, indent=2), flush=True)
            return 2 if report["sync_errors"] else 0
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
