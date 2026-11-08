from __future__ import annotations

import json
from types import SimpleNamespace
from pathlib import Path

from holocore.home import HomeManager
import holocore.lifecycle as lifecycle
from holocore.lifecycle import sync_all


def test_sync_all_reconciles_two_registered_worlds(
    tmp_path: Path, isolated_holocore_home: Path
) -> None:
    worlds = [tmp_path / "First World", tmp_path / "Second World"]
    for world in worlds:
        world.mkdir()

    manager = HomeManager(isolated_holocore_home)
    registrations = [manager.register_world(world, import_archive=False) for world in worlds]
    expected_ids = {item["world"]["id"] for item in registrations}

    report = sync_all(isolated_holocore_home)

    assert report["home"] == str(isolated_holocore_home)
    assert report["count"] == report["updated"] == 2
    assert report["failed"] == 0
    assert report["obsidian_vault"]["vault_name"] == "HoloCore Obsidian Vault"
    assert Path(report["obsidian_vault"]["path"]).is_dir()
    assert {item["id"] for item in report["worlds"]} == expected_ids
    assert {item["root"] for item in report["worlds"]} == {
        str(world.resolve()) for world in worlds
    }
    assert all(item["atlas"]["fresh"] is True for item in report["worlds"])

    configs = [
        json.loads((isolated_holocore_home / "Projects" / world_id / "world.json").read_text(encoding="utf-8"))
        for world_id in expected_ids
    ]
    assert {config["home"] for config in configs} == {str(isolated_holocore_home)}
    assert {config["world_id"] for config in configs} == expected_ids
    assert len({config["archive"] for config in configs}) == 2
    for config in configs:
        storage = isolated_holocore_home / "Projects" / config["world_id"]
        assert Path(config["archive"]) == isolated_holocore_home / "Knowledge" / "Projects" / config["world_id"]
        assert "shared_archive" not in config
        assert (storage / "CodeMap" / "graph.json").is_file()
        assert (storage / "CodeMap" / "atlas.html").is_file()
        assert not Path(config["root"]).joinpath("holocore-out").exists()


def test_sync_all_regenerates_plain_interfaces_and_removes_only_legacy_generated_files(
    tmp_path: Path, isolated_holocore_home: Path
) -> None:
    world = tmp_path / "Plain Names"
    world.mkdir()
    manager = HomeManager(isolated_holocore_home)
    manager.register_world(world, import_archive=False)
    connections = isolated_holocore_home / "Connections"
    legacy = connections / ".claude" / "commands" / "holocore-animus-sync.md"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("# HoloCore generated legacy command\n", encoding="utf-8")
    user_file = connections / ".claude" / "commands" / "my-command.md"
    user_file.write_text("# User command\n", encoding="utf-8")

    report = sync_all(isolated_holocore_home, platforms=["claude"], regenerate=True)

    assert report["regeneration"]["plain_names"] is True
    assert str(legacy) in report["regeneration"]["legacy_interfaces_removed"]
    assert not legacy.exists()
    assert user_file.exists()
    assert (connections / ".claude" / "commands" / "holocore-memory-collect.md").is_file()


def test_update_install_upgrades_then_reconciles(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(lifecycle.shutil, "which", lambda name: "C:/tools/uv.exe")
    monkeypatch.setattr(
        lifecycle.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command) or SimpleNamespace(stdout="updated\n", returncode=0),
    )
    monkeypatch.setattr(
        lifecycle,
        "sync_all",
        lambda home: {"home": str(home), "count": 2, "updated": 2, "failed": 0, "worlds": []},
    )

    result = lifecycle.update_install(tmp_path / "Home")

    assert calls == [[
        "C:/tools/uv.exe",
        "tool",
        "upgrade",
        "holocore",
    ]]
    assert result["updated"] is True
    assert result["installer_output"] == "updated"
    assert result["reconciliation"]["updated"] == 2


def test_native_uninstall_is_queued_and_preserves_data(monkeypatch, tmp_path: Path) -> None:
    install_dir = tmp_path / "Programs" / "HoloCore"
    install_dir.mkdir(parents=True)
    executable = install_dir / "holocore.exe"
    executable.write_bytes(b"native")
    installer = install_dir / "install.ps1"
    installer.write_text("param([switch]$Uninstall)\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(lifecycle.sys, "frozen", True, raising=False)
    monkeypatch.setattr(lifecycle.sys, "executable", str(executable))
    monkeypatch.setattr(
        lifecycle.subprocess,
        "Popen",
        lambda command, **kwargs: calls.append((command, kwargs)) or SimpleNamespace(),
    )

    result = lifecycle.uninstall_tool()

    assert result["uninstall_queued"] is True
    assert result["data_preserved"] is True
    assert result["command"] == [str(installer), "-Uninstall"]
    assert "-Uninstall" in calls[0][0][-1]
    assert calls[0][1]["stdout"] is lifecycle.subprocess.DEVNULL
