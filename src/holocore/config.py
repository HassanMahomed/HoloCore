from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Config:
    vault: Path
    state_dir: Path
    llm: dict | None = None
    home: Path | None = None
    world_id: str | None = None
    animus: Path | None = None
    raw_chats: Path | None = None
    atlas_graph: Path | None = None

    @staticmethod
    def _registered_world(root: Path) -> dict | None:
        """Resolve a World from the single Home registry without writing locally."""
        config_home = Path(os.getenv("HOLOCORE_CONFIG_HOME", Path.home() / ".holocore"))
        pointer = config_home / "home.json"
        try:
            home = Path(json.loads(pointer.read_text(encoding="utf-8"))["home"]).expanduser().resolve()
            registry = json.loads((home / "worlds.json").read_text(encoding="utf-8"))
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        key = os.path.normcase(str(root.resolve()))
        for record in registry.get("worlds", []):
            try:
                if os.path.normcase(str(Path(record["root"]).resolve())) == key:
                    return {**record, "home": str(home)}
            except (KeyError, TypeError, OSError):
                continue
        return None

    @staticmethod
    def _selected_home() -> Path | None:
        config_home = Path(os.getenv("HOLOCORE_CONFIG_HOME", Path.home() / ".holocore"))
        try:
            return Path(json.loads((config_home / "home.json").read_text(encoding="utf-8"))["home"]).expanduser().resolve()
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    @classmethod
    def for_world(cls, root: Path) -> "Config":
        root = root.resolve()
        registered = cls._registered_world(root)
        home = Path(registered["home"]) if registered else cls._selected_home()
        if home:
            from .folder_layout import HomeLayout
            folders = HomeLayout(home)
            if registered:
                world_id = str(registered["id"])
            else:
                from .home import HomeManager
                world_id = HomeManager(home).world_id_for(root)
            world_dir = Path(registered.get("storage") or home / "Projects" / world_id) if registered else home / "Projects" / world_id
            world_archive = folders.project_knowledge(world_id)
            return cls(
                vault=world_archive,
                state_dir=world_dir / "Runtime",
                home=home,
                world_id=world_id,
                animus=folders.memories / "animus.db",
                raw_chats=folders.chat_history / world_id,
                atlas_graph=folders.code_map(world_dir) / "graph.json",
            )
        return cls(Path(os.getenv("HOLOCORE_VAULT", root / "Archive")), Path(os.getenv("HOLOCORE_STATE", root / ".holocore")), None)

    @classmethod
    def load(cls, path: Path | None = None, root: Path | None = None) -> "Config":
        root = (root or Path.cwd()).resolve(); default = cls.for_world(root); path = path or root / ".holocore" / "config.json"
        if not path.exists(): return default
        raw = json.loads(path.read_text(encoding="utf-8"))
        # A registered Home World owns runtime paths. Project-local configs
        # from older releases can point at hashed folders, local databases, or
        # the removed Shared Archive, which makes recall silently empty.
        if default.home is not None:
            default.llm = raw.get("llm") or default.llm
            return default
        return cls(
            vault=Path(raw.get("archive", raw.get("vault", default.vault))),
            state_dir=Path(raw.get("state_dir", path.parent)),
            llm=raw.get("llm"),
            home=Path(raw["home"]) if raw.get("home") else None,
            world_id=str(raw.get("world_id")) if raw.get("world_id") else None,
            animus=Path(raw["animus"]) if raw.get("animus") else None,
            raw_chats=Path(raw["raw_chats"]) if raw.get("raw_chats") else None,
            atlas_graph=Path(raw["atlas_graph"]) if raw.get("atlas_graph") else None,
        )

    @property
    def atlas_path(self) -> Path: return self.atlas_graph or self.state_dir / "atlas.json"

    @property
    def atlas_graph_path(self) -> Path: return self.atlas_graph or self.state_dir / "Atlas" / "graph.json"

    @property
    def animus_path(self) -> Path: return self.animus or self.state_dir / "animus.db"

    @property
    def raw_chats_path(self) -> Path: return self.raw_chats or self.state_dir / "raw-chats"

    def save(self, path: Path | None = None) -> Path:
        path = path or self.state_dir / "world.json"; path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists(): path.write_text(json.dumps({"version": 5, "archive": str(self.vault), "state_dir": str(self.state_dir), "home": str(self.home) if self.home else None, "world_id": self.world_id, "animus": str(self.animus) if self.animus else None, "raw_chats": str(self.raw_chats) if self.raw_chats else None, "atlas_graph": str(self.atlas_graph) if self.atlas_graph else None, "llm": self.llm or {"provider": "local"}}, indent=2), encoding="utf-8")
        return path
