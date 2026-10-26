# HoloCore capabilities (v0.12.0 development)

This page describes what users can do in the current version and its practical boundaries.

| Area | Available now | Boundaries |
|---|---|---|
| Projects and Home | Register projects in one shared HoloCore Home; keep each project's code map and runtime scoped to that project | Project roots must remain available at their registered paths |
| Knowledge | Search, create, edit, validate, and export durable project notes; share selected notes across projects | Conflicting shared notes require an explicit choice |
| Code Map | Map files, symbols, and relationships; search, explain, trace paths, find affected items, audit, cluster, and export the map | Extraction is structural, not a full compiler; language detail varies |
| Memories | Save and recall useful details from earlier work, decisions, and conversations; inspect timelines and remove duplicates | Recall is scoped to the active project; embeddings require a configured provider |
| Chat History | Capture supported Claude Code and Codex session events and transcript checkpoints | Other clients can connect to HoloCore but do not yet have equivalent capture hooks |
| Agent Activity | Review agent runs, actions, status, timestamps, tools, and affected files; find activity by file | Prompts, full tool payloads, credentials, and secrets are excluded from the activity ledger |
| Agent Handoffs | Create work linked to an agent run, list it, claim it once, then complete or abandon it through the CLI or MCP | A handoff contains sanitized recorded context and its short user-authored note |
| AI connections | Detect supported clients and offer non-destructive connections; upgrades rescan for newly installed clients | Client trust prompts remain controlled by each AI application |
| Updates | Update the CLI and reconcile registered projects; optionally check once per day during real HoloCore activity | Automatic updates are session-driven and do not run a resident service or scheduled task |
| Console | Browse chat history, memories, knowledge, code maps, agent activity, commands, locations, and setup status in a local browser view | Console data stays on the machine and is scoped to the selected project |
| Packaging | Install on Windows with the PowerShell installer and downloadable executable, or install from Git with `uv` | Public package-registry distribution and signed installers are not promised |
| Integrations | Use generated commands, skills, MCP tools, and supported capture hooks | Remote MCP transport and server authentication are not included |

## Plain labels and compatibility

The Console and user guides use **Project**, **Knowledge**, **Code Map**, **Memories**, **Chat History**, **Agent Activity**, **Item**, and **Cluster**. Older CLI names, MCP fields, database tables, and storage paths remain supported for compatibility. They may still use technical terms such as `Atlas`, `Animus`, `World`, `Signal`, or `Constellation`.

No item in the Boundaries column is an available feature or compatibility promise.
