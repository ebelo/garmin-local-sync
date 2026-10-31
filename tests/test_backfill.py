import json
import os
import sys
from types import SimpleNamespace

from garmin_local_sync import backfill


def configure(monkeypatch, tmp_path, results):
    monkeypatch.setattr(backfill, "DEFAULT_ROOT", tmp_path)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return results.pop(0)

    monkeypatch.setattr(backfill.subprocess, "run", run)
    return calls


def result(code=0, state=None):
    return SimpleNamespace(returncode=code, stdout=json.dumps(state or {}))


def test_worker_retries_item_errors_then_checks_completeness(monkeypatch, tmp_path):
    state = {"activities": 3, "activities_imported": 3, "sync_errors": 0}
    calls = configure(monkeypatch, tmp_path, [result(2), result(0), result(0), result(0, state)])
    assert backfill.worker() == 0
    report = json.loads((tmp_path / "backfill-status.json").read_text())
    assert report["status"] == "complete"
    assert report["first_sync_exit_code"] == 2
    assert len(calls) == 4
    assert calls[2][3] == "check"


def test_worker_does_not_call_partial_import_complete(monkeypatch, tmp_path):
    state = {"activities": 3, "activities_imported": 2, "sync_errors": 0}
    configure(monkeypatch, tmp_path, [result(0), result(0), result(0, state)])
    assert backfill.worker() == 2
    assert json.loads((tmp_path / "backfill-status.json").read_text())["status"] == "incomplete"


def test_worker_reports_process_failure_without_exception_text(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path, [])

    def fail(*args, **kwargs):
        raise RuntimeError("do not echo arbitrary exception content")

    monkeypatch.setattr(backfill.subprocess, "run", fail)
    assert backfill.worker() == 1
    report = json.loads((tmp_path / "backfill-status.json").read_text())
    assert report["status"] == "failed"
    assert report["error_type"] == "RuntimeError"
    assert "do not echo" not in (tmp_path / "backfill-status.json").read_text()


def test_status_marks_a_reused_non_worker_pid_interrupted(tmp_path, capsys):
    path = tmp_path / "backfill-status.json"
    path.write_text(json.dumps({"status": "running", "pid": os.getpid()}))
    backfill.print_status(path)
    assert json.loads(capsys.readouterr().out)["status"] == "interrupted"
    assert json.loads(path.read_text())["status"] == "running"


def test_start_dispatch_preserves_all_data_refresh_and_detached_options(tmp_path, monkeypatch):
    calls = []

    def launch(command, **options):
        calls.append((command, options))
        return SimpleNamespace(pid=1234)

    monkeypatch.setattr(backfill.subprocess, "Popen", launch)
    monkeypatch.setattr(
        sys, "argv", ["backfill", "start", "--root", str(tmp_path), "--all-data", "--refresh-days", "0"]
    )
    assert backfill.main() == 0
    command, options = calls[0]
    assert "--all-data" in command
    assert command[command.index("--refresh-days") + 1] == "0"
    assert options["start_new_session"] is True
    assert options["close_fds"] is True


def test_main_status_without_report_is_readable(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["backfill", "status", "--root", str(tmp_path)])
    assert backfill.main() == 0
    assert "No detached backfill" in capsys.readouterr().out
