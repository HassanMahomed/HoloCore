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


def test_affected_lookup_is_scoped_case_insensitive_and_newest_first(tmp_path):
    ledger = ActivityLedger(tmp_path / "activity.db")
    for project, event_id, session, path in (
        ("project-1", "event-1", "one", r"C:\Work\SRC\App.py"),
        ("project-1", "event-2", "two", "C:/Work/src/app.py"),
        ("project-2", "event-3", "other", "C:/Work/src/app.py"),
    ):
        ledger.record_hook(
            {
                "hook_event_name": "PostToolUse",
                "session_id": session,
                "event_id": event_id,
                "tool_name": "edit",
                "tool_input": {"file_path": path},
            },
            client="codex",
            project_id=project,
        )

    result = ledger.affected("work/src/app.PY", project_id="project-1")

    assert result["count"] == 2
    assert {item["run_id"] for item in result["results"]} == {
        ActivityLedger.run_id("project-1", "codex", "one"),
        ActivityLedger.run_id("project-1", "codex", "two"),
    }
    assert all(item["agent"] == "codex" for item in result["results"])


def test_handoff_is_bounded_summarized_and_private(tmp_path):
    ledger = ActivityLedger(tmp_path / "activity.db")
    for index in range(55):
        ledger.record_hook(
            {
                "hook_event_name": "PostToolUse",
                "session_id": "handoff-session",
                "event_id": f"event-{index}",
                "tool_name": "apply_patch",
                "is_error": index == 3,
                "prompt": "never include this prompt",
                "tool_input": {
                    "file_path": str(tmp_path / "src" / f"file-{index}.py"),
                    "authorization": "never include this secret",
                },
            },
            client="claude",
            project_id="project-1",
        )

    handoff = ledger.handoff(project_id="project-1", action_limit=500, item_limit=500)

    assert handoff["summary"] == {"actions": 55, "failures": 1, "affected_items": 55}
    assert len(handoff["recent_actions"]) == 50
    assert len(handoff["affected_items"]) == 55
    serialized = json.dumps(handoff)
    assert "never include this prompt" not in serialized
    assert "never include this secret" not in serialized
    assert "raw tool payloads are not stored" in handoff["privacy"]


def test_handoff_rejects_unknown_run(tmp_path):
    ledger = ActivityLedger(tmp_path / "activity.db")
    try:
        ledger.handoff(project_id="project-1", run_id="missing")
    except ValueError as exc:
        assert "No recorded agent activity" in str(exc)
    else:
        raise AssertionError("Expected a missing run to fail clearly")
