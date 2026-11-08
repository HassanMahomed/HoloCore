# HoloCore capabilities (v1.0 release candidate)

This page describes what users can do in the current version and its practical boundaries.

| Area | Available now | Boundaries |
|---|---|---|
| Projects and Home | Register projects in one shared HoloCore Home; keep each project's code map and runtime scoped to that project | Project roots must remain available at their registered paths |
| Knowledge | Search, create, edit, validate, and export durable project notes; share selected notes across projects | Conflicting shared notes require an explicit choice |
| Code Map | Map files, symbols, and relationships; search CamelCase and path concepts; detect weighted feature communities; generate evidence-backed community notes; explain, trace paths, find affected items, audit, and export the map | Community knowledge is generated under Code Map output and is not promoted into curated Knowledge automatically; extraction is structural, not a full compiler |
| Memories | Save and recall useful details; search, filter, paginate, inspect provenance/privacy, review timelines, and explicitly forget retained items in the local Console | Recall and deletion are scoped to the active project; forgetting a memory does not delete its original source file; embeddings require a configured provider |
| Local models | Discover installed Ollama models on loopback, explicitly select one at Home scope, inspect health, and fall back to deterministic extraction | HoloCore does not install runtimes, download models, scan the LAN, or manage GPUs |
| Chat History | Capture supported Claude Code and Codex session events and transcript checkpoints | Other clients can connect to HoloCore but do not yet have equivalent capture hooks |
| Agent Activity | Review agent runs, actions, status, timestamps, tools, and affected files across Claude Code, Codex, Cursor, Gemini CLI, and OpenCode; find activity by file | Cursor, Gemini CLI, and OpenCode contribute activity only; prompts, full tool payloads, credentials, and secrets are excluded |
| Agent Handoffs | Create work linked to an agent run, list it, claim it once, then complete or abandon it through the CLI or MCP | A handoff contains sanitized recorded context and its short user-authored note |
| AI connections | Detect supported clients and offer non-destructive connections; upgrades rescan for newly installed clients | Client trust prompts remain controlled by each AI application |
| Updates | Update the CLI and reconcile registered projects; optionally check once per day during real HoloCore activity | Automatic updates are session-driven and do not run a resident service or scheduled task |
| Console | Browse chat history, memories, knowledge, code maps, agent activity, commands, locations, and setup status in a local browser view | Console data stays on the machine and is scoped to the selected project |
| Research and architecture | Build read-only evidence briefs from Code Map structure, verified Knowledge, and scoped Memories through the Console, CLI, or MCP | Generated findings remain drafts until a user explicitly saves a reviewed brief into Knowledge |
| Packaging | Double-click the standalone native Windows setup executable, use a fail-closed signed release-manifest installer for managed deployment, roll back to the retained previous release, uninstall native application files through `holocore uninstall`, or install from Git with `uv` | Stable tagged publication requires configured Authenticode credentials and a successful signed lifecycle workflow |
| Integrations | Use generated commands, skills, MCP tools, and supported capture hooks | Remote MCP transport and server authentication are not included |

## Plain labels and compatibility

The Console and user guides use **Project**, **Knowledge**, **Code Map**, **Memories**, **Chat History**, **Agent Activity**, **Item**, and **Cluster**. Older CLI names, MCP fields, database tables, and storage paths remain supported for compatibility. They may still use technical terms such as `Atlas`, `Animus`, `World`, `Signal`, or `Constellation`.

No item in the Boundaries column is an available feature or compatibility promise.
