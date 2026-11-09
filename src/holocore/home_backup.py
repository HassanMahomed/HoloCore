"""Full-Home backups, portable exports, verified restores, and scheduled backups.

A backup is one zip holding everything HoloCore cannot rebuild: the project
registry, Knowledge notes, original chat transcripts, a consistent snapshot of
the Memories database, and each project's sources, inbox, and settings. Code
maps, the generated Obsidian vault view, Console exports, and AI connection
files are rebuilt automatically and are left out. The same file is the
portable export: it carries a manifest with a SHA-256 for every file and a
README explaining how to restore it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from . import __version__
from .folder_layout import HomeLayout, ensure_legacy_aliases
from .memories import SCHEMA_VERSION
from .storage import snapshot_database, validate_database

FORMAT = "holocore-home-backup"
FORMAT_VERSION = 1
DATABASE_NAME = "animus.db"
REGISTRY_FILES = ("worlds.json", "layout.json", "layout-migration.json")
PROJECT_PARTS = ("world.json", "Sources", "Inbox", "Runtime")
SKIPPED_SUFFIXES = (".bak", "-wal", "-shm", "-journal")
PRECOMPRESSED = {".pdf", ".docx", ".xlsx", ".pptx", ".zip", ".gz", ".7z", ".png", ".jpg", ".jpeg", ".gif",
                 ".webp", ".mp3", ".mp4", ".mov", ".m4a"}
CATEGORY_LABELS = {
    "registry": "Project registry",
    "memories": "Memories database",
    "knowledge": "Knowledge notes",
    "chat_history": "Original chat transcripts",
    "projects": "Project sources, inboxes, and settings",
}
NOT_INCLUDED = ("Code maps (rebuilt automatically)", "Obsidian vault view (rebuilt automatically)",
                "Console exports (rebuilt automatically)", "AI connection files (recreated by HoloCore Setup)",
                "Your project folders themselves")
TASK_NAME = "HoloCore Backup"
SETTINGS_FILE = "backups.json"
SAFETY_FOLDER = "Restore Safety Backups"
DAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
DEFAULT_SCHEDULE = {"enabled": False, "folder": None, "frequency": "weekly", "day": "SUN", "time": "20:00",
                    "keep": 4, "last_run": None}
_CHUNK = 1 << 20


class BackupError(RuntimeError):
    """A backup, restore, or schedule operation could not be completed safely."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_link(path: Path) -> bool:
    return path.is_symlink() or getattr(os.path, "isjunction", lambda _path: False)(path)


def _units(home: Path) -> list[str]:
    """Home-relative files and folders that a backup covers and a restore replaces."""
    layout = HomeLayout(home)
    units = [name for name in REGISTRY_FILES if (home / name).is_file()]
    for folder in (layout.knowledge, layout.chat_history):
        if folder.is_dir() and not _is_link(folder):
            units.append(folder.relative_to(home).as_posix())
    projects = home / "Projects"
    if projects.is_dir():
        for project in sorted(projects.iterdir()):
            if project.is_dir() and not _is_link(project):
                units.extend((project / part).relative_to(home).as_posix() for part in PROJECT_PARTS
                             if (project / part).exists() and not _is_link(project / part))
    return units


def _files(home: Path, units: list[str]):
    for unit in units:
        path = home / unit
        if path.is_file():
            yield path, unit
            continue
        for root, dirs, files in os.walk(path):
            dirs[:] = sorted(name for name in dirs if not _is_link(Path(root) / name))
            for name in sorted(files):
                file = Path(root) / name
                if not _is_link(file) and not name.endswith(SKIPPED_SUFFIXES):
                    yield file, file.relative_to(home).as_posix()


def _category(rel: str, knowledge: str, chat_history: str) -> str:
    if rel in REGISTRY_FILES:
        return "registry"
    if rel.startswith(knowledge + "/"):
        return "knowledge"
    if rel.startswith(chat_history + "/"):
        return "chat_history"
    return "projects"


def _add(archive: zipfile.ZipFile, source: Path, rel: str, category: str) -> dict[str, Any]:
    """Stream one file into the archive, hashing exactly the bytes written."""
    info = zipfile.ZipInfo.from_file(source, "home/" + rel, strict_timestamps=False)
    # Re-compressing already-compressed formats costs time and saves nothing.
    info.compress_type = zipfile.ZIP_STORED if source.suffix.lower() in PRECOMPRESSED else zipfile.ZIP_DEFLATED
    digest, size = hashlib.sha256(), 0
    with source.open("rb") as reader, archive.open(info, "w", force_zip64=True) as writer:
        for chunk in iter(lambda: reader.read(_CHUNK), b""):
            digest.update(chunk)
            writer.write(chunk)
            size += len(chunk)
    return {"path": rel, "bytes": size, "sha256": digest.hexdigest(), "category": category}


def _readme(manifest: dict[str, Any]) -> str:
    lines = [f"HoloCore backup made {manifest['created_at']} by HoloCore {manifest['holocore_version']}.", "",
             "Contains:"]
    lines += [f"- {CATEGORY_LABELS[key]}: {value['files']} file{'' if value['files'] == 1 else 's'}"
              for key, value in manifest["categories"].items()]
    lines += ["", "Not included:"] + [f"- {item}" for item in NOT_INCLUDED]
    lines += ["", "To restore, on this or another computer with HoloCore installed:",
              "- Open HoloCore Control Centre > Storage & Backups > Restore, or",
              '- run: holocore backup restore "<this file>" --yes', "",
              "Restoring replaces the Home's project registry, Knowledge, Memories, chat transcripts, and project",
              "sources. HoloCore first saves a safety backup of the current state. Project folders are not part of",
              "a backup; on a new computer keep them at the same paths or re-register them with HoloCore Setup.", ""]
    return "\n".join(lines)


def create_home_backup(home: str | Path, folder: str | Path, *, label: str = "manual") -> dict[str, Any]:
    """Write one verified, self-describing backup zip of the Home into ``folder``."""
    home = Path(home).expanduser().resolve()
    if not home.is_dir():
        raise BackupError(f"HoloCore Home not found: {home}")
    if not re.fullmatch(r"[a-z-]+", label):
        raise BackupError("Backup label must use lowercase letters and hyphens")
    folder = Path(folder).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    layout = HomeLayout(home)
    database = layout.memories / DATABASE_NAME
    database_rel = database.relative_to(home).as_posix()
    knowledge = layout.knowledge.relative_to(home).as_posix()
    chat_history = layout.chat_history.relative_to(home).as_posix()
    created = _now()

    def name_for(moment: datetime) -> Path:  # Millisecond stamps sort newest-last and never reuse a name.
        return folder / f"holocore-backup-{moment:%Y%m%dT%H%M%S}{moment.microsecond // 1000:03d}Z-{label}.zip"
    target = name_for(created)
    while target.exists():
        created += timedelta(milliseconds=1)
        target = name_for(created)
    partial = folder / f".{target.name}.partial"
    units, entries, skipped, schema = _units(home), [], [], None
    try:
        with tempfile.TemporaryDirectory(prefix="holocore-backup-") as work:
            with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                if database.is_file():
                    snapshot = Path(work) / DATABASE_NAME
                    schema = snapshot_database(database, snapshot)
                    entries.append(_add(archive, snapshot, database_rel, "memories"))
                for source, rel in _files(home, units):
                    try:
                        entries.append(_add(archive, source, rel, _category(rel, knowledge, chat_history)))
                    except FileNotFoundError:  # Removed while the backup was running.
                        continue
                    except PermissionError as exc:
                        skipped.append({"path": rel, "reason": str(exc)})
                categories: dict[str, dict[str, int]] = {}
                for entry in entries:
                    totals = categories.setdefault(entry["category"], {"files": 0, "bytes": 0})
                    totals["files"] += 1
                    totals["bytes"] += entry["bytes"]
                manifest = {
                    "format": FORMAT, "format_version": FORMAT_VERSION, "label": label,
                    "created_at": created.isoformat(), "holocore_version": __version__,
                    "layout_version": layout.version, "schema_version": schema,
                    "source_home": str(home), "database": database_rel if schema is not None else None,
                    "units": units, "categories": categories, "not_included": list(NOT_INCLUDED),
                    "skipped": skipped, "files": entries,
                }
                archive.writestr("manifest.json", json.dumps(manifest, indent=2))
                archive.writestr("README.txt", _readme(manifest))
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)
    return {"backup": str(target), "bytes": target.stat().st_size, "created_at": manifest["created_at"],
            "label": label, "files": len(entries), "categories": categories, "skipped": skipped,
            "schema_version": schema}


# Inspecting and restoring ----------------------------------------------------
def _manifest(archive: zipfile.ZipFile) -> dict[str, Any]:
    try:
        manifest = json.loads(archive.read("manifest.json"))
    except KeyError as exc:
        raise BackupError("This file is not a HoloCore backup: it has no manifest") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise BackupError("This file is not a HoloCore backup")
    return manifest


def _safe(rel: object) -> bool:
    text = str(rel or "")
    parts = PurePosixPath(text).parts
    return bool(text) and "\\" not in text and ":" not in text and not text.startswith("/") and ".." not in parts


def _verify(archive: zipfile.ZipFile, manifest: dict[str, Any]) -> list[str]:
    issues = [f"Unsafe path in backup: {unit}" for unit in manifest.get("units", []) if not _safe(unit)]
    names = set(archive.namelist())
    for entry in manifest.get("files", []):
        rel = entry.get("path")
        if not _safe(rel):
            issues.append(f"Unsafe path in backup: {rel}")
        elif "home/" + rel not in names:
            issues.append(f"Missing from backup: {rel}")
        else:
            digest = hashlib.sha256()
            with archive.open("home/" + rel) as reader:
                for chunk in iter(lambda: reader.read(_CHUNK), b""):
                    digest.update(chunk)
            if digest.hexdigest() != entry.get("sha256"):
                issues.append(f"Damaged file in backup: {rel}")
        if len(issues) >= 20:
            issues.append("Further problems not listed")
            break
    return issues


def _project_ids(raw: bytes | None) -> set[str]:
    try:
        return {str(row.get("id")) for row in json.loads(raw or b"{}").get("worlds", []) if row.get("id")}
    except (ValueError, AttributeError):
        return set()


def _latest_memory(database: Path) -> str | None:
    if not database.is_file():
        return None
    try:
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
            return db.execute("SELECT MAX(created_at) FROM shards").fetchone()[0]
    except sqlite3.Error:
        return None


def preview_home_restore(backup: str | Path, home: str | Path) -> dict[str, Any]:
    """Check a backup completely and describe what restoring it would change, without changing anything."""
    backup, home = Path(backup).expanduser().resolve(), Path(home).expanduser().resolve()
    result: dict[str, Any] = {"backup": str(backup), "destination": str(home), "restore_applied": False,
                              "valid": False, "issues": [], "warnings": []}
    try:
        with zipfile.ZipFile(backup) as archive:
            manifest = _manifest(archive)
            issues = []
            if int(manifest.get("format_version", 0)) > FORMAT_VERSION:
                issues.append("This backup was made by a newer HoloCore; update HoloCore first")
            schema = manifest.get("schema_version")
            if schema is not None and int(schema) > SCHEMA_VERSION:
                issues.append(f"Its Memories database uses schema v{schema}; this HoloCore supports up to v{SCHEMA_VERSION}")
            layout_now = HomeLayout(home).version if home.is_dir() else 2
            if int(manifest.get("layout_version", 2)) != layout_now:
                issues.append(f"It uses folder layout v{manifest.get('layout_version')}; this Home uses v{layout_now}. "
                              "Run holocore migrate-layout first")
            issues += _verify(archive, manifest)
            names = set(archive.namelist())
            in_backup = _project_ids(archive.read("home/worlds.json") if "home/worlds.json" in names else None)
    except (OSError, zipfile.BadZipFile, ValueError, BackupError) as exc:
        result["issues"] = [str(exc)]
        return result
    registry = home / "worlds.json"
    only_current = sorted(_project_ids(registry.read_bytes() if registry.is_file() else None) - in_backup)
    warnings = []
    if only_current:
        warnings.append(f"{len(only_current)} project(s) registered now are not in this backup and will be removed "
                        f"from HoloCore: {', '.join(only_current)}. The safety backup keeps their data.")
    latest = _latest_memory(HomeLayout(home).memories / DATABASE_NAME)
    if latest and latest > str(manifest.get("created_at", "")):
        warnings.append("Memories recorded after this backup was made will be replaced. The safety backup keeps them.")
    if manifest.get("source_home") and Path(manifest["source_home"]) != home:
        warnings.append(f"It was made from a Home at {manifest['source_home']}; saved paths will be updated to {home}.")
    result.update(valid=not issues, issues=issues, warnings=warnings, created_at=manifest.get("created_at"),
                  label=manifest.get("label"), holocore_version=manifest.get("holocore_version"),
                  schema_version=schema, source_home=manifest.get("source_home"),
                  categories={key: {**value, "label": CATEGORY_LABELS.get(key, key)}
                              for key, value in manifest.get("categories", {}).items()},
                  not_included=manifest.get("not_included", []), projects=sorted(in_backup),
                  projects_only_current=only_current)
    return result


def _remove_links(folder: Path) -> None:
    """Delete junctions and symlinks inside ``folder`` (never their targets) before moving or deleting it."""
    if not folder.is_dir() or _is_link(folder):
        return
    for root, dirs, _files_ in os.walk(folder):
        for name in list(dirs):
            path = Path(root) / name
            if _is_link(path):
                os.unlink(path) if path.is_symlink() else os.rmdir(path)
                dirs.remove(name)


def _rebase(value: Any, old: str, new: str) -> Any:
    if isinstance(value, dict):
        return {key: _rebase(item, old, new) for key, item in value.items()}
    if isinstance(value, list):
        return [_rebase(item, old, new) for item in value]
    if isinstance(value, str) and (value.casefold() == old.casefold()
                                   or value.casefold().startswith(old.casefold() + os.sep)):
        return new + value[len(old):]
    return value


def _rebase_registry(incoming: Path, manifest: dict[str, Any], home: Path) -> None:
    """Point saved Home paths at the destination when restoring into a Home at another location."""
    old = str(manifest.get("source_home") or "")
    if not old or Path(old) == home:
        return
    for path in [incoming / "worlds.json", *incoming.glob("Projects/*/world.json")]:
        if path.is_file():
            data = _rebase(json.loads(path.read_text(encoding="utf-8")), old, str(home))
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _restore_database(staged: Path, live: Path) -> None:
    """Replace the live database contents in one SQLite transaction; open connections stay valid."""
    live.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(staged)) as source, closing(sqlite3.connect(live, timeout=30)) as target:
        source.backup(target)  # pages=-1: a single step, so the destination changes atomically.
    validate_database(live)


def restore_home_backup(backup: str | Path, home: str | Path, *, safety_folder: str | Path | None = None,
                        _step: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Restore a verified backup all-or-nothing, after saving a safety backup of the current Home."""
    home = Path(home).expanduser().resolve()
    preview = preview_home_restore(backup, home)
    if not preview["valid"]:
        raise BackupError("This backup cannot be restored: " + "; ".join(preview["issues"]))
    home.mkdir(parents=True, exist_ok=True)
    safety_dir = Path(safety_folder or home / SAFETY_FOLDER)
    safety = create_home_backup(home, safety_dir, label="pre-restore")
    prune_backups(safety_dir, "pre-restore", keep=3)
    workspace = home / ".holocore-restore" / f"{_now():%Y%m%dT%H%M%S%f}"
    incoming, previous = workspace / "incoming", workspace / "previous"
    with zipfile.ZipFile(Path(backup).expanduser().resolve()) as archive:
        manifest = _manifest(archive)
        for entry in manifest["files"]:
            target = incoming / entry["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open("home/" + entry["path"]) as reader, target.open("wb") as writer:
                shutil.copyfileobj(reader, writer, _CHUNK)
    staged_db = incoming / manifest["database"] if manifest.get("database") else None
    if staged_db is not None:
        validate_database(staged_db)
    _rebase_registry(incoming, manifest, home)
    live_db = HomeLayout(home).memories / DATABASE_NAME
    previous_db = workspace / "previous-memories.db"
    units = sorted(set(manifest["units"]) | set(_units(home)))
    moved: list[tuple[str, str]] = []
    database_replaced = False
    try:
        for unit in units:
            live, old, new = home / unit, previous / unit, incoming / unit
            if live.exists():
                _remove_links(live)
                old.parent.mkdir(parents=True, exist_ok=True)
                os.replace(live, old)
                moved.append(("out", unit))
            if new.exists():
                live.parent.mkdir(parents=True, exist_ok=True)
                os.replace(new, live)
                moved.append(("in", unit))
            if _step:
                _step(unit)
        if staged_db is not None:
            if live_db.is_file():
                snapshot_database(live_db, previous_db)
            database_replaced = True
            _restore_database(staged_db, live_db)
            if _step:
                _step("database")
    except Exception as exc:
        for direction, unit in reversed(moved):
            if direction == "in":
                os.replace(home / unit, incoming / unit)
            else:
                os.replace(previous / unit, home / unit)
        if database_replaced and previous_db.is_file():
            _restore_database(previous_db, live_db)
        _relink(home)
        raise BackupError(f"Restore stopped and your previous data was put back ({exc}). "
                          f"A safety backup is also at {safety['backup']}") from exc
    _relink(home)
    _remove_links(previous)
    shutil.rmtree(workspace, ignore_errors=True)
    return {"restored": True, "backup": preview["backup"], "destination": str(home),
            "created_at": preview["created_at"], "categories": preview["categories"],
            "safety_backup": safety["backup"], "warnings": preview["warnings"]}


def _relink(home: Path) -> None:
    try:
        ensure_legacy_aliases(home)
    except Exception:  # Aliases only serve old v1 clients; never fail a restore over them.
        pass


def prune_backups(folder: str | Path, label: str, keep: int) -> list[str]:
    """Delete all but the newest ``keep`` backups with this label; returns what was removed."""
    files = sorted(Path(folder).glob(f"holocore-backup-*-{label}.zip"), key=lambda path: path.name, reverse=True)
    removed = []
    for path in files[max(1, int(keep)):]:
        path.unlink(missing_ok=True)
        removed.append(str(path))
    return removed


# Scheduled backups -----------------------------------------------------------
def _settings_path(home: Path) -> Path:
    return Path(home) / "Runtime" / SETTINGS_FILE


def schedule_status(home: str | Path) -> dict[str, Any]:
    try:
        saved = json.loads(_settings_path(Path(home)).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    return {**DEFAULT_SCHEDULE, **(saved if isinstance(saved, dict) else {}), "task": TASK_NAME}


def _save_settings(home: Path, settings: dict[str, Any]) -> None:
    path = _settings_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps({key: settings[key] for key in DEFAULT_SCHEDULE}, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def scheduled_command(home: Path) -> str:
    """A windowed command for Task Scheduler, so a scheduled backup never opens a console."""
    executable = Path(sys.executable)
    setup = executable.with_name("HoloCore Setup.exe")
    if setup.is_file():
        prefix = f'"{setup}"'
    elif getattr(sys, "frozen", False):
        prefix = f'"{executable}"'
    else:
        windowed = executable.with_name("pythonw.exe")
        prefix = f'"{windowed if windowed.is_file() else executable}" -m holocore.setup_app'
    return f'{prefix} --backup-scheduled --home "{home}"'


def configure_schedule(home: str | Path, *, folder: str | Path, frequency: str = "weekly", day: str = "SUN",
                       time: str = "20:00", keep: int = 4, runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    """Save the schedule and create (or replace) the Windows scheduled task."""
    home = Path(home).expanduser().resolve()
    frequency, day = frequency.lower(), day.upper()
    if frequency not in ("daily", "weekly"):
        raise BackupError("Choose a daily or weekly schedule")
    if day not in DAYS:
        raise BackupError("Choose a day: " + ", ".join(DAYS))
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", time):
        raise BackupError("Use a 24-hour time such as 20:00")
    if not 1 <= int(keep) <= 52:
        raise BackupError("Keep between 1 and 52 backups")
    folder = Path(folder).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    command = scheduled_command(home)
    if len(command) > 261:
        raise BackupError("The HoloCore Home path is too long for Windows Task Scheduler")
    arguments = ["schtasks", "/Create", "/TN", TASK_NAME, "/SC", frequency.upper()]
    arguments += ["/D", day] if frequency == "weekly" else []
    arguments += ["/ST", time, "/TR", command, "/F"]
    try:
        completed = runner(arguments, capture_output=True, text=True, check=False,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except FileNotFoundError as exc:
        raise BackupError("Scheduled backups need Windows Task Scheduler") from exc
    if completed.returncode:
        raise BackupError("Windows could not create the scheduled backup: "
                          + (completed.stderr or completed.stdout or "unknown error").strip())
    settings = schedule_status(home)
    settings.update(enabled=True, folder=str(folder), frequency=frequency, day=day, time=time, keep=int(keep))
    _save_settings(home, settings)
    return schedule_status(home)


def disable_schedule(home: str | Path, *, runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    home = Path(home).expanduser().resolve()
    try:
        runner(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], capture_output=True, text=True, check=False,
               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except FileNotFoundError:
        pass
    settings = schedule_status(home)
    settings["enabled"] = False
    _save_settings(home, settings)
    return schedule_status(home)


def run_scheduled_backup(home: str | Path) -> dict[str, Any]:
    """Run one scheduled backup, keep the newest N, and record the outcome for the Control Centre."""
    home = Path(home).expanduser().resolve()
    settings = schedule_status(home)
    started = _now().isoformat()
    try:
        if not settings.get("folder"):
            raise BackupError("No backup folder is set")
        result = create_home_backup(home, settings["folder"], label="scheduled")
        removed = prune_backups(settings["folder"], "scheduled", settings["keep"])
        outcome = {"result": "succeeded", "backup": result["backup"], "bytes": result["bytes"],
                   "removed": len(removed), "skipped": len(result["skipped"])}
    except Exception as exc:  # Recorded for the user; a scheduled task has no one watching it.
        outcome = {"result": "failed", "error": str(exc)}
    settings["last_run"] = {"started_at": started, "finished_at": _now().isoformat(), **outcome}
    _save_settings(home, settings)
    return settings["last_run"]


__all__ = ["BackupError", "configure_schedule", "create_home_backup", "disable_schedule", "preview_home_restore",
           "prune_backups", "restore_home_backup", "run_scheduled_backup", "schedule_status", "scheduled_command"]
