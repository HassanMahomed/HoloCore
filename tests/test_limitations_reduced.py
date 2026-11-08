from pathlib import Path

from holocore.memories import MemoryStore
from holocore.memory_retrieval import MemoryRetriever
from holocore.archive_promotion import promote_entry


def test_embedding_retrieval_can_use_custom_embedder(tmp_path: Path) -> None:
    animus = MemoryStore(tmp_path / "animus.db")
    animus.create_world("w")
    animus.create_sector("w", "project")
    animus.ingest("blue automobile", world="w", sector="project", source_ref="test")
    vectors = {"blue automobile": [1.0, 0.0], "vehicle": [1.0, 0.0]}
    result = MemoryRetriever(animus, vectors.get).search("vehicle", world="w", sector="project")
    assert result and result[0].method == "semantic"


def test_world_wiki_promotion_reports_conflicts(tmp_path: Path) -> None:
    source = tmp_path / "world" / "note.md"; source.parent.mkdir()
    archive = tmp_path / "archive"
    source.write_text("one", encoding="utf-8")
    assert promote_entry(source, archive)["status"] == "promoted"
    source.write_text("two", encoding="utf-8")
    assert promote_entry(source, archive)["status"] == "conflict"
