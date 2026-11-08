from __future__ import annotations

from pathlib import Path

from holocore.activity import ActivityLedger
from holocore.config import Config
from holocore.console import build_console_payload, render_console_html
from holocore.engine import HoloCoreEngine


def test_console_exposes_v09_activity_filters_handoffs_and_connections(
    tmp_path: Path, isolated_holocore_home: Path
) -> None:
    root = tmp_path / "world"
    HoloCoreEngine(root).initialize(git=False, home=isolated_holocore_home, platforms=[])
    config = Config.load(root=root)
    ledger = ActivityLedger(config.memory_path)
    ledger.record_hook(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "console-session",
            "event_id": "console-event",
            "tool_name": "apply_patch",
            "tool_input": {"file_path": str(root / "app.py")},
        },
        client="codex",
        project_id=config.world_id or root.name,
    )

    payload = build_console_payload(root)
    html = render_console_html(payload)

    assert payload["runs"]
    assert payload["run_handoffs"]
    assert set(payload["connections"]) == {"claude", "codex", "cursor", "gemini", "opencode"}
    assert "Search agents, tools, status, or affected items" in html
    assert "AI connections" in html


def test_console_exposes_complete_memory_filters_provenance_and_guided_workflows(
    tmp_path: Path, isolated_holocore_home: Path
) -> None:
    root = tmp_path / "memory-console"
    engine = HoloCoreEngine(root)
    engine.initialize(git=False, home=isolated_holocore_home, platforms=[])
    engine.remember("The research workflow must preserve provenance.", sector="project", source="test://console")

    payload = build_console_payload(root)
    html = render_console_html(payload)

    assert payload["memory_browser"]["total"] == 1
    assert payload["memory_browser"]["items"][0]["provenance"][0]["source_ref"] == "test://console"
    assert "Search memories" in html
    assert "Provenance and retention details" in html
    assert "Forget this memory" in html
    assert "Research & Architecture" in html
    assert "Read-only until you explicitly save" in html
    assert "prefers-reduced-motion" in html
