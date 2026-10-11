"""Cross-platform management of the user-visible HoloCore Home.

The Home is intentionally separate from any one project.  A small global
pointer selects it, while ``worlds.json`` records projects and the visible
Archive stores shared and per-World Markdown.
"""

from __future__ import annotations

import filecmp
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .archive import Archive


APP_VERSION = "0.7.1"
SCHEMA_VERSION = 1
CONFIG_HOME_ENV = "HOLOCORE_CONFIG_HOME"
POINTER_FILENAME = "home.json"
REGISTRY_FILENAME = "worlds.json"


class HomeError(RuntimeError):
    """Base exception for HoloCore Home failures."""


class HomeDataError(HomeError):
    """Raised when an existing pointer or registry is malformed."""


def _absolute(path: str | os.PathLike[str]) -> Path:
    return Path(path).expanduser().resolve()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _atomic_write(path: Path, content: bytes) -> bool:
    """Atomically replace ``path`` only when its bytes need to change."""
    try:
        if path.read_bytes() == content:
            return False
    except FileNotFoundError:
        pass

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _load_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HomeDataError(f"invalid {label} JSON at {path}: {exc}") from exc
    except OSError as exc:
        raise HomeDataError(f"could not read {label} at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise HomeDataError(f"{label} at {path} must contain a JSON object")
    return value


def _world_id(value: str) -> str:
    """Return a user-visible, filesystem-safe World id without rewriting it."""
    world_id = value.strip()
    if (
        not world_id
        or world_id in {".", ".."}
        or world_id != world_id.rstrip(". ")
        or any(ord(char) < 32 or char in '<>:"/\\|?*' for char in world_id)
    ):
        raise ValueError(f"project name is not safe for a World folder: {value!r}")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)), *(f"LPT{n}" for n in range(1, 10))}
    if world_id.split(".", 1)[0].upper() in reserved:
        raise ValueError(f"project name is reserved and cannot be a World folder: {value!r}")
    return world_id


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


class HomeManager:
    """Resolve, initialize, and maintain one visible HoloCore Home."""

    def __init__(
        self,
        home: str | os.PathLike[str] | None = None,
        *,
        config_home: str | os.PathLike[str] | None = None,
        app_version: str = APP_VERSION,
    ) -> None:
        configured = config_home or os.getenv(CONFIG_HOME_ENV) or (Path.home() / ".holocore")
        self.config_home = _absolute(configured)
        self.pointer_path = self.config_home / POINTER_FILENAME
        self._selected_home = _absolute(home) if home is not None else None
        self.app_version = str(app_version)

    @property
    def default_home(self) -> Path:
        return _absolute(Path.home() / "HoloCore")

    @property
    def home(self) -> Path:
        """Return the explicit, pointed-to, or default Home without writing."""
        if self._selected_home is not None:
            return self._selected_home
        if not self.pointer_path.exists():
            return self.default_home
        pointer = _load_object(self.pointer_path, label="Home pointer")
        selected = pointer.get("home")
        if not isinstance(selected, str) or not selected.strip():
            raise HomeDataError(f"Home pointer at {self.pointer_path} has no valid 'home' path")
        return _absolute(selected)

    @property
    def archive(self) -> Path:
        return self.home / "Archive"

    @property
    def projects(self) -> Path:
        """Central container for all per-World data."""
        return self.home / "Projects"

    @property
    def worlds_path(self) -> Path:
        return self.home / REGISTRY_FILENAME

    def _pointer_payload(self, home: Path) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "home": str(home)}

    def _write_pointer(self, home: Path) -> bool:
        return _atomic_write(self.pointer_path, _json_bytes(self._pointer_payload(home)))

    def select_home(
        self, path: str | os.PathLike[str], *, initialize: bool = True
    ) -> dict[str, Any]:
        """Select an explicit Home path and optionally initialize its layout."""
        self._selected_home = _absolute(path)
        if initialize:
            return self.initialize()
        changed = self._write_pointer(self._selected_home)
        return {
            "selected": True,
            "initialized": False,
            "changed": changed,
            "home": str(self._selected_home),
            "config_home": str(self.config_home),
            "pointer": str(self.pointer_path),
        }

    def initialize(self, home: str | os.PathLike[str] | None = None) -> dict[str, Any]:
        """Create the visible Home layout without replacing user-owned files."""
        if home is not None:
            self._selected_home = _absolute(home)
        target = self.home
        archive_path = target / "Archive"
        worlds_path = target / REGISTRY_FILENAME
        created: list[str] = []

        if not target.exists():
            target.mkdir(parents=True)
            created.append(str(target))
        elif not target.is_dir():
            raise NotADirectoryError(str(target))

        archive_existed = archive_path.exists()
        archive_report = Archive(archive_path).init_vault(container=True)
        if not archive_existed:
            created.append(str(archive_path))
        created.extend(str(archive_path / relative) for relative in archive_report["created"])

        guide = archive_path / "README.md"
        try:
            existing_guide = guide.read_text(encoding="utf-8")
        except OSError:
            existing_guide = ""
        if any(
            marker in existing_guide
            for marker in (
                "operation: vault-init",
                "operation: home-init",
                '"operation": "vault-init"',
                '"operation": "home-init"',
            )
        ):
            home_guide = """---
type: guide
tags: [guide, archive, holocore-home]
ai-first: true
provenance:
  system: holocore
  operation: home-init
---

## For future Claude
This Home contains one Archive and every centrally stored World.

# HoloCore Archive

- `Worlds/<project-name>/wiki/` contains durable knowledge scoped to one project.
- Cross-project knowledge uses links and tags across World notes.
- `../Projects/<project-name>/Atlas/` contains that World's structural and semantic graph.
- `../Animus/` contains one shared, World-scoped SQLite memory store and raw-chat audit.
- `../Projects/<project-name>/Inbox/` and `Sources/` hold ingestion material outside Obsidian.
- `system/index.md` is the knowledge entry point.

Open the `Archive` folder as one Obsidian vault. Obsidian is optional.
"""
            if _atomic_write(guide, home_guide.encode("utf-8")):
                created.append(str(guide))

        for folder in (
            target / "Projects",
            target / "Connections",
            target / "Animus" / "raw-chats",
            archive_path / "Worlds",
        ):
            if not folder.exists():
                folder.mkdir(parents=True)
                created.append(str(folder))
            elif not folder.is_dir():
                raise NotADirectoryError(str(folder))

        registry_created = False
        if worlds_path.exists():
            self._read_registry(worlds_path)
        else:
            registry_created = _atomic_write(
                worlds_path,
                _json_bytes({"schema_version": SCHEMA_VERSION, "worlds": []}),
            )
            if registry_created:
                created.append(str(worlds_path))

        pointer_changed = self._write_pointer(target)
        self._refresh_home_index()
        return {
            "initialized": True,
            "changed": bool(created or pointer_changed or registry_created),
            "home": str(target),
            "config_home": str(self.config_home),
            "pointer": str(self.pointer_path),
            "pointer_updated": pointer_changed,
            "archive": str(archive_path),
            "worlds_file": str(worlds_path),
            "created": list(dict.fromkeys(created)),
            "created_count": len(dict.fromkeys(created)),
        }

    init_home = initialize

    def _refresh_home_index(self) -> bool:
        """Maintain a small Obsidian index while preserving user-owned indexes."""
        index = self.archive / "system" / "index.md"
        try:
            existing = index.read_text(encoding="utf-8")
        except OSError:
            existing = ""
        marker = "<!-- holocore:managed-home-index -->"
        if existing and marker not in existing and not any(
            value in existing for value in ("operation: vault-init", '"operation": "vault-init"')
        ):
            return False
        worlds = self.list_worlds()["worlds"] if self.worlds_path.exists() else []
        links = [f"- [[Worlds/{item['id']}/system/index|{item['name']}]]" for item in worlds]
        body = [
            marker,
            "# HoloCore Home",
            "",
            "Start with the active project World. Use links and tags for knowledge that applies across Worlds.",
            "",
            "## Registered Worlds",
            "",
            *(links or ["- No Worlds registered yet."]),
            "",
        ]
        return _atomic_write(index, "\n".join(body).encode("utf-8"))

    def resolve_home(self) -> Path:
        """Compatibility-friendly method form of the resolved ``home`` property."""
        return self.home

    def _read_registry(self, path: Path | None = None) -> dict[str, Any]:
        registry_path = path or self.worlds_path
        registry = _load_object(registry_path, label="World registry")
        worlds = registry.get("worlds")
        if not isinstance(worlds, list) or any(not isinstance(item, dict) for item in worlds):
            raise HomeDataError(f"World registry at {registry_path} must contain a 'worlds' object list")
        return registry

    def world_id_for(
        self, project_root: str | os.PathLike[str], name: str | None = None
    ) -> str:
        root = _absolute(project_root)
        # A World is the project, so its durable id and folder are the project's
        # actual directory name. ``name`` remains a display-name concern only.
        return _world_id(root.name or "World")

    def list_worlds(self) -> dict[str, Any]:
        """List registered Worlds without creating a Home as a side effect."""
        path = self.worlds_path
        if not path.exists():
            worlds: list[dict[str, Any]] = []
        else:
            worlds = [dict(item) for item in self._read_registry(path)["worlds"]]
            worlds.sort(key=lambda item: (str(item.get("name", "")).casefold(), str(item.get("id", ""))))
        return {
            "home": str(self.home),
            "worlds_file": str(path),
            "count": len(worlds),
            "worlds": worlds,
        }

    def register_world(
        self,
        project_root: str | os.PathLike[str],
        *,
        name: str | None = None,
        import_archive: bool = True,
    ) -> dict[str, Any]:
        """Atomically register a project and optionally import its Markdown."""
        root = _absolute(project_root)
        if not root.is_dir():
            raise NotADirectoryError(str(root))
        display_name = (name or root.name).strip() or root.name or "World"
        initialized = self.initialize()
        registry = self._read_registry()
        worlds: list[dict[str, Any]] = registry["worlds"]
        root_key = _path_key(root)
        existing = next(
            (item for item in worlds if isinstance(item.get("root"), str) and _path_key(Path(item["root"])) == root_key),
            None,
        )
        now = _utc_now()
        created = existing is None
        updated = False
        world_id = self.world_id_for(root)
        collision = next(
            (
                item
                for item in worlds
                if item is not existing
                and str(item.get("id", "")).casefold() == world_id.casefold()
            ),
            None,
        )
        if collision is not None:
            raise HomeError(
                f"World {world_id!r} is already registered for {collision.get('root')}; "
                "project folder names must be unique within a HoloCore Home"
            )

        if existing is None:
            world_storage = self.projects / world_id
            record = {
                "id": world_id,
                "name": display_name,
                "root": str(root),
                "storage": str(world_storage),
                "registered_at": now,
                "updated_at": now,
                "app_version": self.app_version,
            }
            worlds.append(record)
        else:
            record = existing
            old_id = str(record.get("id", ""))
            old_storage = Path(str(record.get("storage") or self.projects / old_id))
            new_storage = self.projects / world_id
            if old_id != world_id or old_storage != new_storage:
                if old_storage.exists() and new_storage.exists() and old_storage.resolve() != new_storage.resolve():
                    raise HomeError(
                        f"cannot migrate World {old_id!r}: destination already exists at {new_storage}"
                    )
                moved = False
                if old_storage.exists() and old_storage.resolve() != new_storage.resolve():
                    new_storage.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(old_storage), str(new_storage))
                    moved = True
                database = new_storage / "Animus" / "animus.db"
                try:
                    if database.is_file() and old_id and old_id != world_id:
                        from .animus import Animus

                        Animus(database).rename_world(old_id, world_id, display_name)
                except Exception:
                    if moved and new_storage.exists() and not old_storage.exists():
                        shutil.move(str(new_storage), str(old_storage))
                    raise
            expected = {
                "id": world_id,
                "name": display_name,
                "root": str(root),
                "storage": str(new_storage),
                "app_version": self.app_version,
            }
            updated = any(record.get(key) != value for key, value in expected.items())
            if updated:
                record.update(expected)
                record["updated_at"] = now
            record.setdefault("registered_at", now)
            record.setdefault("updated_at", record["registered_at"])

        registry_written = created or updated
        if registry_written:
            registry["schema_version"] = SCHEMA_VERSION
            worlds.sort(key=lambda item: str(item.get("id", "")))
            _atomic_write(self.worlds_path, _json_bytes(registry))

        world_storage = Path(str(record["storage"]))
        world_archive = self.archive / "Worlds" / str(record["id"])
        runtime_archive = world_storage / "Archive"
        if runtime_archive.is_dir() and runtime_archive.resolve() != world_archive.resolve():
            if world_archive.exists():
                raise HomeError(
                    f"cannot simplify World archive: both {runtime_archive} and {world_archive} exist"
                )
            world_archive.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(runtime_archive), str(world_archive))
        Archive(world_archive).init_vault(scoped=True)
        for folder in (
            world_storage / "Atlas",
            world_storage / "Runtime",
            world_storage / "Inbox",
            self.home / "Animus" / "raw-chats" / str(record["id"]),
        ):
            folder.mkdir(parents=True, exist_ok=True)
        world_config = {
            "version": 5,
            "world": display_name,
            "world_id": str(record["id"]),
            "root": str(root),
            "home": str(self.home),
            "archive": str(world_archive),
            "state_dir": str(world_storage / "Runtime"),
            "animus": str(self.home / "Animus" / "animus.db"),
            "raw_chats": str(self.home / "Animus" / "raw-chats" / str(record["id"])),
            "atlas_graph": str(world_storage / "Atlas" / "graph.json"),
            "llm": {"provider": "local"},
        }
        _atomic_write(world_storage / "world.json", _json_bytes(world_config))
        self._refresh_home_index()

        import_report = (
            self.import_project_archive(root, str(record["id"]))
            if import_archive
            else {
                "attempted": False,
                "source": str(root / "Archive"),
                "destination": str(world_archive / "Imported"),
                "copied": [],
                "skipped": [],
                "conflicts": [],
                "errors": [],
            }
        )
        return {
            "registered": True,
            "created": created,
            "updated": updated,
            "changed": registry_written or bool(import_report["copied"]),
            "home": str(self.home),
            "worlds_file": str(self.worlds_path),
            "world": dict(record),
            "import": import_report,
            "home_initialized": initialized["initialized"],
        }

    register_project = register_world
    register = register_world

    def import_project_archive(
        self, project_root: str | os.PathLike[str], world_id: str | None = None
    ) -> dict[str, Any]:
        """Copy project-local Markdown into a World's Imported folder safely."""
        root = _absolute(project_root)
        source = root / "Archive"
        selected_id = world_id or self.world_id_for(root)
        _world_id(selected_id)
        registered = next(
            (item for item in self.list_worlds()["worlds"] if str(item.get("id")) == selected_id),
            None,
        )
        destination = self.archive / "Worlds" / selected_id / "Imported"
        report: dict[str, Any] = {
            "attempted": True,
            "source": str(source),
            "destination": str(destination),
            "copied": [],
            "skipped": [],
            "conflicts": [],
            "errors": [],
            "copied_count": 0,
            "skipped_count": 0,
            "conflicts_count": 0,
            "error_count": 0,
        }
        if not source.is_dir():
            report.update({"available": False, "reason": "project Archive not found"})
            return report
        if source.resolve() == self.archive.resolve():
            report.update({"available": True, "reason": "source is the Home Archive"})
            return report
        try:
            guide = (source / "README.md").read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            guide = ""
        if "system: holocore" in guide and ("operation: vault-init" in guide or "operation: home-init" in guide):
            report.update({"available": True, "reason": "generated HoloCore Archive skipped"})
            return report

        report["available"] = True
        destination.mkdir(parents=True, exist_ok=True)
        destination_resolved = destination.resolve()
        for current, directories, filenames in os.walk(source, followlinks=False):
            current_path = Path(current)
            directories[:] = sorted(
                item
                for item in directories
                if not (current_path / item).is_symlink()
                and (current_path / item).resolve() != destination_resolved
            )
            for filename in sorted(filenames):
                source_file = current_path / filename
                if source_file.suffix.casefold() != ".md" or source_file.is_symlink():
                    continue
                relative = source_file.relative_to(source)
                target = destination / relative
                relative_name = relative.as_posix()
                try:
                    if target.exists() or target.is_symlink():
                        if target.is_file() and not target.is_symlink() and filecmp.cmp(source_file, target, shallow=False):
                            report["skipped"].append(relative_name)
                        else:
                            report["conflicts"].append(relative_name)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        with source_file.open("rb") as input_stream, target.open("xb") as output_stream:
                            shutil.copyfileobj(input_stream, output_stream)
                        shutil.copystat(source_file, target, follow_symlinks=False)
                    except FileExistsError:
                        if target.is_file() and not target.is_symlink() and filecmp.cmp(source_file, target, shallow=False):
                            report["skipped"].append(relative_name)
                        else:
                            report["conflicts"].append(relative_name)
                        continue
                    except Exception:
                        target.unlink(missing_ok=True)
                        raise
                    report["copied"].append(relative_name)
                except OSError as exc:
                    report["errors"].append({"path": relative_name, "error": str(exc)})

        for key in ("copied", "skipped", "conflicts"):
            report[f"{key}_count"] = len(report[key])
        report["error_count"] = len(report["errors"])
        return report

    import_archive = import_project_archive


Home = HomeManager
HoloCoreHome = HomeManager


def initialize_home(
    home: str | os.PathLike[str] | None = None,
    *,
    config_home: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    return HomeManager(home, config_home=config_home).initialize()


def register_project(
    project_root: str | os.PathLike[str],
    *,
    home: str | os.PathLike[str] | None = None,
    config_home: str | os.PathLike[str] | None = None,
    name: str | None = None,
    import_archive: bool = True,
) -> dict[str, Any]:
    return HomeManager(home, config_home=config_home).register_world(
        project_root, name=name, import_archive=import_archive
    )


register_world = register_project


def list_worlds(
    home: str | os.PathLike[str] | None = None,
    *,
    config_home: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    return HomeManager(home, config_home=config_home).list_worlds()


__all__ = [
    "APP_VERSION",
    "CONFIG_HOME_ENV",
    "Home",
    "HomeDataError",
    "HomeError",
    "HomeManager",
    "HoloCoreHome",
    "initialize_home",
    "list_worlds",
    "register_project",
    "register_world",
]
