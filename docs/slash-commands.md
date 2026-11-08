# Slash-command and skill reference

`holocore setup` generates client-native commands non-destructively:

- Claude: project slash commands and project skills.
- Codex: `$`-invoked project skills under `<project>/.agents/skills`.
- Gemini: TOML command definitions.
- Cursor and OpenCode: native project command files.

The generated command or skill delegates to the stable HoloCore CLI.

Claude also receives a top-level `/holocore` orientation command. It reports
status and paths and points to the specific commands below. Use `/holocore-search`
for a focused knowledge search and `/holocore-doctor` for MCP diagnostics.

| Command or skill | Purpose | Writes |
|---|---|---|
| `holocore-setup` | Select/register Home and Project, build the Code Map, connect clients, install capture | Yes |
| `holocore-init` | Initialize storage and integration without building the Code Map | Yes |
| `holocore-connect` | Add or repair project-local connections and hooks | Yes |
| `holocore-home` | Show the selected shared Home and Archive | No |
| `holocore-worlds` | List registered Worlds | No |
| `holocore-global-graph` | Build one Code Map across registered Projects | Yes |
| `holocore-sync-all` | Reconcile every Project and ensure Code Map freshness; use `--regenerate` after a naming upgrade | Yes |
| `holocore-update` | Update HoloCore from Git and reconcile every World | Yes |
| `holocore-paths` | Print every resolved data and integration path | No |
| `holocore-open-archive` | Open `<Home>/Knowledge` | No data write |
| `holocore-status` | Report Home, Project, Code Map, Memories, client, and capture health | No |
| `holocore-search` | Run check-first Code Map → Project/Shared Knowledge → optional Memories search | Generated Code Map refresh when stale |
| `holocore-research` | Build a read-only research evidence brief | No |
| `holocore-architecture` | Build a read-only architecture evidence brief | No |
| `holocore-archive-search` | Search active World and Shared durable notes | No |
| `holocore-archive-create` | Create an active-World note or use `shared/` for Shared | Yes |
| `holocore-code-map-refresh` | Rebuild the active Project's Code Map | Yes |
| `holocore-code-map-view` | Generate and open the active Project's HTML Code Map | Yes |
| `holocore-code-map-explain` | Explain one mapped item and its evidence | No |
| `holocore-code-map-path` | Find a shortest relationship path | No |
| `holocore-code-map-affected` | Find affected mapped items | No |
| `holocore-code-map-neighborhood` | Inspect a bounded item neighborhood | No |
| `holocore-code-map-groups` | List deterministic Code Map groups | No |
| `holocore-code-map-audit` | Audit unresolved and duplicate relationships | No |
| `holocore-code-map-export` | Export Code Map JSON, HTML, Markdown, and manifest | Yes |
| `holocore-remember` | Store one explicit Memory | Yes |
| `holocore-recall` | Recall scoped project history | No |
| `holocore-memory-collect` | Collect new Project or conversation details into Memories | Yes |
| `holocore-memory-checkpoint` | Inspect the last collection checkpoint | No |
| `holocore-memory-event` | Record a remembered project event | Yes |
| `holocore-memory-timeline` | Read the Memories timeline | No |
| `holocore-memory-consolidate` | Consolidate duplicate remembered events | Yes |
| `holocore-memory-export` | Export scoped Memories | No |
| `holocore-memory-groups` | List focused Memory groups | No |
| `holocore-memory-track` | Record a change to a named project item | Yes |
| `holocore-memory-history` | Show the remembered history for a named project item | No |
| `holocore-doctor` | Run diagnostics | No |

## Claude Code

After setup:

1. Restart Claude.
2. Run `/mcp`.
3. Approve or confirm HoloCore.
4. Invoke `/holocore-search`.

The generated MCP prompt is also available as `/mcp__holocore__search`. Claude automatic capture is a separate `SessionEnd` hook installed by setup.

## Codex

After setup:

1. Restart or reopen the project.
2. Run `/hooks`.
3. Review and trust the HoloCore Stop hook.
4. Invoke `$holocore-search`.

Codex does not use HoloCore slash commands. Details following a `$holocore-*` skill mention are interpreted as arguments for the corresponding CLI command.

Generated write workflows tell the AI client to confirm scope before execution. Client approval remains required even though HoloCore writes the integration files automatically.

Legacy `animus-*` and `atlas-*` CLI/MCP names remain callable as compatibility aliases. New and regenerated integrations advertise only the plain `memory-*` and `code-map-*` names. Run `holocore sync-all --regenerate` once after upgrading an existing installation.
