import json
from pathlib import Path

from holocore.global_graph import build_global_graph


def test_global_graph_prefixes_world_nodes_and_keeps_only_atlas_data(tmp_path: Path):
    home = tmp_path / "Home"
    world = tmp_path / "project"
    (world / "graphify-out").mkdir(parents=True)
    (world / "graphify-out" / "graph.json").write_text(json.dumps({
        "nodes": [{"id": "file:a.py", "label": "a.py", "kind": "file"}],
        "links": [],
    }), encoding="utf-8")
    home.mkdir()
    (home / "worlds.json").write_text(json.dumps({"worlds": [{"id": "demo", "name": "Demo", "root": str(world)}]}), encoding="utf-8")

    graph = build_global_graph(home, output=home / "global-graph.json")

    assert any(node["id"] == "world:demo" for node in graph["nodes"])
    assert any(node["id"] == "demo:file:a.py" for node in graph["nodes"])
    assert graph["graph"]["source"].startswith("Code Map only")
    assert Path(graph["path"]).is_file()
