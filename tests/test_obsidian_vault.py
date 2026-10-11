import json
from pathlib import Path

from holocore.obsidian_vault import VAULT_NAME, build_obsidian_vault


def _write_graph(path: Path, label: str) -> None:
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "nodes": [
            {"id": f"file:{label}.py", "label": f"{label}.py", "kind": "file", "source_file": f"{label}.py"},
            {"id": f"function:{label}:run", "label": "run()", "kind": "function", "source_file": f"{label}.py"},
        ],
        "links": [{"source": f"file:{label}.py", "target": f"function:{label}:run", "relation": "contains"}],
    }), encoding="utf-8")


def test_build_obsidian_vault_creates_one_linked_view_without_copying_archive_or_animus(tmp_path: Path) -> None:
    home = tmp_path / "Home"
    world_a = tmp_path / "alpha"
    world_b = tmp_path / "beta"
    _write_graph(world_a / "graphify-out" / "graph.json", "alpha")
    _write_graph(world_b / "graphify-out" / "graph.json", "beta")
    (home / "Archive" / "Worlds" / "alpha").mkdir(parents=True)
    (home / "worlds.json").write_text(json.dumps({"worlds": [
        {"id": "alpha", "name": "Alpha", "root": str(world_a)},
        {"id": "beta", "name": "Beta", "root": str(world_b)},
    ]}), encoding="utf-8")

    result = build_obsidian_vault(home)
    vault = home / VAULT_NAME

    assert Path(result["path"]) == vault
    assert (vault / "README.md").is_file()
    assert (vault / "system" / "index.md").is_file()
    assert "[[Worlds/alpha|Alpha]]" in (vault / "system" / "index.md").read_text(encoding="utf-8")
    assert "[[Worlds/beta|Beta]]" in (vault / "system" / "index.md").read_text(encoding="utf-8")
    assert list((vault / "Worlds" / "alpha" / "Signals").glob("*.md"))
    assert list((vault / "Worlds" / "beta" / "Signals").glob("*.md"))
    assert not (vault / "Archive").exists()
    assert "Atlas-only" in (vault / "system" / "atlas-manifest.json").read_text(encoding="utf-8")
