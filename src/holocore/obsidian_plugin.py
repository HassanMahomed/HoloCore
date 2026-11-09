"""Install the HoloCore board plugin into Obsidian vaults the user chooses.

The plugin only reads HoloCore data by running ``holocore board``; it never opens the
Memories database itself. Installing copies three files into
``<vault>/.obsidian/plugins/holocore-board`` and enables the plugin for that vault.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

PLUGIN_ID = "holocore-board"
PLUGIN_FILES = ("manifest.json", "main.js", "styles.css")
SOURCE = Path(__file__).with_name("obsidian_plugin")


class PluginError(RuntimeError):
    pass


def _obsidian_config() -> Path:
    return Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "obsidian" / "obsidian.json"


def _plugin_folder(vault: Path) -> Path:
    return vault / ".obsidian" / "plugins" / PLUGIN_ID


def obsidian_vaults() -> list[dict]:
    """Vaults Obsidian itself has opened on this computer, read from its own vault list."""
    try:
        listed = json.loads(_obsidian_config().read_text(encoding="utf-8")).get("vaults", {})
    except (OSError, ValueError, AttributeError):
        return []
    rows = []
    for item in listed.values() if isinstance(listed, dict) else []:
        path = Path(str(item.get("path") or "")) if isinstance(item, dict) else None
        if not path or not str(path):
            continue
        rows.append({"path": str(path), "name": path.name, "available": (path / ".obsidian").is_dir(),
                     "installed": (_plugin_folder(path) / "main.js").is_file()})
    return sorted(rows, key=lambda row: row["name"].casefold())


def engine_command() -> list[str]:
    """The command the plugin runs to read HoloCore data."""
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable)
        if executable.name.casefold() == "holocore.exe":
            return [str(executable)]
        # Setup may run from Downloads; the engine it installs lives in the standard install folder.
        installed = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Programs" / "HoloCore" / "holocore.exe"
        return [str(installed if installed.is_file() else executable.with_name("holocore.exe"))]
    return [sys.executable, "-m", "holocore"]


def _vault(vault: str | Path) -> Path:
    path = Path(vault).expanduser().resolve()
    if not (path / ".obsidian").is_dir():
        raise PluginError(f"Not an Obsidian vault (no .obsidian folder): {path}")
    return path


def _community_plugins(vault: Path) -> tuple[Path, list[str]]:
    path = vault / ".obsidian" / "community-plugins.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    except ValueError as exc:
        raise PluginError(f"Obsidian's plugin list could not be read: {path}") from exc
    return path, [str(item) for item in value] if isinstance(value, list) else []


def _write_json(path: Path, value) -> None:
    temporary = path.with_suffix(path.suffix + ".holocore-new")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def install_plugin(vault: str | Path, command: list[str] | None = None) -> dict:
    """Copy the plugin into one vault and enable it. Settings already chosen in Obsidian are kept."""
    path = _vault(vault)
    plugins_path, enabled = _community_plugins(path)  # Refuse before copying anything.
    folder = _plugin_folder(path)
    folder.mkdir(parents=True, exist_ok=True)
    for name in PLUGIN_FILES:
        shutil.copyfile(SOURCE / name, folder / name)
    settings_path = folder / "data.json"
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.is_file() else {}
    except ValueError:
        settings = {}
    settings["command"] = command or engine_command()
    _write_json(settings_path, settings)
    newly_enabled = PLUGIN_ID not in enabled
    if newly_enabled:
        _write_json(plugins_path, enabled + [PLUGIN_ID])
    version = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))["version"]
    return {"installed": True, "vault": str(path), "plugin_folder": str(folder), "version": version,
            "command": settings["command"], "enabled": True, "newly_enabled": newly_enabled,
            "next_step": "Restart Obsidian or reload the vault, then click the HoloCore board icon in the left ribbon."}


def remove_from_all_vaults() -> dict:
    """Used by the HoloCore uninstaller: remove the plugin wherever Obsidian lists it as installed."""
    removed, failed = [], []
    for row in obsidian_vaults():
        if not row["installed"]:
            continue
        try:
            uninstall_plugin(row["path"])
            removed.append(row["path"])
        except (OSError, PluginError) as exc:
            failed.append({"vault": row["path"], "error": str(exc)})
    return {"obsidian_plugin_removed": removed, "obsidian_plugin_failed": failed}


def uninstall_plugin(vault: str | Path) -> dict:
    """Remove the plugin folder and its entry from the vault's enabled plugins."""
    path = _vault(vault)
    folder = _plugin_folder(path)
    removed = folder.is_dir()
    if removed:
        shutil.rmtree(folder)
    plugins_path, enabled = _community_plugins(path)
    if PLUGIN_ID in enabled:
        _write_json(plugins_path, [item for item in enabled if item != PLUGIN_ID])
    return {"uninstalled": removed, "vault": str(path)}
