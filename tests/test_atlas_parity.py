from pathlib import Path

from holocore.atlas import Atlas
from holocore.atlas_exports import export_atlas


def test_constellations_audit_and_queries_are_deterministic(tmp_path: Path):
    (tmp_path / "a.py").write_text("from b import run\ndef start():\n    return run()\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    atlas = Atlas(tmp_path); atlas.refresh()
    first = atlas.constellations(); second = atlas.constellations()
    assert first == second and first[0]["id"].startswith("constellation:")
    assert atlas.explain("run")["resolved"]
    assert atlas.neighborhood("run")["nodes"]
    audit = atlas.audit()
    assert audit["source_coverage"]["files"] == 2


def test_graphify_style_exports_write_manifest(tmp_path: Path):
    (tmp_path / "main.py").write_text("def main():\n    return True\n", encoding="utf-8")
    atlas = Atlas(tmp_path); atlas.refresh()
    result = export_atlas(atlas, tmp_path / "exports")
    assert set(result["formats"]) == {"json", "html", "markdown", "manifest"}
    assert (tmp_path / "exports" / "graph.json").is_file()
    assert (tmp_path / "exports" / "graph.html").is_file()


def test_weighted_communities_split_dense_areas_connected_by_a_bridge(tmp_path: Path):
    (tmp_path / "accounts.py").write_text(
        "def create_account():\n    return validate_account()\n\ndef validate_account():\n    return True\n",
        encoding="utf-8",
    )
    (tmp_path / "billing.py").write_text(
        "from accounts import validate_account\n\ndef create_invoice():\n    return calculate_total()\n\ndef calculate_total():\n    return validate_account()\n",
        encoding="utf-8",
    )
    atlas = Atlas(tmp_path)
    atlas.refresh()

    communities = atlas.constellations(min_size=2)

    assert len(communities) >= 2
    assert all(item["topics"] for item in communities)
    assert all(item["relationship_count"] >= 1 for item in communities)


def test_semantic_search_and_community_knowledge_are_evidence_backed(tmp_path: Path):
    (tmp_path / "release_pipeline.py").write_text(
        "class ReleaseIntegrityGate:\n    def verify_checksum(self):\n        return True\n",
        encoding="utf-8",
    )
    atlas = Atlas(tmp_path)
    graph = atlas.refresh()

    assert any("integrity" in node["semantic_terms"] for node in graph["nodes"] if node.get("kind") == "class")
    assert atlas.search("integrity gate")[0]["label"] == "ReleaseIntegrityGate"

    generated = atlas.write_community_knowledge()
    assert generated["count"] >= 1
    index = Path(generated["index"]).read_text(encoding="utf-8")
    note = Path(generated["files"][0]).read_text(encoding="utf-8")
    assert "outputs, not curated Archive sources" in index
    assert "## Evidence" in note
    assert "Source digest" in note

    stale = Path(generated["path"]) / "stale.md"
    stale.write_text("<!-- holocore:managed-code-community -->\n# Old result\n", encoding="utf-8")
    user_note = Path(generated["path"]) / "my-notes.md"
    user_note.write_text("# Keep this\n", encoding="utf-8")
    refreshed = atlas.write_community_knowledge()
    assert str(stale) in refreshed["removed_stale"]
    assert not stale.exists()
    assert user_note.exists()
