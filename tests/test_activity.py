from __future__ import annotations

import json

from holocore.activity import ActivityLedger
from holocore.capture_hook import run as capture_hook_run


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


def test_cursor_activity_normalizes_session_events_and_never_stores_prompt_or_tool_output(tmp_path):
    ledger = ActivityLedger(tmp_path / "cursor.db")
    result = ledger.record_hook(
        {
            "hook_event_name": "postToolUse",
            "conversation_id": "cursor-conversation",
            "generation_id": "generation-4",
            "tool_use_id": "tool-call-9",
            "model_id": "cursor-model",
            "tool_name": "Write",
            "tool_input": {"file_path": str(tmp_path / "src" / "main.py")},
            "tool_output": "private output",
            "prompt": "private prompt",
        },
        client="cursor",
        project_id="project-1",
    )

    run = next(row for row in ledger.runs(project_id="project-1") if row["id"] == result["run_id"])
    action = ledger.actions(result["run_id"])[0]
    assert run["session_id"] == "cursor-conversation"
    assert run["model"] == "cursor-model"
    assert action["items"] == (str(tmp_path / "src" / "main.py"),)
    serialized = json.dumps({"run": run, "action": action})
    assert "private prompt" not in serialized
    assert "private output" not in serialized


def test_opencode_tool_activity_uses_event_properties_session_id(tmp_path):
    ledger = ActivityLedger(tmp_path / "opencode.db")
    result = ledger.record_hook(
        {
            "event": "tool.execute.after",
            "properties": {"sessionID": "oc-session", "info": {"id": "tool-event-1"}},
            "tool_name": "write",
            "tool_input": {"filePath": str(tmp_path / "README.md")},
        },
        client="opencode",
        project_id="project-1",
    )
    run = next(row for row in ledger.runs(project_id="project-1") if row["id"] == result["run_id"])
    assert run["session_id"] == "oc-session"
    assert result["items"] == [str(tmp_path / "README.md")]


def test_new_client_capture_is_activity_only(tmp_path, isolated_holocore_home, monkeypatch):
    from holocore.engine import HoloCoreEngine

    monkeypatch.setattr("holocore.capture_hook.maybe_check", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        HoloCoreEngine,
        "ingest_chat",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not ingest chat")),
    )
    root = tmp_path / "project"
    root.mkdir()
    result = capture_hook_run(
        {
            "cwd": str(root),
            "hook_event_name": "postToolUse",
            "conversation_id": "cursor-session",
            "tool_use_id": "cursor-action-1",
            "tool_name": "Write",
            "tool_input": {"file_path": str(root / "src.py")},
            "prompt": "do not capture this",
        },
        "cursor",
    )

    assert result["reason"] == "activity-only-client"
    assert result["activity"]["recorded"] is True


def test_cli_hook_entrypoint_emits_json_for_cursor(monkeypatch, capsys):
    from holocore import cli

    monkeypatch.setattr("sys.argv", ["holocore", "capture-hook", "--client", "cursor"])
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("{}"))
    monkeypatch.setattr("holocore.capture_hook.run", lambda payload, client: {"recorded": True})

    assert cli.main() == 0
    assert capsys.readouterr().out.strip() == "{}"


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


def test_persistent_handoff_is_claimed_once_and_returns_run_context(tmp_path):
    ledger = ActivityLedger(tmp_path / "activity.db")
    recorded = ledger.record_hook(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "handoff-source",
            "event_id": "handoff-source-action",
            "tool_name": "edit",
            "tool_input": {"file_path": str(tmp_path / "src" / "feature.py")},
        },
        client="codex",
        project_id="project-1",
    )
    created = ledger.create_handoff(
        project_id="project-1",
        run_id=recorded["run_id"],
        title="Continue the feature",
        note="Review the modified file and run the acceptance checks.",
    )

    claimed = ledger.claim_handoff(created["id"], project_id="project-1", agent="codex")
    repeated = ledger.claim_handoff(created["id"], project_id="project-1", agent="Codex")

    assert claimed["status"] == "claimed"
    assert claimed["claimed_by"] == "codex"
    assert claimed["context"]["run"]["id"] == recorded["run_id"]
    assert claimed["context"]["summary"]["affected_items"] == 1
    assert repeated["claimed_at"] == claimed["claimed_at"]
    assert ledger.handoffs(project_id="project-1", status="open") == []

    try:
        ledger.claim_handoff(created["id"], project_id="project-1", agent="claude")
    except ValueError as exc:
        assert "cannot be claimed" in str(exc)
    else:
        raise AssertionError("A second agent must not claim an already claimed handoff")


def test_handoff_close_requires_claim_owner_and_retains_history(tmp_path):
    ledger = ActivityLedger(tmp_path / "activity.db")
    recorded = ledger.record_hook(
        {"hook_event_name": "SessionEnd", "session_id": "close-source", "event_id": "close-event"},
        client="claude",
        project_id="project-1",
    )
    completed = ledger.create_handoff(project_id="project-1", run_id=recorded["run_id"], title="Finish work")
    abandoned = ledger.create_handoff(project_id="project-1", run_id=recorded["run_id"], title="Skip work")
    ledger.claim_handoff(completed["id"], project_id="project-1", agent="claude")

    try:
        ledger.close_handoff(completed["id"], project_id="project-1", outcome="completed", agent="codex")
    except ValueError as exc:
        assert "claimed this handoff" in str(exc)
    else:
        raise AssertionError("Only the claiming agent may complete claimed work")

    done = ledger.close_handoff(
        completed["id"], project_id="project-1", outcome="completed", agent="claude", reason="Finished"
    )
    cancelled = ledger.close_handoff(
        abandoned["id"], project_id="project-1", outcome="abandoned", reason="No longer needed"
    )

    assert done["status"] == "completed"
    assert done["claimed_by"] == "claude"
    assert done["close_reason"] == "Finished"
    assert cancelled["status"] == "abandoned"
    assert {row["status"] for row in ledger.handoffs(project_id="project-1")} == {"completed", "abandoned"}
