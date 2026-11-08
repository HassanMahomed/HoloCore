"""Reusable local operations for the desktop and future companion integrations.

This is an in-process API: it does not expose a network listener or grant plugins
direct database access. Browsing never initializes a project or imports sources.
"""
from __future__ import annotations

from pathlib import Path

from .archive import Archive
from .config import Config
from .home import HomeManager
from .install import inspect_client_connections
from .storage import create_backup, preview_restore, storage_status


class DesktopService:
    def __init__(self, root: str | Path, home: str | Path | None = None):
        self.root = Path(root).expanduser().resolve()
        self.config = Config.load(root=self.root)
        if home is not None:
            from .folder_layout import HomeLayout
            manager = HomeManager(home)
            folders = HomeLayout(manager.home)
            rows = manager.list_worlds()["worlds"]
            row = next((item for item in rows if Path(item["root"]).resolve() == self.root), None)
            world = str(row["id"]) if row else manager.world_id_for(self.root)
            storage = Path(row.get("storage") or manager.home / "Projects" / world) if row else manager.home / "Projects" / world
            self.config = Config(vault=folders.project_knowledge(world), state_dir=storage / "Runtime",
                                 home=manager.home, world_id=world, memories=folders.memories / "animus.db",
                                 raw_chats=folders.chat_history / world, atlas_graph=folders.code_map(storage) / "graph.json")

    def projects(self) -> list[dict]:
        rows = HomeManager(self.config.home).list_worlds()["worlds"]
        return [{"name": row.get("name", row.get("id", "Project")),
                 "root": row["root"], "available": Path(row["root"]).is_dir()}
                for row in rows if row.get("root")]

    def connections(self) -> list[dict]:
        rows = []
        for name, item in inspect_client_connections().items():
            configured = bool(item.get("connected"))
            rows.append({"name": name, "state": "Configured; runtime unverified" if configured else item["state"],
                         "configured": configured, "runtime_verified": False,
                         "last_capture": None,
                         "detail": item["detail"], "config_path": item["config_path"]})
        return rows

    def search_knowledge(self, query: str, limit: int = 20) -> list[dict]:
        if not query.strip():
            return []
        return Archive(self.config.vault).search(query.strip(), limit=max(1, min(limit, 20)))

    def overview(self) -> dict:
        return {"project": self.config.world_id or self.root.name, "root": str(self.root),
                "available": self.root.is_dir(), "knowledge": str(self.config.vault),
                "database": str(self.config.memory_path),
                "knowledge_ready": self.config.vault.is_dir(),
                "memory_ready": self.config.memory_path.is_file(),
                "storage": storage_status(self.config.memory_path),
                "connections": self.connections(), "projects": self.projects()}

    def backup(self, directory: str | Path) -> dict:
        return create_backup(self.config.memory_path, directory)

    def restore_preview(self, backup: str | Path) -> dict:
        return preview_restore(self.config.memory_path, backup)
