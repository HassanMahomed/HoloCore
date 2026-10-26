from __future__ import annotations

from holocore.atlas_html import render_atlas_html
from holocore.console import render_console_html


def test_code_map_view_uses_plain_item_and_cluster_labels() -> None:
    html = render_atlas_html({"nodes": [{"id": "file:app.py", "label": "app.py", "kind": "file"}], "links": []})

    assert "HoloCore Code Map" in html
    assert "Search items" in html
    assert "Item details" in html
    assert "No matching items" in html
    assert "Signal" not in html
    assert "Constellation" not in html


def test_console_navigation_uses_plain_language_sections() -> None:
    html = render_console_html({
        "world": "demo",
        "root": "C:/demo",
        "worlds": [],
        "paths": {},
        "status": {},
        "chats": [],
        "shards": [],
        "decks": [],
        "chronicle": [],
        "activity_status": {},
        "runs": [],
        "run_actions": {},
        "run_handoffs": {},
        "connections": {},
        "wiki": [],
        "atlas": {},
        "global_atlas": {},
        "commands": [],
    })

    assert "'Overview','Chat History','Memories','Knowledge','Code Map','Agent Activity','Locations','Commands','Setup & Updates'" in html
    assert "Browse and search" in html
    assert "Save knowledge" in html
    assert "Setup and updates" in html
    assert "All Projects Code Map" in html
    assert "Activity Memory" not in html
    assert "Agent Runs" not in html
