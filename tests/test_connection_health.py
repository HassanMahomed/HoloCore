from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from holocore.activity import ActivityLedger
from holocore.connection_health import check_client_connection, last_captures
from holocore.desktop_app import describe_capture, describe_test
from holocore.desktop_service import DesktopService

SRC = str(Path(__file__).resolve().parents[1] / "src")


def _configure(tmp_path, monkeypatch, entry):
    monkeypatch.setenv("HOLOCORE_CLIENT_CONFIG_ROOT", str(tmp_path / "clients"))
    monkeypatch.setenv("HOLOCORE_CONFIG_HOME", str(tmp_path / "config"))
    config = tmp_path / "clients" / ".claude.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    if entry is not None:
        config.write_text(json.dumps({"mcpServers": {"holocore": entry}}), encoding="utf-8")
    return config


def test_unconfigured_and_unreadable_clients_are_reported(tmp_path, monkeypatch):
    config = _configure(tmp_path, monkeypatch, None)
    assert check_client_connection("claude")["result"] == "not-configured"
    config.write_text("{not json", encoding="utf-8")
    assert check_client_connection("claude")["result"] == "invalid-config"


def test_missing_command_and_bad_handshake_fail_without_editing_config(tmp_path, monkeypatch):
    config = _configure(tmp_path, monkeypatch, {"command": str(tmp_path / "missing.exe"), "args": ["mcp"]})
    before = config.read_bytes()
    result = check_client_connection("claude")
    assert result["result"] == "failed" and "not found" in result["detail"]
    assert config.read_bytes() == before
    config = _configure(tmp_path, monkeypatch, {"command": sys.executable, "args": ["-c", "print('hello')"]})
    before = config.read_bytes()
    result = check_client_connection("claude")
    assert result["result"] == "failed"
    assert config.read_bytes() == before


def test_real_holocore_server_passes(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch, {"command": sys.executable, "args": ["-m", "holocore.mcp_server"],
                                       "cwd": str(tmp_path), "env": {"PYTHONPATH": SRC}})
    result = check_client_connection("claude", timeout=60)
    assert result["result"] == "passed", result
    assert result["tools"] > 0
    assert datetime.fromisoformat(result["checked_at"]).tzinfo is not None
    assert describe_test(result).startswith("Connected · tested ")


def test_last_capture_is_scoped_and_read_only(tmp_path):
    path = tmp_path / "memories.db"
    assert last_captures(path, "p") == {}
    assert not path.exists()
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE unrelated (value)")
    assert last_captures(path, "p") == {}
    ledger = ActivityLedger(path)
    ledger.record_hook({"hook_event_name": "Stop", "session_id": "s1"}, client="claude", project_id="p")
    ledger.record_hook({"hook_event_name": "Stop", "session_id": "s2"}, client="codex", project_id="other")
    captures = last_captures(path, "p")
    assert set(captures) == {"claude"}
    assert datetime.fromisoformat(captures["claude"]).tzinfo is not None


def test_desktop_rows_report_capture_separately_from_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("HOLOCORE_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr("holocore.desktop_service.inspect_client_connections", lambda: {
        name: {"connected": True, "state": "connected", "detail": "Entry found", "config_path": "x"}
        for name in ("claude", "codex")})
    service = DesktopService(tmp_path)
    ActivityLedger(service.config.memory_path).record_hook(
        {"hook_event_name": "Stop", "session_id": "s"}, client="claude",
        project_id=service.config.world_id or service.root.name)
    rows = {row["name"]: row for row in service.connections()}
    assert rows["claude"]["configuration"] == "Configured" and rows["claude"]["last_capture"]
    assert rows["codex"]["configuration"] == "Configured" and rows["codex"]["last_capture"] is None


def test_capture_wording_shows_staleness_and_never_invents_a_capture():
    now = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
    assert describe_capture(None, now) == "No capture recorded"
    assert describe_capture((now - timedelta(days=3, hours=2)).isoformat(), now).endswith("(3 days ago)")
    assert describe_capture((now - timedelta(seconds=20)).isoformat(), now).endswith("(just now)")
    assert describe_test(None) == "Not tested"
