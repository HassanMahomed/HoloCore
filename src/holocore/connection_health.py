"""User-invoked connection tests and read-only capture recency.

A test starts the exact HoloCore MCP command a client is configured to run and
performs an MCP handshake. It never edits client configuration, and a passing
test says nothing about whether capture hooks are firing; capture recency is
read separately from the activity ledger.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import tomllib
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .install import _connection_config_path

_HANDSHAKE = "".join(json.dumps(message) + "\n" for message in (
    {"jsonrpc": "2.0", "id": 1, "method": "initialize",
     "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "holocore-connection-test", "version": "1"}}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _server_entry(client: str) -> tuple[dict[str, Any] | None, str]:
    path = _connection_config_path(client)
    if not path.is_file():
        return None, f"Client configuration not found: {path}"
    text = path.read_text(encoding="utf-8")
    if client == "codex":
        entry = tomllib.loads(text).get("mcp_servers", {}).get("holocore")
    else:
        entry = json.loads(text).get("mcp" if client == "opencode" else "mcpServers", {}).get("holocore")
    if not isinstance(entry, dict):
        return None, f"No HoloCore entry in {path}"
    return entry, str(path)


def _command(entry: dict[str, Any]) -> list[str]:
    command = entry.get("command")
    if isinstance(command, list):  # OpenCode stores the full argv as a list.
        return [str(part) for part in command]
    if not command:
        raise ValueError("HoloCore entry has no command")
    return [str(command), *(str(part) for part in entry.get("args", []))]


def check_client_connection(client: str, *, timeout: float = 30.0) -> dict[str, Any]:
    """Start the configured HoloCore server and verify an MCP handshake."""
    result: dict[str, Any] = {"client": client, "checked_at": _now()}
    try:
        entry, source = _server_entry(client)
    except (OSError, UnicodeError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        return {**result, "result": "invalid-config", "detail": f"Could not read client configuration: {exc}"}
    if entry is None:
        return {**result, "result": "not-configured", "detail": source}
    try:
        command = _command(entry)
    except ValueError as exc:
        return {**result, "result": "invalid-config", "detail": str(exc)}
    env = {**os.environ, **{str(k): str(v) for k, v in (entry.get("env") or {}).items()}}
    try:
        completed = subprocess.run(
            command, input=_HANDSHAKE, capture_output=True, text=True, encoding="utf-8",
            errors="replace", cwd=entry.get("cwd") or None, env=env, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError:
        return {**result, "result": "failed", "detail": f"Configured command was not found: {command[0]}"}
    except subprocess.TimeoutExpired:
        return {**result, "result": "failed", "detail": f"HoloCore did not respond within {timeout:g} seconds"}
    except OSError as exc:
        return {**result, "result": "failed", "detail": f"Could not start HoloCore: {exc}"}
    responses = {}
    for line in completed.stdout.splitlines():
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(message, dict) and "id" in message:
            responses[message["id"]] = message
    server = (responses.get(1, {}).get("result") or {}).get("serverInfo") or {}
    tools = (responses.get(2, {}).get("result") or {}).get("tools") or []
    if server.get("name") != "holocore" or not tools:
        error = (responses.get(1) or responses.get(2) or {}).get("error", {}).get("message")
        stderr = completed.stderr.strip().splitlines()[-1:] if completed.stderr.strip() else []
        reason = error or (stderr[0] if stderr else f"No valid MCP handshake (exit code {completed.returncode})")
        return {**result, "result": "failed", "detail": reason}
    return {**result, "result": "passed", "server_version": server.get("version"), "tools": len(tools),
            "detail": f"HoloCore {server.get('version', '')} answered with {len(tools)} tools"}


def last_captures(database: str | Path, project_id: str) -> dict[str, str]:
    """Return each client's latest recorded hook time for a project, read-only."""
    path = Path(database).resolve()
    if not path.is_file():
        return {}
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
            rows = db.execute("SELECT agent, MAX(updated_at) FROM agent_runs WHERE project_id=? GROUP BY agent",
                              (project_id,)).fetchall()
    except sqlite3.OperationalError:  # No activity ledger has been created yet.
        return {}
    return {str(agent): str(stamp) for agent, stamp in rows if stamp}
