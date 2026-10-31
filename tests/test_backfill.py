import json
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
