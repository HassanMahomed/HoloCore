"""Lightweight, session-driven HoloCore update checks."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


RELEASE_API = "https://api.github.com/repos/HassanMahomed/HoloCore/releases/latest"
CHECK_INTERVAL = timedelta(days=1)
RETRY_INTERVAL = timedelta(hours=6)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _state_path(home: str | Path) -> Path:
    return Path(home).resolve() / "Runtime" / "auto-update.json"


def _read(home: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(_state_path(home).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _write(home: str | Path, value: dict[str, Any]) -> None:
    path = _state_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _time(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _version(value: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", str(value).split("-", 1)[0])
    return tuple(int(item) for item in numbers[:4]) or (0,)


def configure(home: str | Path, *, enabled: bool) -> dict[str, Any]:
    state = _read(home)
    state["enabled"] = bool(enabled)
    state["updated_at"] = _now().isoformat()
    _write(home, state)
    return status(home)


def status(home: str | Path) -> dict[str, Any]:
    state = _read(home)
    return {
        "enabled": bool(state.get("enabled", False)),
        "mechanism": "session-driven",
        "check_interval_hours": 24,
        "latest_version": state.get("latest_version"),
        "update_available": bool(state.get("update_available", False)),
        "last_check": state.get("last_check"),
        "last_attempt": state.get("last_attempt"),
        "last_error": state.get("last_error"),
        "update_queued": bool(state.get("update_queued", False)),
        "state_file": str(_state_path(home)),
    }


def _launch_update(home: str | Path) -> bool:
    """Launch a short-lived updater after this hook process exits."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if getattr(sys, "frozen", False):
        installer = Path(sys.executable).resolve().parent / "install.ps1"
        if not installer.is_file():
            return False
        command = (
            f"Wait-Process -Id {os.getpid()} -ErrorAction SilentlyContinue; "
            f"& '{installer}' -Yes -UpgradeOnly -Home '{Path(home).resolve()}'"
        )
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass", "-Command", command],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags,
        )
        return True
    uv = shutil.which("uv")
    if not uv:
        return False
    command = f"Wait-Process -Id {os.getpid()} -ErrorAction SilentlyContinue; & '{uv}' tool upgrade holocore"
    subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", command],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags,
    )
    return True


def maybe_check(home: str | Path, *, current_version: str, session_ending: bool = False, force: bool = False) -> dict[str, Any]:
    """Check at most daily during real HoloCore activity; offline failures are quiet."""
    state = _read(home)
    if not force and not state.get("enabled", False):
        return {**status(home), "checked": False, "reason": "disabled"}
    now = _now()
    last_check = _time(state.get("last_check"))
    last_attempt = _time(state.get("last_attempt"))
    due = force or (
        (last_check is None or now - last_check >= CHECK_INTERVAL)
        and (last_attempt is None or now - last_attempt >= RETRY_INTERVAL)
    )
    if not due:
        return {**status(home), "checked": False, "reason": "not-due"}
    state["last_attempt"] = now.isoformat()
    try:
        request = urllib.request.Request(RELEASE_API, headers={"Accept": "application/vnd.github+json", "User-Agent": "HoloCore"})
        with urllib.request.urlopen(request, timeout=2.5) as response:
            payload = json.loads(response.read(256_000).decode("utf-8"))
        latest = str(payload.get("tag_name") or "").lstrip("v")
        if not latest:
            raise ValueError("release response did not contain tag_name")
        available = _version(latest) > _version(current_version)
        state.update({"last_check": now.isoformat(), "latest_version": latest,
                      "current_version": current_version, "update_available": available,
                      "last_error": None})
        if available and state.get("enabled") and session_ending and not state.get("update_queued"):
            state["update_queued"] = _launch_update(home)
        _write(home, state)
        return {**status(home), "checked": True}
    except Exception as exc:
        state["last_error"] = type(exc).__name__
        _write(home, state)
        return {**status(home), "checked": False, "reason": "offline-or-unavailable"}


__all__ = ["CHECK_INTERVAL", "RELEASE_API", "configure", "maybe_check", "status"]
