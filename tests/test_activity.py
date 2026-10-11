from __future__ import annotations

import json

from holocore.activity import ActivityLedger


def test_activity_ledger_records_runs_actions_subagents_and_affected_items(tmp_path):
    ledger = ActivityLedger(tmp_path / "activity.db")
    base = {
        "hook_event_name": "PreToolUse",
        "session_id": "session-1",
        "event_id": "event-1",
        "model": "test-model",
        "tool_name": "apply_patch",
        "tool_input": {
            "file_path": str(tmp_path / "src" / "app.py"),
            "authorization": "Bearer do-not-store",
        },
        "prompt": "private prompt body",
        "api_key": "do-not-store",
    }
    first = ledger.record_hook(base, client="codex", project_id="project-1")
    repeated = ledger.record_hook(base, client="codex", project_id="project-1")
    child = ledger.record_hook(
        {
            "hook_event_name": "SubagentStart",
            "session_id": "session-1",
            "agent_id": "researcher-1",
            "agent_type": "researcher",
            "event_id": "event-2",
        },
        client="codex",
        project_id="project-1",
    )

    assert first["recorded"] is True
    assert repeated["recorded"] is False
    assert first["items"] == [str(tmp_path / "src" / "app.py")]
    assert child["run_id"] != first["run_id"]

    runs = ledger.runs(project_id="project-1")
    child_run = next(run for run in runs if run["id"] == child["run_id"])
    assert child_run["parent_run_id"] == first["run_id"]
    assert ledger.status(project_id="project-1") == {
        "ready": True,
        "database": str(tmp_path / "activity.db"),
        "runs": 2,
        "actions": 2,
        "items": 1,
    }

    serialized = json.dumps({"runs": runs, "actions": ledger.actions(first["run_id"])})
    assert "private prompt body" not in serialized
    assert "do-not-store" not in serialized


def test_session_end_completes_run(tmp_path):
    ledger = ActivityLedger(tmp_path / "activity.db")
    payload = {"hook_event_name": "SessionEnd", "session_id": "session-2", "event_id": "end-1"}
    result = ledger.record_hook(payload, client="claude", project_id="project-1")
    run = next(item for item in ledger.runs(project_id="project-1") if item["id"] == result["run_id"])
    assert run["status"] == "completed"
    assert run["ended_at"]
