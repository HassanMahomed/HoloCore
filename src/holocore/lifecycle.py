"""HoloCore upgrade and registered-World reconciliation workflows."""

from __future__ import annotations

import shutil
import subprocess
import os
import sys
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path
from typing import Any

from .home import HomeManager
from .install import bootstrap_project


REPOSITORY = "https://github.com/HassanMahomed/HoloCore.git"


def installation_check() -> dict[str, Any]:
    """Report the installed package and safe upgrade path before setup runs."""
    try:
        installed = version("holocore")
    except PackageNotFoundError:
        installed = "source-checkout"
    uv = shutil.which("uv")
    return {"installed_version": installed, "uv_available": bool(uv), "upgrade_command": "holocore update", "uninstall_command": "uv tool uninstall holocore"}


def uninstall_tool() -> dict[str, Any]:
    """Remove only the installed CLI; never delete project or Home data."""
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv is required to uninstall the HoloCore tool.")
    command = [uv, "tool", "uninstall", "holocore"]
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    return {"uninstalled": True, "command": command, "output": completed.stdout.strip(), "data_preserved": True}


def sync_all(home: Path | None = None, *, platforms: list[str] | None = None) -> dict[str, Any]:
    manager = HomeManager(home)
    listing = manager.list_worlds()
    results: list[dict[str, Any]] = []
    for world in listing["worlds"]:
        root = Path(str(world.get("root", ""))).expanduser()
        if not root.is_dir():
            results.append({"id": world.get("id"), "root": str(root), "updated": False, "reason": "missing-world"})
            continue
        try:
            report = bootstrap_project(root, init_git=False, home=manager.home, platforms=platforms)
            from .engine import HoloCoreEngine

            atlas = HoloCoreEngine(root).ensure_atlas()
            results.append({
                "id": report.world_id,
                "root": str(root.resolve()),
                "updated": True,
                "files_changed": len(report.created) + len(report.updated),
                "atlas": atlas,
            })
        except Exception as exc:
            results.append({"id": world.get("id"), "root": str(root), "updated": False, "reason": str(exc)})
    from .obsidian_vault import build_obsidian_vault

    vault = build_obsidian_vault(manager.home)
    return {
        "home": str(manager.home),
        "worlds": results,
        "count": len(results),
        "updated": sum(1 for item in results if item["updated"]),
        "failed": sum(1 for item in results if not item["updated"]),
        "obsidian_vault": vault,
    }


def update_install(home: Path | None = None, *, repository: str = REPOSITORY, reconcile: bool = True) -> dict[str, Any]:
    if getattr(sys, "frozen", False):
        installer = Path(sys.executable).resolve().parent / "install.ps1"
        if not installer.is_file():
            raise RuntimeError(
                f"The Windows updater is missing: {installer}. Run the one-line installer again."
            )
        selected_home = HomeManager(home).home
        command_text = (
            f"Wait-Process -Id {os.getpid()} -ErrorAction SilentlyContinue; "
            f"& '{installer}' -Yes -UpgradeOnly -Home '{selected_home}'"
        )
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass", "-Command", command_text],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return {
            "updated": False,
            "update_queued": True,
            "command": [str(installer), "-Yes", "-UpgradeOnly", "-Home", str(selected_home)],
            "installer_output": "Update will begin after this HoloCore process exits.",
            "reconciliation": None,
        }
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv is required for self-update. Install uv, then run `holocore update` again.")
    # `uv tool upgrade` avoids Windows file-lock failures caused by trying to
    # replace the currently running holocore.exe with `tool install --force`.
    command = [uv, "tool", "upgrade", "holocore"]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if getattr(completed, "returncode", 0) != 0:
        fallback = [uv, "tool", "install", "--force", f"git+{repository}"]
        completed = subprocess.run(fallback, check=False, capture_output=True, text=True)
        command = fallback
    if getattr(completed, "returncode", 0) != 0:
        detail = (completed.stderr or completed.stdout or "uv returned no diagnostic output").strip()
        if "os error 32" in detail.lower() or "being used by another process" in detail.lower():
            detail += " Close Claude Code, Codex, other MCP clients, and any running holocore processes, then run `uv tool upgrade holocore` from a fresh PowerShell window."
        raise RuntimeError(f"HoloCore update failed (exit {completed.returncode}): {detail}")
    result = {
        "updated": True,
        "command": command,
        "installer_output": completed.stdout.strip(),
        "reconciliation": sync_all(home) if reconcile else None,
    }
    return result


__all__ = ["REPOSITORY", "installation_check", "sync_all", "uninstall_tool", "update_install"]
