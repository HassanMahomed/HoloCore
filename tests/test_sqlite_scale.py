"""Measured Memories database behaviour at small, medium, and large sizes.

Opt-in because the large size takes a while: set HOLOCORE_SCALE_TEST=1 and run with -s
to print the measurements. All memories go into one project, the worst case for search.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import sqlite3
import statistics
import time
from contextlib import closing

import pytest

from holocore.board import build_board
from holocore.folder_layout import HomeLayout
from holocore.home import HomeManager
from holocore.memories import MemoryStore
from holocore.storage import create_backup

pytestmark = pytest.mark.skipif(os.environ.get("HOLOCORE_SCALE_TEST") != "1",
                                reason="set HOLOCORE_SCALE_TEST=1 to run the scale measurements")

SIZES = {"small": 1_000, "medium": 20_000, "large": 200_000}
MEMORIES_PER_SESSION = 100
_WORDS = [f"word{index}" for index in range(2_000)]


def _populate(database, world: str, count: int) -> None:
    """Bulk-insert memories shaped like real ones (about 130 bytes, one AI session per 100)."""
    rng = random.Random(count)
    now = "2026-11-01T00:00:00+00:00"
    shards, provenance, runs = [], [], []
    for index in range(count):
        content = f"memory {index} " + " ".join(rng.choices(_WORDS, k=14))
        digest = hashlib.sha256(content.encode()).hexdigest()
        shard = digest[:32]
        session = index // MEMORIES_PER_SESSION
        shards.append((shard, world, None, content, digest, "distilled-memory", "[]", "{}",
                       json.dumps({"memory_kind": "fact"}), now, now))
        provenance.append((shard, world, f"codex:C:/chats/{session}.jsonl#distilled/fact/{index}", 0, None, "{}", now, now))
        if index % MEMORIES_PER_SESSION == 0:
            runs.append((f"run-{session}", world, f"s{session}", "codex", "main", "gpt", None, "completed",
                         now, now, now, json.dumps({"transcript_path": f"C:/chats/{session}.jsonl"})))
    with closing(sqlite3.connect(database)) as db, db:
        db.executemany("INSERT INTO shards VALUES(?,?,?,?,?,?,?,?,?,?,?)", shards)
        db.executemany("INSERT INTO provenance(shard_id,world_id,source_ref,chunk_index,version_token,metadata,"
                       "first_seen_at,last_seen_at) VALUES(?,?,?,?,?,?,?,?)", provenance)
        db.executemany("INSERT INTO agent_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", runs)


def _timed(action):
    started = time.perf_counter()
    result = action()
    return time.perf_counter() - started, result


@pytest.mark.parametrize("size", list(SIZES))
def test_memories_database_at_scale(size, tmp_path, monkeypatch):
    from holocore.activity import ActivityLedger
    monkeypatch.setenv("HOLOCORE_CONFIG_HOME", str(tmp_path / "pointer"))
    root = tmp_path / "project"
    root.mkdir()
    manager = HomeManager(tmp_path / "home")
    manager.register_world(root, import_archive=False)
    world = manager.list_worlds()["worlds"][0]["id"]
    database = HomeLayout(manager.home).memories / "animus.db"
    store = MemoryStore(database)
    store.create_world(world)
    ActivityLedger(database).close()
    _populate(database, world, SIZES[size])

    writes = [_timed(lambda i=i: store.ingest(f"new memory {i} about word7 and word42", world=world,
                                              source_ref=f"new#{i}"))[0] for i in range(200)]
    searches = [_timed(lambda q=q: store.search(q, world=world))[0] for q in ("word7", "word42 word99", "memory 5")]
    board_seconds, board = _timed(lambda: build_board(manager.home, days=0, limit=30))
    store.close()
    with closing(sqlite3.connect(database)) as db:
        integrity_seconds, integrity = _timed(lambda: db.execute("PRAGMA integrity_check").fetchone()[0])
    backup_seconds, backup = _timed(lambda: create_backup(database, tmp_path / "backups"))

    assert integrity == "ok" and backup["integrity"] == "ok"
    assert len(board["sessions"]) == min(30, SIZES[size] // MEMORIES_PER_SESSION) and board["memories"]
    result = {"size": size, "memories": SIZES[size] + 200,
              "database_mb": round(database.stat().st_size / 1e6, 1),
              "save_ms_median": round(statistics.median(writes) * 1000, 2),
              "search_s_median": round(statistics.median(searches), 3),
              "board_s": round(board_seconds, 3), "integrity_check_s": round(integrity_seconds, 3),
              "backup_s": round(backup_seconds, 3), "backup_mb": round(backup["bytes"] / 1e6, 1)}
    print("\nSCALE " + json.dumps(result))
