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
    ledger = ActivityLedger(config.animus_path)
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
