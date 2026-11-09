from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from holocore.activity import ActivityLedger
from holocore.board import build_board
from holocore.folder_layout import HomeLayout
from holocore.home import HomeManager
from holocore.memories import MemoryStore


def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOLOCORE_CONFIG_HOME", str(tmp_path / "pointer"))
    root = tmp_path / "project"
    root.mkdir()
    manager = HomeManager(tmp_path / "home")
    manager.register_world(root, import_archive=False)
    world = manager.list_worlds()["worlds"][0]["id"]
    database = HomeLayout(manager.home).memories / "animus.db"
    return manager, root, world, database


def _session(ledger, world, root, session, transcript, *, client="claude", files=()):
    base = {"session_id": session, "cwd": str(root), "transcript_path": str(transcript)}
    ledger.record_hook({**base, "hook_event_name": "SessionStart"}, client=client, project_id=world)
    for name in files:
        ledger.record_hook({**base, "hook_event_name": "PostToolUse", "tool_name": "Edit",
                            "tool_input": {"file_path": str(root / name)}}, client=client, project_id=world)


def _memory(db, world, shard, text, source, created="2026-11-08T10:00:00+00:00", kind="fact"):
    db.execute("INSERT INTO shards VALUES(?,?,?,?,?,?,?,?,?,?,?)",
               (shard, world, "conversations", text, shard, "distilled-memory", "[]", "{}",
                json.dumps({"memory_kind": kind}), created, created))
    db.execute("INSERT INTO provenance(shard_id, world_id, source_ref, chunk_index, metadata, first_seen_at, last_seen_at) "
               "VALUES(?,?,?,?,?,?,?)", (shard, world, f"{source}#distilled/entity/{shard}", 0, "{}", created, created))


def test_board_links_projects_sessions_memories_and_files(tmp_path, monkeypatch):
    manager, root, world, database = _home(tmp_path, monkeypatch)
    transcript = tmp_path / "chat-1.jsonl"
    ledger = ActivityLedger(database)
    _session(ledger, world, root, "s1", transcript, files=("a.py", "b.py"))
    ledger.record_hook({"hook_event_name": "SubagentStop", "session_id": "s1", "agent_id": "x"}, client="claude", project_id=world)
    store = MemoryStore(database)
    store.create_world(world)
    source = f"claude:{transcript}"
    with closing(sqlite3.connect(database)) as db:
        _memory(db, world, "m1", "Chose snapshot backups for the Home", source)
        _memory(db, world, "m2", "MB", source)  # Noise: hidden unless asked for.
        _memory(db, world, "m3", "Project memory not from any session shown", "codex:elsewhere.jsonl")
        db.execute("INSERT INTO diary(id, world_id, sector_id, occurred_at, title, content, kind, metadata, created_at) "
                   "VALUES('d1', ?, '', '2026-11-08T09:00:00+00:00', 'Conversation', ?, 'conversation', '{}', 'x')",
                   (world, "# AGENTS.md instructions for the project"))
        db.execute("INSERT INTO diary(id, world_id, sector_id, occurred_at, title, content, kind, metadata, created_at) "
                   "VALUES('d2', ?, '', '2026-11-08T09:01:00+00:00', 'Conversation', ?, 'conversation', '{}', 'x')",
                   (world, "Add a flow board <system-reminder>injected</system-reminder>\n\nTranscript:\n..."))
        for diary in ("d1", "d2"):
            db.execute("INSERT INTO diary_provenance(diary_id, source_ref, chunk_index, metadata, first_seen_at, last_seen_at) "
                       "VALUES(?,?,0,'{}','x','x')", (diary, source))
        db.commit()
    before = database.read_bytes()

    board = build_board(manager.home, days=None)
    assert database.read_bytes() == before  # Read-only.
    session = board["sessions"][0]
    assert len(board["sessions"]) == 1  # The subagent run is counted, not shown.
    assert session["subagents"] == 1 and session["memories"] == 2
    assert session["title"] == "Add a flow board"
    assert [item["text"] for item in board["memories"]] == ["Chose snapshot backups for the Home",
                                                          "Project memory not from any session shown"]
    assert sorted(item["label"] for item in board["files"]) == ["a.py", "b.py"]
    links = {(link["source"], link["target"]) for link in board["links"]}
    project = f"project:{world}"
    assert (project, session["id"]) in links
    assert (session["id"], "memory:m1") in links and (project, "memory:m3") in links
    assert all(source == session["id"] for source, target in links if target.startswith("file:"))
    assert board["projects"][0]["name"] == world and board["projects"][0]["sessions"] == 1
    shown = build_board(manager.home, days=None, hide_noise=False)
    assert "memory:m2" in {item["id"] for item in shown["memories"]}


def test_board_hides_unregistered_folders_and_respects_the_window(tmp_path, monkeypatch):
    manager, root, world, database = _home(tmp_path, monkeypatch)
    ledger = ActivityLedger(database)
    _session(ledger, world, root, "recent", tmp_path / "r.jsonl")
    elsewhere = tmp_path / "client-bin"
    elsewhere.mkdir()
    ledger.record_hook({"hook_event_name": "Stop", "session_id": "x", "cwd": str(elsewhere)}, client="codex",
                       project_id="22cfb1d302613eae")
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    _session(ledger, world, root, "old", tmp_path / "o.jsonl")
    with closing(sqlite3.connect(database)) as db:
        db.execute("UPDATE agent_runs SET updated_at=? WHERE session_id='old'", (old,))
        db.commit()

    board = build_board(manager.home, days=7)
    assert len(board["sessions"]) == 1 and board["unregistered_sessions"] == 1
    assert [item["project"] for item in board["projects"]] == [world]
    assert len(build_board(manager.home, days=None)["sessions"]) == 2
    assert build_board(manager.home, project="someone-else", days=None)["sessions"] == []
    assert len(build_board(manager.home, days=None, limit=1)["sessions"]) == 1


def test_board_without_a_database_is_empty_and_creates_nothing(tmp_path, monkeypatch):
    manager, _root, _world, database = _home(tmp_path, monkeypatch)
    board = build_board(manager.home)
    assert board["database_found"] is False
    assert board["sessions"] == [] and board["memories"] == [] and board["links"] == []
    assert not database.exists()


def test_board_cli_prints_json(tmp_path, monkeypatch, capsys):
    from holocore.cli import main
    manager, *_ = _home(tmp_path, monkeypatch)
    monkeypatch.setattr("sys.argv", ["holocore", "board", "--home", str(manager.home), "--days", "0"])
    assert main() == 0
    value = json.loads(capsys.readouterr().out)
    assert value["format"] == "holocore-board" and value["filters"]["days"] is None


def test_board_cli_writes_utf8_whatever_the_console_encoding(tmp_path):
    # The Obsidian plugin reads the output as UTF-8; a Windows pipe defaults to the ANSI code page.
    import os
    import subprocess
    import sys
    root = tmp_path / "café 🚀"
    root.mkdir()
    manager = HomeManager(tmp_path / "home")
    manager.register_world(root, import_archive=False)
    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "HOLOCORE_CONFIG_HOME": str(tmp_path / "pointer"),
           "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    result = subprocess.run([sys.executable, "-m", "holocore", "board", "--home", str(manager.home), "--days", "0"],
                            env=env, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    names = [item["name"] for item in json.loads(result.stdout.decode("utf-8"))["project_choices"]]
    assert names == ["café 🚀"]
