import json

from holocore.atlas_html import generate_atlas_html


def test_self_contained_html_is_generated(tmp_path):
    graph = tmp_path / "atlas.json"; graph.write_text(json.dumps({"nodes": [{"id": "a", "label": "App", "kind": "class"}], "links": []}), encoding="utf-8")
    output = generate_atlas_html(graph)
    text = output.read_text(encoding="utf-8")
    assert "HoloCore Code Map" in text and "App" in text and "Search items" in text
    assert "forceLayout" in text and "More labels" in text and "Zoom in" in text
