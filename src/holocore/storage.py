"""Read-only storage diagnostics and consistent SQLite snapshot backups."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from holocore.memories import SCHEMA_VERSION


def _read_connection(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(f"Memories database not found: {path}")
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)


def _validate(db: sqlite3.Connection) -> int:
    results = [row[0] for row in db.execute("PRAGMA quick_check")]
    if results != ["ok"]:
        raise ValueError("Database integrity check failed: " + "; ".join(results))
    try:
        rows = db.execute("SELECT version FROM schema_info").fetchall()
    except sqlite3.DatabaseError as exc:
        raise ValueError("Not a HoloCore Memories database") from exc
    if len(rows) != 1 or not 1 <= int(rows[0][0]) <= SCHEMA_VERSION:
        raise ValueError("Unsupported Memories schema version")
    required = {"worlds", "sectors", "shards", "provenance", "checkpoints"}
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not required <= tables:
        raise ValueError("Memories database is missing required tables")
    if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("Database contains broken references")
    return int(rows[0][0])


def storage_status(database: str | Path) -> dict[str, Any]:
    """Return cheap page/file diagnostics without scanning chat content."""
    path = Path(database).resolve()
    if not path.is_file():
        return {"exists": False, "database": str(path)}
    with closing(_read_connection(path)) as db:
        page_size = int(db.execute("PRAGMA page_size").fetchone()[0])
        pages = int(db.execute("PRAGMA page_count").fetchone()[0])
        free_pages = int(db.execute("PRAGMA freelist_count").fetchone()[0])
        journal = db.execute("PRAGMA journal_mode").fetchone()[0]
    sizes = {}
    for label, file in (("database_bytes", path), ("wal_bytes", Path(str(path) + "-wal")),
                        ("shared_memory_bytes", Path(str(path) + "-shm"))):
        try:
            sizes[label] = file.stat().st_size
        except FileNotFoundError:
            sizes[label] = 0
    return {
        "exists": True, "database": str(path), "journal_mode": journal,
        "page_size": page_size, "page_count": pages, "free_pages": free_pages,
        "reusable_bytes": free_pages * page_size, "logical_bytes": pages * page_size,
        "total_bytes": sum(sizes.values()), "integrity_checked": False, **sizes,
    }


def create_backup(database: str | Path, backup_dir: str | Path) -> dict[str, Any]:
    """Snapshot committed WAL data, validate it, and atomically publish a new file."""
    source = Path(database).resolve()
    directory = Path(backup_dir).resolve()
    # Verify the source before creating a directory or an empty destination.
    with closing(_read_connection(source)) as db:
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        target = directory / f"memories-{stamp}-{uuid.uuid4().hex}.sqlite3"
        handle, temporary_name = tempfile.mkstemp(prefix=".holocore-backup-", suffix=".tmp", dir=directory)
        os.close(handle)
        temporary = Path(temporary_name)
        deadline = time.monotonic() + 120

        def progress(_status: int, _remaining: int, _total: int) -> None:
            if time.monotonic() > deadline:
                raise TimeoutError("Backup exceeded two minutes; retry when capture is less busy")

        try:
            with closing(sqlite3.connect(temporary)) as copy:
                db.backup(copy, pages=256, progress=progress, sleep=0.05)
                version = _validate(copy)
                copy.execute("PRAGMA journal_mode=DELETE")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return {"backup": str(target), "database": str(source), "schema_version": version,
            "bytes": target.stat().st_size, "integrity": "ok", "scope": "memory_database",
            "includes_raw_transcripts": False}


def preview_restore(database: str | Path, backup: str | Path) -> dict[str, Any]:
    """Validate a recovery candidate without changing live data or opening writers."""
    target, source = Path(database).resolve(), Path(backup).resolve()
    if target == source:
        raise ValueError("Select a backup separate from the live database")
    with closing(_read_connection(source)) as db:
        version = _validate(db)
    return {"database": str(target), "backup": str(source), "schema_version": version,
            "integrity": "ok", "bytes": source.stat().st_size, "restore_applied": False,
            "requires_stopped_writers": True, "scope": "memory_database",
            "includes_raw_transcripts": False}
