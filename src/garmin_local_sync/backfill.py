"""Run a one-off, detached, self-checking historical backfill. No scheduler changes."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path

from .config import DEFAULT_KEY, DEFAULT_ROOT
from .util import atomic_write, now


def worker(root=None, delay=1.0, key_file=DEFAULT_KEY):
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
        report = {"status": "running", "pid": os.getpid(), "started_at": now()}

        def update(**values):
            report.update(values)
            atomic_write(report_path, json.dumps(report, indent=2).encode())

        def run(*args, capture=False):
            return subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "garmin_local_sync",
                    *args,
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
    args = parser.parse_args()
    import math

    if not math.isfinite(args.delay) or args.delay < 0:
        parser.error("--delay must be a finite non-negative number")
    root = args.root.expanduser().resolve()
    report_path = root / "backfill-status.json"
    log_path = root / "backfill.log"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if args.command == "worker":
        return worker(root, args.delay, args.api_key_file)
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
        child = subprocess.Popen(
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
