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
| `holocore-setup` | Select/register Home and World, build Atlas, connect clients, install capture | Yes |
| `holocore-init` | Initialize storage and integration without building Atlas | Yes |
| `holocore-connect` | Add or repair project-local connections and hooks | Yes |
| `holocore-home` | Show the selected shared Home and Archive | No |
| `holocore-worlds` | List registered Worlds | No |
| `holocore-global-graph` | Build an Atlas-only graph across registered Worlds | Yes |
| `holocore-sync-all` | Reconcile every World and ensure Atlas freshness | Yes |
| `holocore-update` | Update HoloCore from Git and reconcile every World | Yes |
| `holocore-paths` | Print every resolved data and integration path | No |
| `holocore-open-archive` | Open `<Home>/Knowledge` | No data write |
| `holocore-status` | Report Home, World, Atlas, Animus, client, and capture health | No |
| `holocore-search` | Run check-first Atlas → World/Shared Archive → optional Animus search | Generated Atlas refresh when stale |
| `holocore-archive-search` | Search active World and Shared durable notes | No |
| `holocore-archive-create` | Create an active-World note or use `shared/` for Shared | Yes |
| `holocore-atlas-refresh` | Rebuild the active World's graph | Yes |
| `holocore-atlas-view` | Generate and open the active World's HTML graph | Yes |
| `holocore-atlas-explain` | Explain one structural Signal and its evidence | No |
| `holocore-atlas-path` | Find a shortest relationship path | No |
| `holocore-atlas-affected` | Find affected Signals | No |
| `holocore-atlas-neighborhood` | Inspect a bounded Signal neighborhood | No |
| `holocore-atlas-constellations` | List deterministic Atlas communities | No |
| `holocore-atlas-audit` | Audit unresolved and duplicate graph relationships | No |
| `holocore-atlas-export` | Export Atlas JSON, HTML, Markdown, and manifest | Yes |
| `holocore-remember` | Store one explicit Memory Shard | Yes |
| `holocore-recall` | Recall scoped project history | No |
| `holocore-animus-sync` | Incrementally mine the active World | Yes |
| `holocore-animus-checkpoint` | Inspect the last mining checkpoint | No |
| `holocore-diary` | Record an episodic diary entry | Yes |
| `holocore-timeline` | Read the episodic timeline | No |
| `holocore-consolidate` | Consolidate duplicate diary records | Yes |
| `holocore-animus-export` | Export scoped Animus records | No |
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
