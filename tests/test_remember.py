from __future__ import annotations

from pathlib import Path

from holocore.engine import HoloCoreEngine


def _engine(tmp_path: Path, home: Path) -> HoloCoreEngine:
    engine = HoloCoreEngine(tmp_path / "remember-world")
    engine.initialize(git=False, home=home, platforms=[])
    return engine


def _texts(engine: HoloCoreEngine) -> set[str]:
    return {shard.content for shard in engine.router.memories.shards(world=engine.router.world_id, sector="project")}


def test_remembering_different_details_from_one_source_keeps_all_of_them(tmp_path, isolated_holocore_home):
    engine = _engine(tmp_path, isolated_holocore_home)
    details = ["First decision about backups.", "Second decision about search.", "Third decision about installs."]
    results = [engine.remember(text, "project", "claude:review") for text in details]
    assert [result["action"] for result in results] == ["inserted"] * 3
    assert set(details) <= _texts(engine)


def test_remembering_without_a_source_keeps_every_detail(tmp_path, isolated_holocore_home):
    engine = _engine(tmp_path, isolated_holocore_home)
    engine.remember("A detail saved with no source.", "project")
    engine.remember("Another detail saved with no source.", "project")
    assert {"A detail saved with no source.", "Another detail saved with no source."} <= _texts(engine)


def test_remembering_the_same_detail_again_is_still_a_duplicate(tmp_path, isolated_holocore_home):
    engine = _engine(tmp_path, isolated_holocore_home)
    first = engine.remember("Keep backups weekly.", "project", "claude:review")
    again = engine.remember("Keep backups weekly.", "project", "claude:review")
    assert first["created"] is True
    assert again["deduplicated"] is True and again["action"] == "unchanged"
    assert again["id"] == first["id"]
