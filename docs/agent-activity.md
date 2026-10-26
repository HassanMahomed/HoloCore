# Agent activity ledger

HoloCore records a structured history of AI-agent work so users can answer:

- Which agent or subagent ran?
- What action or tool event occurred?
- When did it happen, and did it succeed?
- Which files or folders were affected?
- How do the actions fit into one run?

## What is recorded

Each supported client hook creates or updates an **Agent Run** and appends an **Action**. A run includes the project, client, session, agent type, model when supplied by the client, parent run for subagents, status, and timestamps. An action includes its lifecycle event, tool name, status, duration when supplied, and affected item paths.

Affected paths are normalized into reusable **Items** and connected to their actions. This supports both directions: list the items touched by an action, or later show the actions that affected an item.

The data is stored in the local HoloCore SQLite database beside conversation memory. View it in the Console's **Agent Runs** tab or use:

```powershell
holocore activity status
holocore activity runs
holocore activity show <run-id>
holocore activity affected "src/holocore/activity.py"
holocore handoff
```

`activity affected` works in the reverse direction: start with a full or partial file path and see the newest linked actions, agents, and runs. `handoff` selects the latest run by default, or accepts a run ID, and returns totals, failures, affected items, and up to 50 recent actions. It is generated on demand from the sanitized ledger and is not copied into conversation memory.

The older `activity-status` and `runs` commands remain available for scripts and existing AI skills.

## Claimable handoffs

A run summary can become a persistent handoff that the next agent claims before continuing. HoloCore records the run reference and a short title/note; the sanitized context is loaded from the activity ledger when claimed.

```powershell
holocore handoffs create <run-id> --title "Finish the installer work" --note "Review the outstanding packaging issue."
holocore handoffs list --status open
holocore handoffs claim <handoff-id> --agent codex
holocore handoffs complete <handoff-id> --agent codex --reason "Packaging issue resolved."
```

Use `abandon` to close work that should not continue. An open handoff can be abandoned directly; claimed work can be completed or abandoned by the agent that claimed it. A second agent cannot claim an already claimed handoff. Handoff notes are local and should not contain credentials or private conversation text.

## Privacy boundary

The activity ledger stores allow-listed lifecycle metadata. It does not persist prompt bodies, assistant responses, complete tool inputs or outputs, authentication values, cookies, API keys, passwords, or tokens. The complete hook payload may contribute to a one-way idempotency hash, but it is not saved by the ledger.

Conversation memory remains a separate feature with its existing raw-audit and distillation controls.

## Client coverage

HoloCore installs activity hooks for Claude Code and Codex events supported by their local hook systems, including session, prompt, tool, subagent, stop, and session-end events. Gemini CLI, Cursor, and OpenCode receive MCP connections; their activity coverage can expand when equivalent stable lifecycle hooks are available.

Hooks are non-blocking. A ledger, transcript, code-map, or update-check failure must not prevent the parent AI client from continuing or ending its session.

## Plain-language names

The current user interface uses **Project**, **Knowledge**, **Code Map**, **Activity**, **Memory**, **Item**, **Cluster**, and **Agent Run**. Older internal paths and APIs retain `World`, `Archive`, `Atlas`, `Animus`, `Memory Shard`, `Signal`, and `Constellation` for backward compatibility.
