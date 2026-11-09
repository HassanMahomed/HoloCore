from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from holocore import obsidian_plugin
from holocore.obsidian_plugin import PLUGIN_ID, PluginError, engine_command, install_plugin, obsidian_vaults, uninstall_plugin


def _vault(tmp_path, name="Vault", plugins=None):
    vault = tmp_path / name
    (vault / ".obsidian").mkdir(parents=True)
    if plugins is not None:
        (vault / ".obsidian" / "community-plugins.json").write_text(json.dumps(plugins), encoding="utf-8")
    return vault


def test_plugin_files_are_packaged_and_consistent():
    manifest = json.loads((obsidian_plugin.SOURCE / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["id"] == PLUGIN_ID and manifest["isDesktopOnly"] is True
    script = (obsidian_plugin.SOURCE / "main.js").read_text(encoding="utf-8")
    assert "'board'" in script and "VIEW_TYPE = 'holocore-board'" in script
    assert "sqlite" not in script.casefold()  # The plugin reads HoloCore only through the CLI.
    assert (obsidian_plugin.SOURCE / "styles.css").is_file()


def test_install_copies_files_enables_plugin_and_keeps_settings(tmp_path):
    vault = _vault(tmp_path, plugins=["obsidian-git"])
    folder = vault / ".obsidian" / "plugins" / PLUGIN_ID
    folder.mkdir(parents=True)
    (folder / "data.json").write_text(json.dumps({"days": 30, "command": ["old.exe"]}), encoding="utf-8")

    result = install_plugin(vault, command=["C:/HoloCore/holocore.exe"])
    assert result["installed"] and result["newly_enabled"]
    assert {path.name for path in folder.iterdir()} == {"manifest.json", "main.js", "styles.css", "data.json"}
    assert json.loads((folder / "data.json").read_text(encoding="utf-8")) == {"days": 30, "command": ["C:/HoloCore/holocore.exe"]}
    enabled = json.loads((vault / ".obsidian" / "community-plugins.json").read_text(encoding="utf-8"))
    assert enabled == ["obsidian-git", PLUGIN_ID]
    assert install_plugin(vault, command=["x"])["newly_enabled"] is False  # Re-install does not duplicate.

    removed = uninstall_plugin(vault)
    assert removed["uninstalled"] and not folder.exists()
    assert json.loads((vault / ".obsidian" / "community-plugins.json").read_text(encoding="utf-8")) == ["obsidian-git"]
    assert uninstall_plugin(vault)["uninstalled"] is False


def test_install_into_vault_without_plugin_list_creates_it(tmp_path):
    vault = _vault(tmp_path)
    install_plugin(vault, command=["holocore.exe"])
    assert json.loads((vault / ".obsidian" / "community-plugins.json").read_text(encoding="utf-8")) == [PLUGIN_ID]


def test_non_vault_and_unreadable_plugin_list_are_refused(tmp_path):
    with pytest.raises(PluginError):
        install_plugin(tmp_path / "not-a-vault")
    vault = _vault(tmp_path)
    (vault / ".obsidian" / "community-plugins.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(PluginError):
        install_plugin(vault, command=["holocore.exe"])
    assert not (vault / ".obsidian" / "plugins").exists()


def test_vault_list_comes_from_obsidian_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    assert obsidian_vaults() == []
    present = _vault(tmp_path, "Brain")
    install_plugin(present, command=["holocore.exe"])
    registry = tmp_path / "roaming" / "obsidian" / "obsidian.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({"vaults": {"a": {"path": str(present)}, "b": {"path": str(tmp_path / "Gone")},
                                               "c": "malformed"}}), encoding="utf-8")
    rows = obsidian_vaults()
    assert [(row["name"], row["available"], row["installed"]) for row in rows] == [
        ("Brain", True, True), ("Gone", False, False)]


def test_engine_command_for_source_and_frozen_builds(tmp_path, monkeypatch):
    assert engine_command() == [sys.executable, "-m", "holocore"]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Downloads" / "HoloCore Setup.exe"))
    assert engine_command() == [str(tmp_path / "Downloads" / "holocore.exe")]  # Nothing installed yet.
    installed = tmp_path / "local" / "Programs" / "HoloCore" / "holocore.exe"
    installed.parent.mkdir(parents=True)
    installed.write_bytes(b"engine")
    assert engine_command() == [str(installed)]  # Setup run from Downloads points at the installed engine.
    monkeypatch.setattr(sys, "executable", str(tmp_path / "holocore.exe"))
    assert engine_command() == [str(tmp_path / "holocore.exe")]
