from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from holocore.sqlite_connections import DatabaseBusyError, ThreadConnections


def _connections(tmp_path):
    path = tmp_path / "test.db"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA journal_mode = WAL")
        db.execute("CREATE TABLE t(v TEXT)")
    return ThreadConnections(lambda: sqlite3.connect(path, timeout=30)), path


def _values(path):
    with sqlite3.connect(path) as db:
        return sorted(row[0] for row in db.execute("SELECT v FROM t"))


def test_one_thread_reuses_its_connection_and_commits_each_transaction(tmp_path):
    connections, path = _connections(tmp_path)
    with connections.transaction() as first:
        first.execute("INSERT INTO t VALUES ('a')")
    assert _values(path) == ["a"]  # Committed when the block ends, not when the connection closes.
    with connections.transaction() as second:
        assert second is first
    connections.close()


def test_nested_use_cannot_commit_the_outer_transaction(tmp_path):
    connections, path = _connections(tmp_path)
    with pytest.raises(RuntimeError):
        with connections.transaction() as outer:
            outer.execute("INSERT INTO t VALUES ('outer')")
            with connections.transaction() as inner:
                assert inner is not outer
                assert inner.execute("SELECT count(*) FROM t").fetchone()[0] == 0  # Outer work is not visible yet.
            raise RuntimeError("fail the outer transaction")
    assert _values(path) == []
    connections.close()


def test_a_failed_connection_is_replaced(tmp_path):
    connections, path = _connections(tmp_path)
    with pytest.raises(sqlite3.OperationalError):
        with connections.transaction() as failed:
            failed.execute("INSERT INTO t VALUES ('rolled back')")
            failed.execute("SELECT * FROM missing_table")
    with pytest.raises(sqlite3.ProgrammingError):
        failed.execute("SELECT 1")  # Closed, not reused.
    with connections.transaction() as fresh:
        fresh.execute("INSERT INTO t VALUES ('b')")
    assert _values(path) == ["b"]
    connections.close()


def test_a_held_write_lock_gives_a_clear_error_after_the_busy_timeout(tmp_path):
    path = tmp_path / "test.db"
    _connections(tmp_path)
    connections = ThreadConnections(lambda: sqlite3.connect(path, timeout=0.2), path)
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        started = time.perf_counter()
        with pytest.raises(DatabaseBusyError) as raised:
            with connections.transaction() as db:
                db.execute("INSERT INTO t VALUES ('blocked')")
        assert 0.15 < time.perf_counter() - started < 5  # Waited the busy timeout, then stopped.
        assert str(path) in str(raised.value) and "try again" in str(raised.value)
        assert isinstance(raised.value, sqlite3.OperationalError)  # Existing handlers still catch it.
    finally:
        holder.rollback()
        holder.close()
    with connections.transaction() as db:  # Works again once the lock is released.
        db.execute("INSERT INTO t VALUES ('after')")
    assert _values(path) == ["after"]
    connections.close()


def test_each_thread_has_its_own_connection(tmp_path):
    connections, _ = _connections(tmp_path)
    seen = []

    def work():
        with connections.transaction() as db:
            seen.append(db)
        connections.close()

    threads = [threading.Thread(target=work) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len({id(db) for db in seen}) == 3


def test_checkpoint_reports_when_a_reader_blocks_it_and_completes_later(tmp_path):
    from holocore.memories import MemoryStore
    store = MemoryStore(tmp_path / "animus.db")
    store.create_world("w")
    store.ingest("first memory", world="w", source_ref="a#0")
    reader = sqlite3.connect(tmp_path / "animus.db")
    reader.execute("BEGIN")
    reader.execute("SELECT count(*) FROM shards").fetchone()  # Holds a snapshot of the current WAL.
    try:
        store.ingest("second memory", world="w", source_ref="a#1")
        blocked = store.checkpoint_wal()
        assert blocked["completed"] is False and blocked["checkpointed_frames"] < blocked["wal_frames"]
    finally:
        reader.rollback()
        reader.close()
    done = store.checkpoint_wal()
    assert done["completed"] is True and done["checkpointed_frames"] == done["wal_frames"]
    assert len(store.search("memory", world="w")) == 2  # Nothing was lost while it was blocked.
    store.close()


class _CommitFails(sqlite3.Connection):
    """A connection whose seventh commit fails, as if the process were stopped mid-merge."""
    commits = 0

    def commit(self):
        _CommitFails.commits += 1
        if _CommitFails.commits == 7:
            raise sqlite3.OperationalError("simulated interruption")
        super().commit()


def _contents(path):
    with sqlite3.connect(path) as db:
        return {table: sorted(db.execute(f"SELECT * FROM {table}").fetchall())
                for table in ("worlds", "shards", "provenance", "diary", "diary_provenance")}


def test_an_interrupted_merge_finishes_when_run_again(tmp_path, monkeypatch):
    from holocore import memories as memories_module
    from holocore.memories import MemoryStore
    legacy = MemoryStore(tmp_path / "legacy.db")
    legacy.create_world("old")
    for index in range(450):
        legacy.ingest(f"legacy memory {index}", world="old", source_ref=f"chat#{index}")
    legacy.record_diary("A legacy conversation.", world="old", title="Chat", source_ref="chat")
    legacy.close()

    reference = MemoryStore(tmp_path / "reference.db")
    expected = reference.merge_database(tmp_path / "legacy.db", world="new")
    reference.close()

    monkeypatch.setattr(memories_module, "MERGE_BATCH", 100)
    monkeypatch.setattr(_CommitFails, "commits", 0)
    target = MemoryStore(tmp_path / "target.db")
    target._connections = memories_module.ThreadConnections(
        lambda: _row_connection(tmp_path / "target.db", _CommitFails), target.path)
    with pytest.raises(sqlite3.OperationalError, match="simulated interruption"):
        target.merge_database(tmp_path / "legacy.db", world="new")
    partial = _contents(tmp_path / "target.db")
    assert len(partial["shards"]) == expected["shards"]  # Earlier batches stayed committed...
    assert 0 < len(partial["provenance"]) < expected["provenance"]  # ...and it stopped partway.

    target._connections = memories_module.ThreadConnections(target._connect, target.path)
    resumed = target.merge_database(tmp_path / "legacy.db", world="new")
    target.close()
    assert _contents(tmp_path / "target.db") == _contents(tmp_path / "reference.db")
    assert resumed["provenance"] == expected["provenance"] - len(partial["provenance"])


def _row_connection(path, factory):
    db = sqlite3.connect(path, timeout=30, factory=factory)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


_WRITER = """
import sys
from holocore.memories import MemoryStore
store = MemoryStore(sys.argv[1])
store.create_world("w")
index = 0
while True:
    store.ingest(f"memory number {index} " + "x" * 2000, world="w", source_ref=f"chat#{index}")
    print(index, flush=True)  # Printed only after the memory's transaction committed.
    index += 1
"""


def test_a_killed_writer_loses_no_committed_memory_and_leaves_a_valid_database(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    from holocore.memories import MemoryStore
    database = tmp_path / "animus.db"
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    writer = subprocess.Popen([sys.executable, "-c", _WRITER, str(database)], env=env,
                              stdout=subprocess.PIPE, text=True)
    committed = []
    try:
        while len(committed) < 200:
            line = writer.stdout.readline()
            assert line, "the writer stopped before it was killed"
            committed.append(int(line))
    finally:
        writer.kill()  # No clean shutdown: WAL left as it was, mid-write.
        writer.wait(timeout=30)
        writer.stdout.close()
    with sqlite3.connect(database) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        refs = {row[0] for row in db.execute("SELECT source_ref FROM provenance")}
        orphans = db.execute("SELECT count(*) FROM shards s WHERE NOT EXISTS "
                             "(SELECT 1 FROM provenance p WHERE p.shard_id=s.id)").fetchone()[0]
    assert {f"chat#{index}" for index in committed} <= refs  # Every reported memory survived.
    assert orphans == 0  # A memory and its source link are written together or not at all.
    store = MemoryStore(database)  # Opens normally and keeps working.
    store.ingest("after the crash", world="w", source_ref="after#0")
    store.close()


def test_raw_chat_audits_stay_byte_identical_through_rebuilds(tmp_path):
    from holocore.memories import MemoryStore
    from holocore.memory_pipeline import MemoryRefinementPipeline
    store = MemoryStore(tmp_path / "animus.db")
    store.create_world("w")
    pipeline = MemoryRefinementPipeline(store, tmp_path / "ChatHistory")
    chat = [{"role": "user", "content": "We decided to keep backups weekly."},
            {"role": "assistant", "content": "Noted: weekly backups, keep four."}]
    audit = pipeline.refine(chat, world="w", source_ref="codex:chat.jsonl").audit_path
    original = audit.read_bytes()
    pipeline.refine(chat, world="w", source_ref="codex:chat.jsonl")  # Same chat processed again.
    store.consolidate(world="w")  # Derived memories rebuilt.
    assert audit.read_bytes() == original
    assert sorted(path.name for path in (tmp_path / "ChatHistory").rglob("*") if path.is_file()) == [audit.name]
    store.close()
