import datetime as dt
import hashlib
import sqlite3

import pytest

from garmin_local_sync.cli import main
from garmin_local_sync.example import create_example
from garmin_local_sync.formats import sample_row
from garmin_local_sync.storage import Store
from garmin_local_sync.sync import refresh_cutoff


def test_refresh_window_is_exactly_n_calendar_days_and_zero_disables_it():
    assert refresh_cutoff(7, dt.date(2024, 3, 1)) == "2024-02-24"
    assert refresh_cutoff(1, dt.date(2024, 3, 1)) == "2024-03-01"
    assert refresh_cutoff(0, dt.date(2024, 3, 1)) is None


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_read_only_commands_do_not_create_missing_database_or_directories(tmp_path):
    root = tmp_path / "missing" / "data"
    assert main(["status", "--root", str(root)]) == 1
    assert main(["check", "--root", str(root)]) == 1
    assert not root.exists()


def test_inspection_does_not_modify_bytes_permissions_or_create_a_lock(tmp_path):
    path = tmp_path / "example.gpkg"
    create_example(path)
    before, mode = digest(path), path.stat().st_mode
    assert main(["status", "--database", str(path)]) == 0
    assert main(["check", "--database", str(path)]) == 0
    assert digest(path) == before and path.stat().st_mode == mode
    assert not path.with_suffix(".gpkg.lock").exists()


def test_foreign_sqlite_file_is_never_adopted_or_modified(tmp_path):
    path = tmp_path / "foreign.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE unrelated(value TEXT)")
    before = digest(path)
    with pytest.raises(RuntimeError, match="Not a GeoPackage"):
        Store(path)
    assert digest(path) == before


def test_unknown_schema_is_rejected_without_rewriting_database(tmp_path):
    path = tmp_path / "future.gpkg"
    create_example(path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE schema_version SET version=99")
    before = digest(path)
    with pytest.raises(RuntimeError, match="Unsupported"):
        Store(path)
    assert digest(path) == before


def test_legacy_schema_is_migrated_only_on_a_write_open(tmp_path):
    path = tmp_path / "legacy.gpkg"
    create_example(path)
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE activity_samples DROP COLUMN segment_index")
        db.execute("UPDATE schema_version SET version=1")
    before = digest(path)
    store = Store(path, read_only=True)
    assert store.db.execute("SELECT version FROM schema_version").fetchone()[0] == 1
    store.close()
    assert digest(path) == before
    store = Store(path)
    assert store.db.execute("SELECT version FROM schema_version").fetchone()[0] == 2
    assert store.db.execute("SELECT count(*) FROM activity_samples").fetchone()[0] == 8
    assert store.db.execute("SELECT DISTINCT segment_index FROM activity_samples").fetchall() == [(0,)]
    store.close()


def test_refresh_failure_invalidates_the_old_completion_checkpoint(tmp_path):
    path = tmp_path / "example.gpkg"
    create_example(path)
    store = Store(path)
    row = sample_row(1001, 0, {"longitude": 30, "latitude": 40})
    with pytest.raises(sqlite3.IntegrityError):
        store.save_activity(1001, {}, [row, row], [], "synthetic")
    assert (
        store.db.execute("SELECT samples_synced_at FROM activities WHERE activity_id=1001").fetchone()[0]
        is None
    )
    assert store.db.execute("SELECT count(*) FROM activity_samples WHERE activity_id=1001").fetchone()[0] == 4
    store.save_activity(1001, {}, [row], [], "synthetic")
    assert store.db.execute("SELECT samples_synced_at FROM activities WHERE activity_id=1001").fetchone()[0]
    store.close()


def test_invalid_dataset_is_rejected_before_any_database_write(tmp_path):
    root = tmp_path / "data"
    with pytest.raises(SystemExit) as exc:
        main(["sync", "--root", str(root), "--datasets", "not-a-dataset"])
    assert exc.value.code == 2 and not root.exists()


def test_console_error_is_concise_and_does_not_reveal_a_credential(tmp_path, capsys):
    root = tmp_path / "data"
    key = tmp_path / "missing-key"
    assert main(["sync", "--root", str(root), "--api-key-file", str(key)]) == 1
    captured = capsys.readouterr()
    assert "Sync stopped:" in captured.err and "Traceback" not in captured.err


def test_no_key_or_token_is_written_into_an_example_database(tmp_path):
    path = tmp_path / "example.gpkg"
    create_example(path)
    with sqlite3.connect(path) as db:
        names = [x[0] for x in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    assert not any("token" in x or "credential" in x for x in names)
