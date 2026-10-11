from __future__ import annotations

from pathlib import Path

import holocore.obsidian_watch as watcher


def test_watch_home_refreshes_once_for_a_world(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "Home"
    world = tmp_path / "World"
    home.mkdir()
    world.mkdir()
    (home / "worlds.json").write_text(
        '{"schema_version": 1, "worlds": [{"id": "world", "name": "World", "root": "'
        + str(world).replace("\\", "\\\\")
        + '"}]}',
        encoding="utf-8",
    )
    calls: list[Path] = []
    monkeypatch.setattr(watcher, "refresh_home", lambda value: calls.append(Path(value)))

    assert watcher.watch_home(home, once=True) == 1
    assert calls == [home.resolve()]


def test_watch_ignores_generated_output(tmp_path: Path) -> None:
    home = tmp_path / "Home"
    home.mkdir()
    (home / "worlds.json").write_text('{"worlds": []}', encoding="utf-8")
    first = watcher._source_fingerprint(home)
    generated = home / "HoloCore Obsidian Vault"
    generated.mkdir()
    (generated / "README.md").write_text("generated", encoding="utf-8")
    assert watcher._source_fingerprint(home) == first
