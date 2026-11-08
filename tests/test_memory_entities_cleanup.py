from pathlib import Path

from holocore.memories import MemoryStore
from holocore.cleanup import cleanup_legacy


def test_temporal_entities_are_scoped_to_rooms_and_keep_validity(tmp_path: Path):
    animus = MemoryStore(tmp_path / "animus.db")
    animus.create_world("Demo")
    animus.record_entity_event("Redis", "used_for", "cache", world="Demo", room="Architecture", valid_from="2026-01-01", source_ref="chat:1")
    animus.record_entity_event("Redis", "used_for", "session-cache", world="Demo", room="Architecture", valid_from="2026-02-01", source_ref="chat:2")
    assert [room.name for room in animus.rooms(world="Demo")] == ["Architecture"]
    events = animus.entity_timeline(world="Demo", entity="Redis")
    assert len(events) == 2
    assert events[0].valid_from == "2026-02-01"


def test_cleanup_defaults_to_preview_and_only_removes_on_apply(tmp_path: Path):
    legacy = tmp_path / ".holocore"
    legacy.mkdir()
    (legacy / "animus.db").write_bytes(b"sqlite")
    preview = cleanup_legacy(tmp_path)
    assert preview["removed"] == []
    assert legacy.exists()
    result = cleanup_legacy(tmp_path, apply=True)
    assert str(legacy) in result["removed"]
    assert not legacy.exists()
