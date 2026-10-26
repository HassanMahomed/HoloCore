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
_PATH_KEYS = {"path", "file", "file_path", "filepath", "file_path_after", "filePath", "target_path", "target_file", "targetfile", "workdir"}
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
        "cwd", "transcript_path", "is_error", "duration_ms", "conversation_id",
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


def normalize_hook_payload(payload: Mapping[str, Any], client: str) -> dict[str, Any]:
    """Map client-specific hook names into the shared, privacy-bounded event shape."""
    result = dict(payload)
    if client == "cursor":
        result.setdefault("session_id", payload.get("conversation_id") or payload.get("generation_id"))
        result.setdefault("event_id", ":".join(str(part) for part in (
            payload.get("hook_event_name") or "event", payload.get("tool_use_id") or "",
            payload.get("file_path") or "",
            payload.get("timestamp") or payload.get("generation_id") or "",
        )))
        result.setdefault("model", payload.get("model_id"))
        result.setdefault("duration_ms", payload.get("duration"))
        result.setdefault("is_error", payload.get("isError") or payload.get("error_message") or payload.get("failure_type"))
        result.setdefault("agent_id", payload.get("subagent_id"))
        result.setdefault("agent_type", payload.get("subagent_type"))
        result.setdefault("tool_name", payload.get("tool_name") or payload.get("tool"))
    elif client == "gemini":
        response = payload.get("tool_response")
        result.setdefault("is_error", isinstance(response, Mapping) and bool(response.get("error")))
    elif client == "opencode":
        props = payload.get("properties")
        props = props if isinstance(props, Mapping) else {}
        info = props.get("info")
        info = info if isinstance(info, Mapping) else {}
        result.setdefault("session_id", payload.get("session_id") or props.get("sessionID") or props.get("session_id") or info.get("sessionID"))
        result.setdefault("event_id", payload.get("event_id") or props.get("id") or payload.get("tool_use_id"))
        result.setdefault("tool_name", payload.get("tool_name") or payload.get("tool"))
        result.setdefault("model", payload.get("model") or props.get("model"))
        result.setdefault("is_error", payload.get("is_error") or props.get("error"))
        result.setdefault("hook_event_name", payload.get("hook_event_name") or payload.get("event") or payload.get("type"))
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
                CREATE TABLE IF NOT EXISTS agent_handoffs(
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    run_id TEXT NOT NULL REFERENCES agent_runs(id),
                    title TEXT NOT NULL,
                    note TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('open','claimed','completed','abandoned')),
                    created_at TEXT NOT NULL,
                    claimed_at TEXT,
                    claimed_by TEXT,
                    closed_at TEXT,
                    close_reason TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_agent_handoffs_project_status
                    ON agent_handoffs(project_id, status, created_at DESC);
                """
            )

    @staticmethod
    def run_id(project_id: str, agent: str, session_id: str) -> str:
        return uuid.uuid5(uuid.NAMESPACE_URL, f"holocore:{project_id}:{agent}:{session_id}").hex

    def record_hook(self, payload: Mapping[str, Any], *, client: str, project_id: str) -> dict[str, Any]:
        payload = normalize_hook_payload(payload, client)
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

    def create_handoff(
        self,
        *,
        project_id: str,
        run_id: str,
        title: str = "",
        note: str = "",
    ) -> dict[str, Any]:
        """Create a persistent, claimable pointer to one run and its derived context."""
        title = str(title).strip()[:160]
        note = str(note).strip()[:2000]
        with self._connection() as db:
            run = db.execute(
                "SELECT agent, started_at FROM agent_runs WHERE id=? AND project_id=?",
                (run_id, project_id),
            ).fetchone()
            if run is None:
                raise ValueError(f"No recorded agent activity found for run {run_id} in this project.")
            if not title:
                title = f"Continue work from {run['agent']} run started {run['started_at']}"[:160]
            handoff_id = uuid.uuid4().hex
            now = _now()
            db.execute(
                """INSERT INTO agent_handoffs
                (id, project_id, run_id, title, note, status, created_at)
                VALUES(?,?,?,?,?,'open',?)""",
                (handoff_id, project_id, run_id, title, note, now),
            )
            row = db.execute("SELECT * FROM agent_handoffs WHERE id=?", (handoff_id,)).fetchone()
        return dict(row)

    def handoffs(
        self,
        *,
        project_id: str,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if status and status not in {"open", "claimed", "completed", "abandoned"}:
            raise ValueError("Handoff status must be open, claimed, completed, or abandoned.")
        values: list[Any] = [project_id]
        where = "project_id=?"
        if status:
            where += " AND status=?"
            values.append(status)
        values.append(max(1, min(int(limit), 500)))
        with self._connection() as db:
            rows = db.execute(
                f"""SELECT * FROM agent_handoffs WHERE {where}
                ORDER BY created_at DESC, id DESC LIMIT ?""",
                values,
            ).fetchall()
        return [dict(row) for row in rows]

    def claim_handoff(
        self,
        handoff_id: str,
        *,
        project_id: str,
        agent: str,
    ) -> dict[str, Any]:
        """Atomically claim open work; repeated claims by the same agent are safe."""
        agent = str(agent).strip()[:128]
        if not agent:
            raise ValueError("Agent name is required to claim a handoff.")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM agent_handoffs WHERE id=? AND project_id=?",
                (handoff_id, project_id),
            ).fetchone()
            if row is None:
                raise ValueError(f"Handoff {handoff_id} was not found in this project.")
            if row["status"] == "open":
                now = _now()
                db.execute(
                    """UPDATE agent_handoffs SET status='claimed', claimed_at=?, claimed_by=?
                    WHERE id=? AND project_id=? AND status='open'""",
                    (now, agent, handoff_id, project_id),
                )
                row = db.execute("SELECT * FROM agent_handoffs WHERE id=?", (handoff_id,)).fetchone()
            elif row["status"] == "claimed" and str(row["claimed_by"]).casefold() == agent.casefold():
                pass
            else:
                raise ValueError(
                    f"Handoff {handoff_id} is {row['status']} and cannot be claimed by {agent}."
                )
            result = dict(row)
        result["context"] = self.handoff(project_id=project_id, run_id=result["run_id"])
        return result

    def close_handoff(
        self,
        handoff_id: str,
        *,
        project_id: str,
        outcome: str,
        agent: str = "",
        reason: str = "",
    ) -> dict[str, Any]:
        """Complete or abandon a handoff while preserving its claim history."""
        if outcome not in {"completed", "abandoned"}:
            raise ValueError("Handoff outcome must be completed or abandoned.")
        agent = str(agent).strip()[:128]
        reason = str(reason).strip()[:1000]
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM agent_handoffs WHERE id=? AND project_id=?",
                (handoff_id, project_id),
            ).fetchone()
            if row is None:
                raise ValueError(f"Handoff {handoff_id} was not found in this project.")
            current = str(row["status"])
            if row["claimed_by"] and str(row["claimed_by"]).casefold() != agent.casefold():
                raise ValueError("Only the agent that claimed this handoff can close it.")
            if current == outcome:
                return dict(row)
            if current in {"completed", "abandoned"}:
                raise ValueError(f"Handoff {handoff_id} is already {current}.")
            if outcome == "completed" and current != "claimed":
                raise ValueError("Only a claimed handoff can be completed.")
            now = _now()
            db.execute(
                """UPDATE agent_handoffs SET status=?, closed_at=?, close_reason=?
                WHERE id=? AND project_id=?""",
                (outcome, now, reason, handoff_id, project_id),
            )
            updated = db.execute("SELECT * FROM agent_handoffs WHERE id=?", (handoff_id,)).fetchone()
        return dict(updated)

    def status(self, *, project_id: str | None = None) -> dict[str, Any]:
        where, values = (" WHERE project_id=?", [project_id]) if project_id else ("", [])
        with self._connection() as db:
            runs = db.execute(f"SELECT count(*) FROM agent_runs{where}", values).fetchone()[0]
            actions = db.execute(f"SELECT count(*) FROM activity_actions{where}", values).fetchone()[0]
            items = db.execute(f"SELECT count(*) FROM activity_items{where}", values).fetchone()[0]
        return {"ready": True, "database": str(self.path), "runs": runs, "actions": actions, "items": items}


__all__ = ["ActivityAction", "ActivityLedger", "AgentRun"]
