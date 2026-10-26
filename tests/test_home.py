from __future__ import annotations

import json
from pathlib import Path

import holocore.home as home_module
from holocore.config import Config
from holocore.engine import HoloCoreEngine
from holocore.home import APP_VERSION, HomeManager
from holocore.install import bootstrap_project
from holocore.archive_source import sync_archive_source


def test_config_pointer_and_explicit_visible_home(tmp_path: Path, monkeypatch) -> None:
    config_home = tmp_path / "global-config"
    selected = tmp_path / "My visible HoloCore"
    monkeypatch.setenv("HOLOCORE_CONFIG_HOME", str(config_home))

    manager = HomeManager(selected)
    report = manager.initialize()

    pointer = json.loads((config_home / "home.json").read_text(encoding="utf-8"))
    assert pointer["home"] == str(selected.resolve())
    assert report["home"] == str(selected.resolve())
    assert manager.default_home == (Path.home() / "HoloCore").resolve()


def test_initialize_creates_visible_archive_layout_idempotently(tmp_path: Path) -> None:
    home = tmp_path / "HoloCore"
    manager = HomeManager(home, config_home=tmp_path / "config")

    first = manager.initialize()
    registry_before = (home / "worlds.json").read_bytes()
    pointer_before = manager.pointer_path.read_bytes()
    second = manager.initialize()

    assert first["initialized"] is True
    assert (home / "Archive" / "system" / "index.md").is_file()
    assert (home / "Projects").is_dir()
    assert (home / "Connections").is_dir()
    assert (home / "Archive" / "system" / "index.md").is_file()
    assert not (home / "system").exists()
    assert (home / "Archive" / "Worlds").is_dir()
    assert not (home / "Archive" / "Shared").exists()
    assert json.loads(registry_before)["worlds"] == []
    assert second["changed"] is False
    assert second["created"] == []
    assert second["created_count"] == 0
    assert (home / "worlds.json").read_bytes() == registry_before
    assert manager.pointer_path.read_bytes() == pointer_before


def test_register_world_is_stable_atomic_and_lists_existing_worlds(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "Demo Project"
    (project / "Archive" / "wiki").mkdir(parents=True)
    original = project / "Archive" / "wiki" / "decision.md"
    original.write_text("# Keep the original\n", encoding="utf-8")
    manager = HomeManager(home, config_home=tmp_path / "config")

    first = manager.register_world(project)
    registry_before = manager.worlds_path.read_bytes()
    second = manager.register_world(project)
    listed = manager.list_worlds()

    world = first["world"]
    assert world["id"] == "Demo Project"
    assert world["name"] == "Demo Project"
    assert world["root"] == str(project.resolve())
    assert world["registered_at"].endswith("Z")
    assert world["updated_at"] == world["registered_at"]
    assert world["app_version"] == APP_VERSION
    assert Path(world["storage"]) == home / "Projects" / world["id"]
    assert (Path(world["storage"]) / "world.json").is_file()
    assert first["created"] is True
    assert first["import"]["copied"] == ["wiki/decision.md"]
    assert second["created"] is False
    assert second["updated"] is False
    assert second["import"]["skipped"] == ["wiki/decision.md"]
    assert manager.worlds_path.read_bytes() == registry_before
    assert listed["count"] == 1
    assert listed["worlds"][0] == world
    assert original.read_text(encoding="utf-8") == "# Keep the original\n"


def test_import_never_overwrites_conflicting_markdown(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source = project / "Archive" / "nested" / "knowledge.md"
    source.parent.mkdir(parents=True)
    source.write_text("project version", encoding="utf-8")
    manager = HomeManager(tmp_path / "home", config_home=tmp_path / "config")
    registered = manager.register_world(project, import_archive=False)
    world_id = registered["world"]["id"]
    destination = manager.archive / "Worlds" / world_id / "Imported" / "nested" / "knowledge.md"
    destination.parent.mkdir(parents=True)
    destination.write_text("existing Home version", encoding="utf-8")

    report = manager.import_project_archive(project, world_id)

    assert report["copied"] == []
    assert report["conflicts"] == ["nested/knowledge.md"]
    assert destination.read_text(encoding="utf-8") == "existing Home version"
    assert source.read_text(encoding="utf-8") == "project version"


def test_reregister_updates_metadata_without_changing_stable_id(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    times = iter(("2026-07-13T10:00:00Z", "2026-07-13T11:00:00Z"))
    monkeypatch.setattr(home_module, "_utc_now", lambda: next(times))
    manager = HomeManager(tmp_path / "home", config_home=tmp_path / "config")

    first = manager.register_world(project, import_archive=False)["world"]
    second_report = manager.register_world(project, name="Renamed Project", import_archive=False)
    second = second_report["world"]

    assert second_report["created"] is False
    assert second_report["updated"] is True
    assert second["id"] == first["id"]
    assert second["registered_at"] == first["registered_at"]
    assert second["updated_at"] == "2026-07-13T11:00:00Z"


def test_duplicate_project_names_are_rejected_without_hash_suffixes(tmp_path: Path) -> None:
    first = tmp_path / "one" / "Shared Name"
    second = tmp_path / "two" / "Shared Name"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    manager = HomeManager(tmp_path / "home", config_home=tmp_path / "config")

    manager.register_world(first, import_archive=False)

    import pytest

    with pytest.raises(RuntimeError, match="folder names must be unique"):
        manager.register_world(second, import_archive=False)


def test_missing_project_archive_is_a_clear_noop_report(tmp_path: Path) -> None:
    project = tmp_path / "empty-project"
    project.mkdir()
    manager = HomeManager(tmp_path / "home", config_home=tmp_path / "config")

    result = manager.register_world(project)

    assert result["registered"] is True
    assert result["import"]["available"] is False
    assert result["import"]["reason"] == "project Archive not found"
    assert result["import"]["copied_count"] == 0


def test_curated_source_sync_targets_second_brain_world_not_shared_archive(tmp_path: Path) -> None:
    source = tmp_path / "curated"
    source.mkdir()
    (source / "concept.md").write_text("# Durable concept\n", encoding="utf-8")
    home = tmp_path / "home"

    report = sync_archive_source(source, home)

    assert report["copied"] == ["concept.md"]
    assert (home / "Archive" / "Worlds" / "Second Brain" / "wiki" / "concept.md").is_file()
    assert not (home / "Archive" / "Shared").exists()


def test_two_worlds_share_one_home_and_animus_without_private_archive_leakage(
    tmp_path: Path, isolated_holocore_home: Path
) -> None:
    alpha = tmp_path / "Alpha World"
    beta = tmp_path / "Beta World"
    bootstrap_project(alpha, init_git=False, platforms=[], home=isolated_holocore_home)
    bootstrap_project(beta, init_git=False, platforms=[], home=isolated_holocore_home)

    alpha_config = Config.load(root=alpha)
    beta_config = Config.load(root=beta)
    assert alpha_config.home == beta_config.home == isolated_holocore_home
    assert alpha_config.world_id != beta_config.world_id
    assert alpha_config.vault != beta_config.vault
    assert alpha_config.animus_path == beta_config.animus_path == isolated_holocore_home / "Memories" / "animus.db"
    assert alpha_config.raw_chats_path == isolated_holocore_home / "Memories" / "ChatHistory" / "Alpha World"
    assert beta_config.raw_chats_path == isolated_holocore_home / "Memories" / "ChatHistory" / "Beta World"
    assert not (alpha / ".holocore").exists()
    assert not (beta / ".holocore").exists()

    alpha_engine = HoloCoreEngine(alpha)
    beta_engine = HoloCoreEngine(beta)
    alpha_engine.router.archive.create(
        "wiki/alpha-private.md", "Alpha-only private archive knowledge."
    )
    assert beta_engine.router.archive.search("Alpha-only private") == []
    assert not (isolated_holocore_home / "Archive" / "Shared").exists()
    assert (isolated_holocore_home / "Archive" / "Worlds" / "Alpha World" / "wiki").is_dir()
    assert (isolated_holocore_home / "Archive" / "Worlds" / "Beta World" / "wiki").is_dir()

    alpha_engine.remember("alphaepisodicneedle", "project")
    beta_engine.remember("betaepisodicneedle", "project")
    assert beta_engine.router.animus.search(
        "alphaepisodicneedle", world=beta_engine.router.world_id, sector="project"
    ) == []
