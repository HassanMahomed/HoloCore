"""Read-only flow board of HoloCore activity: projects, AI sessions, memories, and changed files.

The Obsidian plugin runs ``holocore board --json`` and draws this structure. Every link
here comes from a recorded relationship in the Memories database; nothing is inferred.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path, PureWindowsPath

from .desktop_service import _rows, activity_label, is_noise
from .folder_layout import HomeLayout
from .home import HomeManager

FORMAT = "holocore-board"
FORMAT_VERSION = 1
MEMORIES_PER_SESSION = 4
MEMORIES_PER_PROJECT = 3
_TAG = re.compile(r"<[a-z][a-z0-9_-]*>", re.IGNORECASE)


def _metadata(value) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _same_path(a: str, b: str) -> bool:
    return str(PureWindowsPath(a)).casefold().rstrip("\\") == str(PureWindowsPath(b)).casefold().rstrip("\\")


def _title(text: str) -> str:
    """First recorded line of the session, without injected <tag> blocks."""
    return _TAG.split(text, 1)[0].strip()[:160]


def _memory(row, project: str, session: str | None) -> dict:
    content = str(row["content"] or "")
    text = " ".join((_TAG.split(content, 1)[0].strip() or content).split())
    return {"id": f"memory:{row['id']}", "project": project, "session": session,
            "kind": row["kind"] or "memory", "created_at": row["created_at"], "text": text[:280]}


def build_board(home: str | Path | None = None, *, project: str | None = None, days: int | None = 7,
                limit: int = 30, hide_noise: bool = True) -> dict:
    """Return the newest ``limit`` main AI sessions in the window, with what each one is linked to.

    Sessions from folders that are not registered HoloCore projects (for example an AI
    client's own working folders) are counted in ``unregistered_sessions`` but not shown.
    """
    manager = HomeManager(home)
    database = HomeLayout(manager.home).memories / "animus.db"
    worlds = [row for row in manager.list_worlds()["worlds"] if row.get("id")]
    by_id = {str(row["id"]): row for row in worlds}

    def project_for(project_id: str, cwd: str) -> str:
        if project_id in by_id:
            return project_id
        match = next((str(row["id"]) for row in worlds if row.get("root") and cwd and _same_path(row["root"], cwd)), None)
        return match or project_id

    since = None
    if days:
        since = (datetime.now(timezone.utc) - timedelta(days=int(days))).isoformat()
    sql = ("SELECT r.id, r.project_id, r.agent, r.model, r.status, r.started_at, r.updated_at, r.metadata, "
           "(SELECT COUNT(*) FROM activity_actions a WHERE a.run_id=r.id) AS actions, "
           "(SELECT COUNT(*) FROM agent_runs c WHERE c.parent_run_id=r.id) AS subagents "
           "FROM agent_runs r WHERE r.parent_run_id IS NULL")
    values: list = []
    if since:
        sql += " AND r.updated_at >= ?"
        values.append(since)
    sql += " ORDER BY r.updated_at DESC"
    sessions, links, unregistered = [], [], 0
    for row in _rows(database, sql, tuple(values)):
        meta = _metadata(row["metadata"])
        owner = project_for(str(row["project_id"]), str(meta.get("cwd") or ""))
        if project and owner != project:
            continue
        if owner not in by_id:
            unregistered += 1
            continue
        sessions.append({"id": f"session:{row['id']}", "run_id": row["id"], "project": owner,
                         "agent": row["agent"], "model": row["model"], "status": row["status"],
                         "started_at": row["started_at"], "updated_at": row["updated_at"],
                         "actions": row["actions"], "subagents": row["subagents"], "title": "",
                         "transcript": str(meta.get("transcript_path") or ""), "cwd": str(meta.get("cwd") or "")})
        if len(sessions) >= max(1, int(limit)):
            break

    memories, files = [], {}
    for session in sessions:
        links.append({"source": f"project:{session['project']}", "target": session["id"]})
        transcript = session.pop("transcript")
        if transcript:
            source = f"{session['agent']}:{transcript}"
            earliest = _rows(database, "SELECT d.title, d.content FROM diary_provenance p JOIN diary d ON d.id=p.diary_id "
                             "WHERE p.source_ref=? ORDER BY d.occurred_at LIMIT 6", (source,))
            # Skip injected instruction files such as "# AGENTS.md instructions ...".
            titles = [_title(activity_label(dict(row))) for row in earliest]
            session["title"] = next((title for title in titles if title and not title.startswith(("#", "<"))), "")
            # Every "<source>#..." reference, as an index range: '$' is the character after '#'.
            # world_id IN (...) lets SQLite use the (world_id, source_ref) index instead of scanning.
            span = (source + "#", source + "$")
            within = "p.world_id IN (SELECT id FROM worlds) AND p.source_ref >= ? AND p.source_ref < ?"
            found = _rows(database, "SELECT DISTINCT s.id, s.content, s.created_at, "
                          "json_extract(s.metadata, '$.memory_kind') AS kind FROM provenance p "
                          f"JOIN shards s ON s.id=p.shard_id WHERE {within} "
                          "ORDER BY s.created_at DESC LIMIT 40", span)
            kept = [row for row in found if not (hide_noise and is_noise(row["content"]))][:MEMORIES_PER_SESSION]
            counted = _rows(database, f"SELECT COUNT(DISTINCT shard_id) FROM provenance p WHERE {within}", span)
            session["memories"] = counted[0][0] if counted else 0  # No memories table yet.
            for row in kept:
                memories.append(_memory(row, session["project"], session["id"]))
                links.append({"source": session["id"], "target": f"memory:{row['id']}"})
        for row in _rows(database, "SELECT i.id, i.uri, i.label, COUNT(*) AS touches FROM activity_actions a "
                         "JOIN action_items x ON x.action_id=a.id JOIN activity_items i ON i.id=x.item_id "
                         "WHERE a.run_id=? AND i.kind='file' GROUP BY i.id ORDER BY touches DESC LIMIT 8",
                         (session["run_id"],)):
            uri = str(row["uri"])
            base = session["cwd"] or str(by_id.get(session["project"], {}).get("root") or "")
            path = uri if PureWindowsPath(uri).is_absolute() or not base else str(PureWindowsPath(base) / uri)
            key = f"file:{row['id']}"
            entry = files.setdefault(key, {"id": key, "project": session["project"], "label": row["label"],
                                           "uri": uri, "path": path, "touches": 0})
            entry["touches"] += row["touches"]
            links.append({"source": session["id"], "target": key})
        session.pop("cwd")

    shown = {session["project"] for session in sessions} | ({project} if project else set())
    seen = {item["id"] for item in memories}
    for owner in sorted(shown):
        sql = ("SELECT id, content, created_at, json_extract(metadata, '$.memory_kind') AS kind FROM shards "
               "WHERE world_id=?" + (" AND created_at >= ?" if since else "") + " ORDER BY created_at DESC LIMIT 40")
        rows = _rows(database, sql, (owner, since) if since else (owner,))
        kept = [row for row in rows if f"memory:{row['id']}" not in seen
                and not (hide_noise and is_noise(row["content"]))][:MEMORIES_PER_PROJECT]
        for row in kept:
            memories.append(_memory(row, owner, None))
            links.append({"source": f"project:{owner}", "target": f"memory:{row['id']}"})

    counts = {row[0]: row[1] for row in _rows(database, "SELECT project_id, COUNT(*) FROM agent_runs "
                                              "WHERE parent_run_id IS NULL GROUP BY project_id")}
    projects = []
    for owner in sorted(shown, key=lambda item: str(by_id.get(item, {}).get("name") or item).casefold()):
        row = by_id.get(owner, {})
        projects.append({"id": f"project:{owner}", "project": owner, "name": str(row.get("name") or owner),
                         "root": str(row.get("root") or ""), "registered": owner in by_id,
                         "sessions": sum(1 for item in sessions if item["project"] == owner),
                         "all_sessions": counts.get(owner, 0)})
    return {"format": FORMAT, "version": FORMAT_VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
            "home": str(manager.home), "database_found": database.is_file(),
            "filters": {"project": project, "days": days, "limit": limit, "hide_noise": hide_noise},
            "project_choices": [{"id": str(row["id"]), "name": str(row.get("name") or row["id"])} for row in worlds],
            "unregistered_sessions": unregistered, "projects": projects, "sessions": sessions, "memories": memories,
            "files": sorted(files.values(), key=lambda item: -item["touches"]), "links": links}
