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
        assert Path(config["archive"]) == isolated_holocore_home / "Archive" / "Worlds" / config["world_id"]
        assert "shared_archive" not in config
        assert (storage / "Atlas" / "graph.json").is_file()
        assert (storage / "Atlas" / "atlas.html").is_file()
        assert not Path(config["root"]).joinpath("holocore-out").exists()


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
