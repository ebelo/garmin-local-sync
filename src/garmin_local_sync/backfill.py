"""Run a one-off, detached, self-checking historical backfill. No scheduler changes."""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os

# executes this package using the current interpreter, never a shell.
import subprocess  # nosec B404
import sys
from pathlib import Path

from . import __version__
from .config import DEFAULT_KEY, DEFAULT_ROOT
from .util import atomic_write, now


def worker(root=None, delay=1.0, key_file=DEFAULT_KEY, all_data=False, refresh_days=7):
    root = Path(root or DEFAULT_ROOT).expanduser().resolve()
    report_path = root / "backfill-status.json"
    os.umask(0o077)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (root / "backfill.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("A historical backfill is already running", flush=True)
            return 1
        report = {
            "status": "running",
            "pid": os.getpid(),
            "started_at": now(),
            "all_data": all_data,
            "refresh_days": refresh_days,
            "end_date": dt.date.today().isoformat(),
            "package_version": __version__,
            "interpreter": sys.executable,
        }

        def update(**values):
            report.update(values)
            atomic_write(report_path, json.dumps(report, indent=2).encode())

        def run(*args, capture=False):
            sync_options = []
            if args[0] == "sync":
                sync_options = ["--refresh-days", str(refresh_days), "--end", report["end_date"]]
                if all_data:
                    sync_options.append("--all-data")
            # fixed interpreter/module; options passed as argv, shell=False.
            return subprocess.run(  # nosec B603
                [
                    sys.executable,
                    "-m",
                    "garmin_local_sync",
                    *args,
                    *sync_options,
                    "--root",
                    str(root),
                    "--api-key-file",
                    str(key_file),
                    "--delay",
                    str(delay),
                ],
                text=True,
                capture_output=capture,
            )

        try:
            update(phase="historical_sync")
            sync = run("sync")
            if sync.returncode == 2:
                update(phase="retry_incomplete_items", first_sync_exit_code=2)
                sync = run("sync")
            update(phase="integrity_check", sync_exit_code=sync.returncode)
            check = run("check")
            state = run("status", capture=True)
            summary = json.loads(state.stdout) if state.returncode == 0 else {}
            complete = (
                sync.returncode == 0
                and check.returncode == 0
                and state.returncode == 0
                and summary.get("activities_imported") == summary.get("activities")
                and summary.get("sync_errors") == 0
            )
            update(
                status="complete" if complete else "incomplete",
                phase="finished",
                finished_at=now(),
                check_exit_code=check.returncode,
                summary=summary,
            )
            print(json.dumps(report, indent=2), flush=True)
            return 0 if complete else 2
        except Exception as error:
            # Never include request headers, credential values, or response bodies.
            update(status="failed", phase="stopped", finished_at=now(), error_type=type(error).__name__)
            print(f"Backfill worker stopped: {type(error).__name__}; see the private log", flush=True)
            return 1


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["start", "worker", "status"])
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--api-key-file", type=Path, default=DEFAULT_KEY)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--all-data", action="store_true", help="Include every supported read endpoint")
    parser.add_argument("--refresh-days", type=int, default=7)
    args = parser.parse_args()
    import math

    if not math.isfinite(args.delay) or args.delay < 0:
        parser.error("--delay must be a finite non-negative number")
    if args.refresh_days < 0:
        parser.error("--refresh-days cannot be negative")
    root = args.root.expanduser().resolve()
    report_path = root / "backfill-status.json"
    log_path = root / "backfill.log"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if args.command == "worker":
        return worker(root, args.delay, args.api_key_file, args.all_data, args.refresh_days)
    if args.command == "status":
        if report_path.exists():
            report = json.loads(report_path.read_text())
            if report.get("status") == "running":
                try:
                    os.kill(report["pid"], 0)
                    command = Path(f"/proc/{report['pid']}/cmdline").read_bytes()
                    if b"garmin_local_sync.backfill" not in command:
                        report["status"] = "interrupted"
                except (ProcessLookupError, FileNotFoundError):
                    report["status"] = "interrupted"
            print(json.dumps(report, indent=2))
        else:
            print("No detached backfill has been started")
        return 0
    # The child owns the exclusive lock for its entire lifetime. Check before
    # spawning too, so repeated start commands don't create redundant log writers.
    with (root / "backfill.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("A historical backfill is already running")
            return 0
    with log_path.open("a") as log:
        os.chmod(log_path, 0o600)
        # fixed interpreter/module; options passed as argv, shell=False.
        child = subprocess.Popen(  # nosec B603
            [
                sys.executable,
                "-m",
                "garmin_local_sync.backfill",
                "worker",
                "--root",
                str(root),
                "--delay",
                str(args.delay),
                "--api-key-file",
                str(args.api_key_file.expanduser().resolve()),
                "--refresh-days",
                str(args.refresh_days),
                *(["--all-data"] if args.all_data else []),
            ],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    print(f"Detached backfill started (PID {child.pid}). Log: {log_path}; report: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
