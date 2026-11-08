import sqlite3
from contextlib import closing

import pytest

from holocore.memories import MemoryStore
from holocore.storage import create_backup, preview_restore, storage_status


def test_backup_includes_committed_wal_and_is_independent(tmp_path):
    path = tmp_path / "memories.db"
    MemoryStore(path).create_world("first")
    with closing(sqlite3.connect(path)) as writer:
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("UPDATE worlds SET display_name='Changed in WAL' WHERE id='first'")
        writer.commit()
        report = create_backup(path, tmp_path / "backups")
        assert report["integrity"] == "ok"
        assert report["includes_raw_transcripts"] is False
        with closing(sqlite3.connect(report["backup"])) as copy:
            assert copy.execute("SELECT display_name FROM worlds").fetchone()[0] == "Changed in WAL"
        writer.execute("UPDATE worlds SET display_name='Later' WHERE id='first'")
        writer.commit()
        preview = preview_restore(path, report["backup"])
        assert preview["restore_applied"] is False
        assert writer.execute("SELECT display_name FROM worlds").fetchone()[0] == "Later"
    assert storage_status(path)["journal_mode"] == "wal"
    assert storage_status(path)["total_bytes"] > 0


def test_invalid_backup_is_not_published(tmp_path):
    path = tmp_path / "other.db"
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE unrelated (value)")
    with pytest.raises(ValueError, match="Not a HoloCore"):
        create_backup(path, tmp_path / "backups")
    assert list((tmp_path / "backups").iterdir()) == []


def test_readonly_status_does_not_create_database(tmp_path):
    path = tmp_path / "absent.db"
    assert storage_status(path)["exists"] is False
    assert not path.exists()
    with pytest.raises(FileNotFoundError):
        create_backup(path, tmp_path / "backups")
    assert not (tmp_path / "backups").exists()


def test_current_schema_open_does_not_scan_integrity(tmp_path, monkeypatch):
    path = tmp_path / "memories.db"
    MemoryStore(path)
    def fail_scan(*args):
        raise AssertionError("Unexpected full scan")
    monkeypatch.setattr(MemoryStore, "_assert_integrity", fail_scan)
    MemoryStore(path)


def test_future_schema_restore_is_rejected(tmp_path):
    path = tmp_path / "backup.db"
    MemoryStore(path)
    with closing(sqlite3.connect(path)) as db:
        db.execute("UPDATE schema_info SET version=999")
        db.commit()
    with pytest.raises(ValueError, match="Unsupported"):
        preview_restore(tmp_path / "live.db", path)
