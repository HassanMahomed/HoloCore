"""Automatic refresh of the shared HoloCore Obsidian vault."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .home import HomeManager
from .lifecycle import sync_all
from .obsidian_vault import build_obsidian_vault


TASK_NAME = "HoloCore Obsidian Vault Refresh"
DEFAULT_INTERVAL = 30.0
_IGNORED_DIRS = frozenset({".git", ".holocore", ".obsidian", "__pycache__", "build", "dist", "graphify-out", "holocore-out", "node_modules", "raw", "Imported", "imported"})


def _source_fingerprint(home: Path) -> str:
    """Fingerprint World source files and the registry, excluding generated state."""
    digest = hashlib.sha256()
    registry = home / "worlds.json"
    paths: list[Path] = [registry] if registry.is_file() else []
    if registry.is_file():
        try:
            worlds = __import__("json").loads(registry.read_text(encoding="utf-8")).get("worlds", [])
        except (OSError, ValueError):
            worlds = []
        for world in worlds:
            if not isinstance(world, dict) or not isinstance(world.get("root"), str):
                continue
            root = Path(world["root"]).expanduser()
            if not root.is_dir():
                continue
            for path in root.rglob("*"):
                if not path.is_file() or any(part in _IGNORED_DIRS for part in path.relative_to(root).parts):
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                paths.append(path)
    for path in sorted(set(paths), key=lambda item: os.fspath(item).casefold()):
        try:
            stat = path.stat()
            digest.update(os.fspath(path).casefold().encode())
            digest.update(f"{stat.st_mtime_ns}:{stat.st_size}".encode())
        except OSError:
            digest.update((os.fspath(path) + ":missing").encode())
    return digest.hexdigest()


def refresh_home(home: str | Path) -> dict[str, Any]:
    """Reconcile Worlds and rebuild the shared Obsidian view once."""
    home_path = Path(home).expanduser().resolve()
    reconciliation = sync_all(home_path)
    vault = reconciliation.get("obsidian_vault") or build_obsidian_vault(home_path)
    return {"home": str(home_path), "reconciliation": reconciliation, "vault": vault}


def watch_home(home: str | Path, *, interval: float = DEFAULT_INTERVAL, once: bool = False, max_cycles: int | None = None) -> int:
    """Refresh after source or World-registry changes."""
    home_path = Path(home).expanduser().resolve()
    previous: str | None = None
    cycles = 0
    while True:
        current = _source_fingerprint(home_path)
        if previous is None or current != previous:
            refresh_home(home_path)
            previous = _source_fingerprint(home_path)
        cycles += 1
        if once or (max_cycles is not None and cycles >= max_cycles):
            return cycles
        time.sleep(max(0.5, interval))


def install_windows_task(home: str | Path, *, interval: float = DEFAULT_INTERVAL) -> dict[str, Any]:
    """Install a logon-triggered Windows watcher; idempotent and replaceable.

    Task Scheduler is preferred. Some locked-down Windows profiles deny task
    creation, so a per-user Startup launcher is the safe no-admin fallback.
    """
    if os.name != "nt":
        raise RuntimeError("Automatic HoloCore watch installation is currently supported on Windows only.")
    executable = shutil.which("holocore") or sys.executable
    if executable == sys.executable:
        command = f'"{executable}" -m holocore.cli watch --home "{Path(home).resolve()}" --interval {interval:g}'
    else:
        command = f'"{executable}" watch --home "{Path(home).resolve()}" --interval {interval:g}'
    completed = subprocess.run(["schtasks", "/Create", "/TN", TASK_NAME, "/SC", "ONLOGON", "/TR", command, "/F"], capture_output=True, text=True, check=False)
    if completed.returncode == 0:
        return {"installed": True, "mechanism": "task-scheduler", "task": TASK_NAME, "home": str(Path(home).resolve()), "interval": interval, "command": command}

    startup = Path(os.environ.get("APPDATA", Path.home())) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    launcher = startup / f"{TASK_NAME}.vbs"
    vbs_command = command.replace('"', '""')
    content = f'CreateObject("WScript.Shell").Run "{vbs_command}", 0, False\n'
    startup.mkdir(parents=True, exist_ok=True)
    launcher.write_text(content, encoding="utf-8")
    return {
        "installed": True,
        "mechanism": "user-startup",
        "task": TASK_NAME,
        "home": str(Path(home).resolve()),
        "interval": interval,
        "command": command,
        "fallback_reason": (completed.stderr or completed.stdout or "Task Scheduler was unavailable").strip(),
        "launcher": str(launcher),
    }


def uninstall_windows_task() -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("Automatic HoloCore watch installation is currently supported on Windows only.")
    completed = subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], capture_output=True, text=True, check=False)
    launcher = Path(os.environ.get("APPDATA", Path.home())) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / f"{TASK_NAME}.vbs"
    removed_launcher = launcher.exists()
    launcher.unlink(missing_ok=True)
    return {"installed": False, "task": TASK_NAME, "removed": completed.returncode == 0 or removed_launcher, "output": (completed.stdout or completed.stderr).strip(), "launcher": str(launcher) if removed_launcher else None}


__all__ = ["DEFAULT_INTERVAL", "TASK_NAME", "install_windows_task", "refresh_home", "uninstall_windows_task", "watch_home"]
