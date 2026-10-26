from __future__ import annotations

import json
from pathlib import Path

import pytest

from holocore.folder_layout import HomeLayout, LayoutMigrationError, migrate_home


def _legacy_home(root: Path) -> Path:
    (root / "Archive" / "Worlds" / "demo").mkdir(parents=True)
    (root / "Archive" / "Worlds" / "demo" / "note.md").write_text("kept", encoding="utf-8")
    (root / "Animus" / "raw-chats" / "demo").mkdir(parents=True)
    (root / "Animus" / "raw-chats" / "demo" / "chat.json").write_text("{}", encoding="utf-8")
    (root / "Projects" / "demo" / "Atlas").mkdir(parents=True)
    (root / "Projects" / "demo" / "Atlas" / "graph.json").write_text("{}", encoding="utf-8")
    (root / "worlds.json").write_text(json.dumps({"worlds": [{"id": "demo", "storage": str(root / "Projects" / "demo")}]}), encoding="utf-8")
    return root


def test_legacy_dry_run_does_not_mutate_home(tmp_path: Path) -> None:
    home = _legacy_home(tmp_path / "Home")
    before = sorted(str(p.relative_to(home)) for p in home.rglob("*"))
    report = migrate_home(home)
    assert report["applied"] is False
    assert report["layout_version"] == 1
    assert sorted(str(p.relative_to(home)) for p in home.rglob("*")) == before


def test_migration_renames_data_and_preserves_legacy_paths(tmp_path: Path) -> None:
    home = _legacy_home(tmp_path / "Home")
    report = migrate_home(home, apply=True)
    assert report["applied"] is True
    assert HomeLayout(home).version == 2
    assert (home / "Knowledge" / "Projects" / "demo" / "note.md").read_text(encoding="utf-8") == "kept"
    assert (home / "Memories" / "ChatHistory" / "demo" / "chat.json").is_file()
    assert (home / "Projects" / "demo" / "CodeMap" / "graph.json").is_file()
    assert (home / "Archive" / "Worlds" / "demo" / "note.md").is_file()
    assert (home / "Animus" / "raw-chats" / "demo" / "chat.json").is_file()
    assert (home / "Projects" / "demo" / "Atlas" / "graph.json").is_file()
    assert (home / "worlds.json").is_file()
    assert json.loads((home / "layout-migration.json").read_text(encoding="utf-8"))["status"] == "complete"


def test_migration_refuses_to_merge_conflicting_layouts(tmp_path: Path) -> None:
    home = _legacy_home(tmp_path / "Home")
    (home / "Knowledge").mkdir()
    with pytest.raises(LayoutMigrationError, match="both legacy and new folders"):
        migrate_home(home, apply=True)
    assert (home / "Archive" / "Worlds" / "demo" / "note.md").is_file()

