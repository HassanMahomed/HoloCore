"""Structured, privacy-bounded agent run and action history."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


_TERMINAL_EVENTS = {"sessionend", "session_end"}
_PAUSE_EVENTS = {"stop", "interrupt", "subagentstop", "subagent_stop"}
_PATH_KEYS = {"path", "file", "file_path", "filepath", "target_path", "workdir"}
_SECRET_KEYS = re.compile(r"(token|secret|password|authorization|api[_-]?key|cookie)", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "unknown").casefold()).strip("-") or "unknown"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _safe_metadata(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Keep useful lifecycle metadata without storing prompts or tool payload bodies."""
    allowed = {
        "hook_event_name", "session_id", "turn_id", "agent_id", "agent_type",
        "model", "permission_mode", "tool_name", "tool", "reason", "source",
        "cwd", "transcript_path", "is_error", "duration_ms",
    }
    result: dict[str, Any] = {}
    for key in allowed:
        value = payload.get(key)
        if value is None or _SECRET_KEYS.search(key):
            continue
        if isinstance(value, (str, int, float, bool)):
            text = str(value)
            result[key] = text[:2048] if isinstance(value, str) else value
    return result


def _walk_paths(value: Any, *, depth: int = 0) -> list[str]:
    if depth > 4:
        return []
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).casefold()
            if normalized in _PATH_KEYS and isinstance(item, (str, Path)):
                text = str(item).strip()
                if text:
                    found.append(text[:4096])
            elif not _SECRET_KEYS.search(normalized):
                found.extend(_walk_paths(item, depth=depth + 1))
    elif isinstance(value, (list, tuple)):
        for item in value[:100]:
            found.extend(_walk_paths(item, depth=depth + 1))
    return list(dict.fromkeys(found))


@dataclass(frozen=True)
class AgentRun:
    id: str
    project_id: str
    session_id: str
    agent: str
    agent_type: str
    model: str
    parent_run_id: str | None
    status: str
    started_at: str
    updated_at: str
    ended_at: str | None
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class ActivityAction:
    id: str
    run_id: str
    project_id: str
    event: str
    tool: str
    status: str
    occurred_at: str
    duration_ms: int | None
    metadata: Mapping[str, Any]
    items: tuple[str, ...] = ()


class ActivityLedger:
    """Append-safe activity ledger stored beside HoloCore's memory index."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def _initialize(self) -> None:
        with self._connection() as db:
            db.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS agent_runs(
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, session_id TEXT NOT NULL,
                    agent TEXT NOT NULL, agent_type TEXT NOT NULL, model TEXT NOT NULL,
                    parent_run_id TEXT, status TEXT NOT NULL, started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, ended_at TEXT, metadata TEXT NOT NULL,
                    UNIQUE(project_id, agent, session_id));
                CREATE INDEX IF NOT EXISTS ix_agent_runs_project ON agent_runs(project_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS activity_actions(
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
                    project_id TEXT NOT NULL, event TEXT NOT NULL, tool TEXT NOT NULL,
                    status TEXT NOT NULL, occurred_at TEXT NOT NULL, duration_ms INTEGER,
                    metadata TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS ix_activity_actions_run ON activity_actions(run_id, occurred_at);
                CREATE TABLE IF NOT EXISTS activity_items(
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, uri TEXT NOT NULL,
                    kind TEXT NOT NULL, label TEXT NOT NULL, metadata TEXT NOT NULL,
                    UNIQUE(project_id, uri));
                CREATE TABLE IF NOT EXISTS action_items(
                    action_id TEXT NOT NULL REFERENCES activity_actions(id) ON DELETE CASCADE,
                    item_id TEXT NOT NULL REFERENCES activity_items(id) ON DELETE CASCADE,
                    relation TEXT NOT NULL, PRIMARY KEY(action_id, item_id, relation));
                CREATE INDEX IF NOT EXISTS ix_activity_items_project_uri
                    ON activity_items(project_id, uri);
                CREATE INDEX IF NOT EXISTS ix_action_items_item
                    ON action_items(item_id, action_id);
                """
            )

    @staticmethod
    def run_id(project_id: str, agent: str, session_id: str) -> str:
        return uuid.uuid5(uuid.NAMESPACE_URL, f"holocore:{project_id}:{agent}:{session_id}").hex

    def record_hook(self, payload: Mapping[str, Any], *, client: str, project_id: str) -> dict[str, Any]:
        event = str(payload.get("hook_event_name") or payload.get("event") or "unknown").strip()
        session_id = str(payload.get("session_id") or payload.get("transcript_path") or "local-session").strip()
        agent_id = str(payload.get("agent_id") or "").strip()
        agent = _slug(client)
        agent_type = str(payload.get("agent_type") or ("subagent" if agent_id else "main")).strip()
        effective_session = f"{session_id}:{agent_id}" if agent_id else session_id
        run_id = self.run_id(project_id, agent, effective_session)
        parent_run_id = self.run_id(project_id, agent, session_id) if agent_id else None
        now = _now()
        normalized = event.casefold().replace("-", "").replace("_", "")
        run_status = "completed" if normalized in _TERMINAL_EVENTS else "paused" if normalized in _PAUSE_EVENTS else "active"
        ended_at = now if run_status == "completed" else None
        metadata = _safe_metadata(payload)
        tool = str(payload.get("tool_name") or payload.get("tool") or "").strip()[:256]
        status = "failed" if payload.get("is_error") or payload.get("error") else "completed"
        try:
            duration = int(payload["duration_ms"]) if payload.get("duration_ms") is not None else None
        except (TypeError, ValueError):
            duration = None
        event_key = str(payload.get("event_id") or payload.get("hook_id") or "").strip()
        if not event_key:
            # The complete payload participates only in a one-way idempotency
            # digest. It is never persisted by the ledger.
            canonical = _json({"run": run_id, "payload": payload})
            event_key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        action_id = uuid.uuid5(uuid.NAMESPACE_URL, f"holocore-action:{event_key}").hex
        item_paths = _walk_paths(payload)
        created = False
        with self._connection() as db:
            db.execute(
                """INSERT INTO agent_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(project_id, agent, session_id) DO UPDATE SET
                agent_type=excluded.agent_type, model=excluded.model,
                parent_run_id=COALESCE(excluded.parent_run_id, agent_runs.parent_run_id),
                status=excluded.status, updated_at=excluded.updated_at,
                ended_at=COALESCE(excluded.ended_at, agent_runs.ended_at), metadata=excluded.metadata""",
                (run_id, project_id, effective_session, agent, agent_type, str(payload.get("model") or ""),
                 parent_run_id, run_status, now, now, ended_at, _json(metadata)),
            )
            cursor = db.execute(
                "INSERT OR IGNORE INTO activity_actions VALUES(?,?,?,?,?,?,?,?,?)",
                (action_id, run_id, project_id, event, tool, status, now, duration, _json(metadata)),
            )
            created = cursor.rowcount > 0
            if created:
                for uri in item_paths:
                    item_id = uuid.uuid5(uuid.NAMESPACE_URL, f"holocore-item:{project_id}:{uri}").hex
                    kind = "directory" if uri.endswith(("/", "\\")) else "file"
                    db.execute(
                        "INSERT OR IGNORE INTO activity_items VALUES(?,?,?,?,?,?)",
                        (item_id, project_id, uri, kind, Path(uri).name or uri, "{}"),
                    )
                    db.execute("INSERT OR IGNORE INTO action_items VALUES(?,?,?)", (action_id, item_id, "affected"))
        return {"recorded": created, "run_id": run_id, "action_id": action_id, "event": event, "items": item_paths}

    def runs(self, *, project_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query = "SELECT * FROM agent_runs"
        values: list[Any] = []
        if project_id:
            query += " WHERE project_id=?"
            values.append(project_id)
        query += " ORDER BY updated_at DESC LIMIT ?"
        values.append(max(1, min(int(limit), 1000)))
        with self._connection() as db:
            rows = db.execute(query, values).fetchall()
        return [{**dict(row), "metadata": json.loads(row["metadata"])} for row in rows]

    def actions(self, run_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
        with self._connection() as db:
            rows = db.execute(
                """SELECT a.*, group_concat(i.uri, char(10)) AS item_uris
                FROM activity_actions a
                LEFT JOIN action_items ai ON ai.action_id=a.id
                LEFT JOIN activity_items i ON i.id=ai.item_id
                WHERE a.run_id=? GROUP BY a.id ORDER BY a.occurred_at LIMIT ?""",
                (run_id, max(1, min(int(limit), 5000))),
            ).fetchall()
        return [{**dict(row), "metadata": json.loads(row["metadata"]),
                 "items": tuple(filter(None, str(row["item_uris"] or "").splitlines()))} for row in rows]

    def affected(
        self,
        query: str,
        *,
        project_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Return newest recorded actions linked to matching files or directories."""
        normalized = str(query).strip().replace("\\", "/").casefold()
        if not normalized:
            raise ValueError("Affected-item query cannot be empty.")
        values: list[Any] = [f"%{normalized}%"]
        project_clause = ""
        if project_id:
            project_clause = " AND i.project_id=?"
            values.append(project_id)
        values.append(max(1, min(int(limit), 1000)))
        with self._connection() as db:
            rows = db.execute(
                f"""SELECT i.id AS item_id, i.uri, i.kind, i.label,
                    a.id AS action_id, a.event, a.tool, a.status AS action_status,
                    a.occurred_at, a.duration_ms, r.id AS run_id, r.agent,
                    r.agent_type, r.model, r.status AS run_status
                FROM activity_items i
                JOIN action_items ai ON ai.item_id=i.id AND ai.relation='affected'
                JOIN activity_actions a ON a.id=ai.action_id
                JOIN agent_runs r ON r.id=a.run_id
                WHERE replace(lower(i.uri), '\\', '/') LIKE ?{project_clause}
                ORDER BY a.occurred_at DESC, a.id DESC LIMIT ?""",
                values,
            ).fetchall()
        return {"query": query, "count": len(rows), "results": [dict(row) for row in rows]}

    def handoff(
        self,
        *,
        project_id: str,
        run_id: str | None = None,
        action_limit: int = 50,
        item_limit: int = 100,
    ) -> dict[str, Any]:
        """Build a deterministic, privacy-bounded handoff from structured activity."""
        with self._connection() as db:
            if run_id:
                run = db.execute(
                    "SELECT * FROM agent_runs WHERE id=? AND project_id=?",
                    (run_id, project_id),
                ).fetchone()
            else:
                run = db.execute(
                    "SELECT * FROM agent_runs WHERE project_id=? ORDER BY updated_at DESC, id DESC LIMIT 1",
                    (project_id,),
                ).fetchone()
            if run is None:
                target = f"run {run_id}" if run_id else f"project {project_id}"
                raise ValueError(f"No recorded agent activity found for {target}.")
            selected_run_id = str(run["id"])
            totals = db.execute(
                """SELECT count(*) AS actions,
                    sum(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failures
                FROM activity_actions WHERE run_id=?""",
                (selected_run_id,),
            ).fetchone()
            item_count = db.execute(
                """SELECT count(DISTINCT ai.item_id)
                FROM action_items ai JOIN activity_actions a ON a.id=ai.action_id
                WHERE a.run_id=? AND ai.relation='affected'""",
                (selected_run_id,),
            ).fetchone()[0]
            item_rows = db.execute(
                """SELECT i.uri, i.kind, i.label, max(a.occurred_at) AS last_affected_at
                FROM activity_items i
                JOIN action_items ai ON ai.item_id=i.id AND ai.relation='affected'
                JOIN activity_actions a ON a.id=ai.action_id
                WHERE a.run_id=? GROUP BY i.id
                ORDER BY last_affected_at DESC, i.uri LIMIT ?""",
                (selected_run_id, max(1, min(int(item_limit), 100))),
            ).fetchall()
            action_rows = db.execute(
                """SELECT a.*, group_concat(i.uri, char(10)) AS item_uris
                FROM activity_actions a
                LEFT JOIN action_items ai ON ai.action_id=a.id
                LEFT JOIN activity_items i ON i.id=ai.item_id
                WHERE a.run_id=? GROUP BY a.id
                ORDER BY a.occurred_at DESC, a.id DESC LIMIT ?""",
                (selected_run_id, max(1, min(int(action_limit), 50))),
            ).fetchall()
        run_value = {**dict(run), "metadata": json.loads(run["metadata"])}
        recent_actions = [
            {
                **dict(row),
                "metadata": json.loads(row["metadata"]),
                "items": tuple(sorted(filter(None, str(row["item_uris"] or "").splitlines()))),
            }
            for row in action_rows
        ]
        return {
            "generated_at": _now(),
            "privacy": "Derived from sanitized structured activity; prompts and raw tool payloads are not stored.",
            "run": run_value,
            "summary": {
                "actions": int(totals["actions"] or 0),
                "failures": int(totals["failures"] or 0),
                "affected_items": int(item_count or 0),
            },
            "affected_items": [dict(row) for row in item_rows],
            "recent_actions": recent_actions,
        }

    def status(self, *, project_id: str | None = None) -> dict[str, Any]:
        where, values = (" WHERE project_id=?", [project_id]) if project_id else ("", [])
        with self._connection() as db:
            runs = db.execute(f"SELECT count(*) FROM agent_runs{where}", values).fetchone()[0]
            actions = db.execute(f"SELECT count(*) FROM activity_actions{where}", values).fetchone()[0]
            items = db.execute(f"SELECT count(*) FROM activity_items{where}", values).fetchone()[0]
        return {"ready": True, "database": str(self.path), "runs": runs, "actions": actions, "items": items}


__all__ = ["ActivityAction", "ActivityLedger", "AgentRun"]
