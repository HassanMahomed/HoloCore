"""Reusable local operations for the desktop and future companion integrations.

This is an in-process API: it does not expose a network listener or grant plugins
direct database access. Browsing never initializes a project or imports sources,
and every database read here opens SQLite in read-only mode.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .archive import Archive
from .config import Config
from .connection_health import check_client_connection, last_captures
from .folder_layout import HomeLayout
from .home import HomeManager
from .home_backup import (
    BackupError,
    configure_schedule,
    create_home_backup,
    disable_schedule,
    preview_home_restore,
    restore_home_backup,
    schedule_status,
)
from .install import inspect_client_connections
from .storage import storage_status

CONFIGURATION_LABELS = {"connected": "Configured", "needs-connection": "Not configured",
                        "invalid-config": "Configuration unreadable", "not-installed": "Unavailable"}
SECOND_BRAIN_WORLD = "Second Brain"
CODE_KINDS = {"file", "module", "class", "function", "method"}
_NOISE_PREFIXES = ("[Image:", "```", "<")


def is_noise(text) -> bool:
    """Fragments too short or too mechanical to be worth reading as a memory."""
    value = " ".join(str(text or "").split())
    return len(value) < 15 or len(value.split()) < 3 or value.startswith(_NOISE_PREFIXES)
KNOWLEDGE_SOURCES = {"project": "This project", "second_brain": "Second Brain", "home": "Whole Home"}


def _read_only(path: Path) -> sqlite3.Connection | None:
    if not path.is_file():
        return None
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    return db


def _rows(path: Path, query: str, values: tuple = ()) -> list[sqlite3.Row]:
    db = _read_only(path)
    if db is None:
        return []
    with closing(db):
        try:
            return db.execute(query, values).fetchall()
        except sqlite3.OperationalError:  # Table not created yet in this database.
            return []


def default_project(home: str | Path | None = None, cwd: str | Path | None = None) -> Path:
    """Project to open when launched from the Start menu: the one containing cwd, else the most recently active."""
    manager = HomeManager(home)
    here = Path(cwd or Path.cwd()).resolve()
    rows = [row for row in manager.list_worlds()["worlds"] if row.get("root") and Path(row["root"]).is_dir()]
    for row in rows:
        root = Path(row["root"]).resolve()
        if here == root or root in here.parents:
            return root
    database = HomeLayout(manager.home).memories / "animus.db"
    recent = {row[0]: row[1] for row in _rows(database, "SELECT project_id, MAX(updated_at) FROM agent_runs GROUP BY project_id")}
    rows.sort(key=lambda row: recent.get(str(row.get("id")), ""), reverse=True)
    return Path(rows[0]["root"]).resolve() if rows else here


def note_label(path: Path, fallback: str) -> str:
    """A readable name: the note's Summary line, else its first heading, else its file name."""
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
            lines = [line.strip() for line in handle.read(4096).splitlines()]
    except OSError:
        return fallback
    if "## Summary" in lines:
        following = next((line for line in lines[lines.index("## Summary") + 1:] if line), "")
        if following and not following.startswith("#"):
            return following.lstrip("-* ").strip()
    heading = next((line[2:].strip() for line in lines if line.startswith("# ")), "")
    return heading or fallback


def activity_label(row: dict) -> str:
    """The first sentence HoloCore recorded, rather than the transcript file name."""
    first = str(row.get("content") or "").split("\n\nTranscript:", 1)[0].strip()
    return first.splitlines()[0] if first else str(row.get("title") or "")


class DesktopService:
    def __init__(self, root: str | Path, home: str | Path | None = None):
        self.root = Path(root).expanduser().resolve()
        self.config = Config.load(root=self.root)
        if home is not None:
            manager = HomeManager(home)
            folders = HomeLayout(manager.home)
            rows = manager.list_worlds()["worlds"]
            row = next((item for item in rows if Path(item["root"]).resolve() == self.root), None)
            world = str(row["id"]) if row else manager.world_id_for(self.root)
            storage = Path(row.get("storage") or manager.home / "Projects" / world) if row else manager.home / "Projects" / world
            self.config = Config(vault=folders.project_knowledge(world), state_dir=storage / "Runtime",
                                 home=manager.home, world_id=world, memories=folders.memories / "animus.db",
                                 raw_chats=folders.chat_history / world, atlas_graph=folders.code_map(storage) / "graph.json")

    @property
    def world(self) -> str:
        return self.config.world_id or self.root.name

    def projects(self) -> list[dict]:
        if self.config.home is None:
            return []
        rows = HomeManager(self.config.home).list_worlds()["worlds"]
        return [{"id": str(row.get("id", "")), "name": row.get("name", row.get("id", "Project")),
                 "root": row["root"], "available": Path(row["root"]).is_dir()}
                for row in rows if row.get("root")]

    def connections(self) -> list[dict]:
        captures = last_captures(self.config.memory_path, self.world)
        rows = []
        for name, item in inspect_client_connections().items():
            configured = bool(item.get("connected"))
            rows.append({"name": name, "configuration": CONFIGURATION_LABELS.get(item["state"], item["state"]),
                         "configured": configured, "runtime_verified": False,
                         "last_capture": captures.get(name),
                         "detail": item["detail"], "config_path": item["config_path"]})
        return rows

    def test_connection(self, name: str) -> dict:
        """Run the client's configured HoloCore command; never edits its configuration."""
        return check_client_connection(name)

    # Knowledge -----------------------------------------------------------------
    def knowledge_folder(self, source: str = "project") -> Path | None:
        if source == "project":
            return self.config.vault
        if self.config.home is None:
            return None
        layout = HomeLayout(self.config.home)
        return layout.project_knowledge(SECOND_BRAIN_WORLD) if source == "second_brain" else layout.knowledge

    def _archive(self, source: str) -> Archive | None:
        folder = self.knowledge_folder(source)
        return Archive(folder) if folder is not None and folder.is_dir() else None

    def notes(self, source: str = "project", limit: int | None = 500) -> list[dict]:
        archive = self._archive(source)
        if archive is None:
            return []
        rows = archive.list_notes(limit)
        if limit is not None:  # Only label what will be displayed.
            for row in rows:
                row["label"] = note_label(archive.vault / row["path"], row["title"])
        return rows

    def search_knowledge(self, query: str, limit: int = 20, source: str = "project") -> list[dict]:
        archive = self._archive(source)
        if archive is None or not query.strip():
            return []
        rows = archive.search(query.strip(), limit=max(1, min(limit, 20)))
        for row in rows:
            row["label"] = note_label(archive.vault / row["path"], row["title"])
        return rows

    def read_note(self, path: str, source: str = "project") -> dict:
        archive = self._archive(source)
        if archive is None:
            raise FileNotFoundError("This knowledge folder does not exist yet")
        return archive.read(path)

    # Memories ------------------------------------------------------------------
    def memory_kinds(self) -> list[str]:
        rows = _rows(self.config.memory_path, "SELECT DISTINCT json_extract(metadata, '$.memory_kind') FROM shards "
                     "WHERE world_id=? ORDER BY 1", (self.world,))
        return [str(row[0]) for row in rows if row[0]]

    def memories(self, query: str = "", kind: str | None = None, limit: int = 300, hide_noise: bool = False) -> list[dict]:
        limit = max(1, min(int(limit), 1000))
        rows = self._memory_rows(query, kind, limit * 4 if hide_noise else limit)
        return [row for row in rows if not (hide_noise and is_noise(row["content"]))][:limit]

    def _memory_rows(self, query: str, kind: str | None, limit: int) -> list[dict]:
        sql = ("SELECT id, sector_id, json_extract(metadata, '$.memory_kind') AS kind, created_at, content "
               "FROM shards WHERE world_id=?")
        values: list = [self.world]
        if kind:
            sql += " AND json_extract(metadata, '$.memory_kind')=?"
            values.append(kind)
        for term in query.split():
            sql += " AND content LIKE ? ESCAPE '\\'"
            values.append("%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        sql += " ORDER BY created_at DESC LIMIT ?"
        values.append(limit)
        return [dict(row) for row in _rows(self.config.memory_path, sql, tuple(values))]

    def timeline(self, limit: int = 50, hide_noise: bool = False) -> list[dict]:
        limit = max(1, min(int(limit), 500))
        rows = _rows(self.config.memory_path, "SELECT occurred_at, title, kind, content FROM diary WHERE world_id=? "
                     "ORDER BY occurred_at DESC LIMIT ?", (self.world, limit * 4 if hide_noise else limit))
        labelled = [{**dict(row), "label": activity_label(dict(row))} for row in rows]
        return [row for row in labelled if not (hide_noise and is_noise(row["label"]))][:limit]

    # Home and code map ---------------------------------------------------------
    def home_summary(self) -> dict:
        """Counts across every registered project in the Home, read-only."""
        database = self.config.memory_path
        memories = {row[0]: row[1] for row in _rows(database, "SELECT world_id, COUNT(*) FROM shards GROUP BY world_id")}
        diary = {row[0]: row[1] for row in _rows(database, "SELECT world_id, MAX(occurred_at) FROM diary GROUP BY world_id")}
        runs = {row[0]: row[1] for row in _rows(database, "SELECT project_id, MAX(updated_at) FROM agent_runs GROUP BY project_id")}
        layout = HomeLayout(self.config.home) if self.config.home else None
        projects = []
        for project in self.projects():
            folder = layout.project_knowledge(project["id"]) if layout else None
            last = max(filter(None, (diary.get(project["id"]), runs.get(project["id"]))), default=None)
            projects.append({**project, "notes": len(Archive(folder).list_notes()) if folder and folder.is_dir() else 0,
                             "memories": int(memories.get(project["id"], 0)), "last_activity": last})
        home_notes = self.notes("home", limit=None) if layout else []
        second_brain = self._archive("second_brain")
        return {"home": str(self.config.home) if self.config.home else None, "projects": projects,
                "knowledge_notes": len(home_notes),
                "second_brain_notes": len(second_brain.list_notes()) if second_brain else 0,
                "memories": int(sum(memories.values())), "project_memories": int(memories.get(self.world, 0))}

    def code_map(self, top: int = 25) -> dict:
        path = self.config.atlas_graph
        if path is None or not path.is_file():
            return {"exists": False, "path": str(path) if path else None}
        graph = json.loads(path.read_text(encoding="utf-8"))
        nodes, links = graph.get("nodes", []), graph.get("links", [])
        degree: Counter = Counter()
        for link in links:
            degree[link.get("source")] += 1
            degree[link.get("target")] += 1
        code = {node.get("id"): node for node in nodes if node.get("kind") in CODE_KINDS}
        connected = [{"label": code[node_id].get("label", node_id), "kind": code[node_id].get("kind", ""),
                      "links": count, "source": code[node_id].get("source_file", "")}
                     for node_id, count in degree.most_common() if node_id in code][:top]
        return {"exists": True, "path": str(path), "signals": len(nodes), "links": len(links),
                "code_signals": len(code),
                "files": sum(1 for node in nodes if node.get("kind") == "file"),
                "updated": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                "kinds": Counter(str(node.get("kind", "unknown")) for node in nodes).most_common(),
                "most_connected": connected}

    def overview(self) -> dict:
        return {"project": self.world, "root": str(self.root),
                "available": self.root.is_dir(), "knowledge": str(self.config.vault),
                "database": str(self.config.memory_path),
                "knowledge_ready": self.config.vault.is_dir(),
                "memory_ready": self.config.memory_path.is_file(),
                "storage": storage_status(self.config.memory_path),
                "connections": self.connections(), "projects": self.projects()}

    def dashboard(self) -> dict:
        """Everything the Home page shows, gathered in one background call."""
        return {**self.overview(), "summary": self.home_summary(), "timeline": self.timeline(30, hide_noise=True),
                "backups": schedule_status(self.config.home) if self.config.home else None}

    # Whole-Home backups ----------------------------------------------------------
    def _home(self) -> Path:
        if self.config.home is None:
            raise BackupError("Open a project that belongs to a HoloCore Home first")
        return self.config.home

    def backup(self, folder: str | Path) -> dict:
        return create_home_backup(self._home(), folder)

    def restore_preview(self, backup: str | Path) -> dict:
        return preview_home_restore(backup, self._home())

    def restore(self, backup: str | Path) -> dict:
        return restore_home_backup(backup, self._home())

    def backup_schedule(self) -> dict:
        return schedule_status(self._home())

    def set_backup_schedule(self, **settings) -> dict:
        return configure_schedule(self._home(), **settings)

    def stop_backup_schedule(self) -> dict:
        return disable_schedule(self._home())
