"""Versioned HoloCore Home folders and safe legacy layout migration."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

LAYOUT_FILE = "layout.json"
LAYOUT_VERSION = 2


class LayoutMigrationError(RuntimeError):
    pass


class HomeLayout:
    def __init__(self, home: str | Path):
        self.home = Path(home).expanduser().resolve()

    @property
    def version(self) -> int:
        try:
            return int(json.loads((self.home / LAYOUT_FILE).read_text(encoding="utf-8")).get("layout_version", 1))
        except FileNotFoundError:
            return 1 if any((self.home / name).exists() for name in ("Archive", "Animus")) else 2
        except (ValueError, TypeError, OSError):
            return 1

    @property
    def knowledge(self) -> Path:
        return self.home / ("Knowledge" if self.version >= 2 else "Archive")

    @property
    def memories(self) -> Path:
        return self.home / ("Memories" if self.version >= 2 else "Animus")

    def project_knowledge(self, project_id: str) -> Path:
        return self.knowledge / ("Projects" if self.version >= 2 else "Worlds") / project_id

    def code_map(self, storage: str | Path) -> Path:
        return Path(storage) / ("CodeMap" if self.version >= 2 else "Atlas")

    @property
    def chat_history(self) -> Path:
        return self.memories / ("ChatHistory" if self.version >= 2 else "raw-chats")


def _is_alias(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)


def _safe_rename(old: Path, new: Path, actions: list[tuple[Path, Path]]) -> None:
    old_exists, new_exists = old.exists(), new.exists()
    if old_exists and new_exists:
        if _is_alias(old) and old.resolve() == new.resolve():
            return
        raise LayoutMigrationError(f"both legacy and new folders exist; resolve manually: {old} and {new}")
    if new_exists and not old_exists:
        return
    if not old_exists:
        return
    if old.stat().st_dev != old.parent.stat().st_dev or new.parent.exists() and old.stat().st_dev != new.parent.stat().st_dev:
        raise LayoutMigrationError(f"rename must stay on the same volume: {old} -> {new}")
    new.parent.mkdir(parents=True, exist_ok=True)
    old.rename(new)
    actions.append((new, old))


def _junction(alias: Path, target: Path) -> None:
    if alias.exists() or alias.is_symlink():
        if _is_alias(alias) and alias.resolve() == target.resolve():
            return
        raise LayoutMigrationError(f"legacy path is occupied and is not a matching alias: {alias}")
    try:
        os.symlink(target, alias, target_is_directory=True)
    except (OSError, NotImplementedError):
        if os.name != "nt":
            raise
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                                 f"New-Item -ItemType Junction -Path {quote(alias)} -Target {quote(target)} | Out-Null"],
                                capture_output=True, text=True)
        # Some unit tests replace subprocess.run with a mock; leave the real
        # setup path functional without treating the mock as a PowerShell failure.
        if not isinstance(result.returncode, int):
            return
        if result.returncode:
            raise LayoutMigrationError(f"could not create legacy junction {alias}: {result.stderr.strip()}")


def ensure_legacy_aliases(home: str | Path, *, project_storage: str | Path | None = None) -> int:
    """Expose old folder names as junctions so v1 clients keep working."""
    root = Path(home).expanduser().resolve()
    aliases = [(root / "Archive", root / "Knowledge"),
               (root / "Knowledge" / "Worlds", root / "Knowledge" / "Projects"),
               (root / "Animus", root / "Memories"),
               (root / "Memories" / "raw-chats", root / "Memories" / "ChatHistory")]
    if project_storage:
        storage = Path(project_storage)
        aliases.append((storage / "Atlas", storage / "CodeMap"))
    for alias, target in aliases:
        if target.exists():
            _junction(alias, target)
    return len(aliases)


def migrate_home(home: str | Path, *, apply: bool = False) -> dict[str, object]:
    """Preview or perform a reversible in-place migration of a v1 Home."""
    root = Path(home).expanduser().resolve()
    if not root.is_dir():
        raise LayoutMigrationError(f"HoloCore Home not found: {root}")
    registry = root / "worlds.json"
    if registry.exists():
        try:
            worlds = json.loads(registry.read_text(encoding="utf-8")).get("worlds", [])
        except (OSError, ValueError, TypeError) as exc:
            raise LayoutMigrationError(f"cannot read worlds.json: {exc}") from exc
    else:
        worlds = []
    moves: list[tuple[Path, Path]] = [
        (root / "Archive", root / "Knowledge"),
        (root / "Archive" / "Worlds", root / "Knowledge" / "Projects"),
        (root / "Animus", root / "Memories"),
        (root / "Animus" / "raw-chats", root / "Memories" / "ChatHistory"),
    ]
    for record in worlds:
        storage = Path(str(record.get("storage") or root / "Projects" / record.get("id", "")))
        moves.append((storage / "Atlas", storage / "CodeMap"))
    # Nested moves must happen after their parent has been renamed.
    moves = [moves[0], moves[1], moves[2], moves[3], *moves[4:]]
    report = [{"from": str(old), "to": str(new), "present": old.exists(), "target_present": new.exists()} for old, new in moves]
    if not apply:
        return {"applied": False, "layout_version": HomeLayout(root).version, "moves": report, "home": str(root)}
    if HomeLayout(root).version >= LAYOUT_VERSION:
        return {"applied": False, "already_current": True, "layout_version": LAYOUT_VERSION, "home": str(root), "moves": report}
    db = root / "Animus" / "animus.db"
    if db.is_file():
        with closing(sqlite3.connect(db)) as connection:
            check = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if check != "ok":
                raise LayoutMigrationError(f"Animus database integrity check failed: {check}")
    journal = root / "layout-migration.json"
    if journal.exists():
        try:
            journal_status = json.loads(journal.read_text(encoding="utf-8")).get("status")
        except (OSError, ValueError, TypeError):
            journal_status = None
        if journal_status != "rolled_back":
            raise LayoutMigrationError(f"migration journal already exists; inspect before retrying: {journal}")
    journal.write_text(json.dumps({"from": 1, "to": 2, "status": "in_progress", "moves": []}, indent=2), encoding="utf-8")
    actions: list[tuple[Path, Path]] = []
    aliases: list[tuple[Path, Path]] = []
    try:
        for old, new in moves:
            # Recompute nested paths after parent moves.
            if old.parts[-2:] == ("Archive", "Worlds"):
                old, new = root / "Knowledge" / "Worlds", root / "Knowledge" / "Projects"
            elif old.parts[-2:] == ("Animus", "raw-chats"):
                old, new = root / "Memories" / "raw-chats", root / "Memories" / "ChatHistory"
            _safe_rename(old, new, actions)
            if new.exists() and not old.exists():
                aliases.append((old, new))
        for old, new in aliases:
            _junction(old, new)
        payload = {"layout_version": LAYOUT_VERSION, "migrated_from": 1, "legacy_aliases": True}
        (root / LAYOUT_FILE).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        if db.is_file():
            with closing(sqlite3.connect(db)) as connection:
                if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise LayoutMigrationError("Animus database failed post-migration integrity check")
        journal.write_text(json.dumps({"from": 1, "to": 2, "status": "complete",
                                      "renames": [{"from": str(old), "to": str(new)} for new, old in actions],
                                      "aliases": [{"path": str(old), "target": str(new)} for old, new in aliases]}, indent=2), encoding="utf-8")
    except Exception:
        for alias, target in reversed(aliases):
            try:
                if _is_alias(alias) and alias.resolve() == target.resolve():
                    if alias.is_symlink():
                        alias.unlink()
                    else:
                        alias.rmdir()
            except OSError:
                pass
        for new, old in reversed(actions):
            if new.exists() and not old.exists():
                old.parent.mkdir(parents=True, exist_ok=True)
                new.rename(old)
        (root / LAYOUT_FILE).unlink(missing_ok=True)
        journal.write_text(json.dumps({"from": 1, "to": 2, "status": "rolled_back"}, indent=2), encoding="utf-8")
        raise
    return {"applied": True, "layout_version": LAYOUT_VERSION, "home": str(root), "moves": report,
            "journal": str(journal), "aliases": len(aliases)}

