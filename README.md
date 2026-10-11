# HoloCore Context Engine

![HoloCore Context Engine icon](docs/assets/holocore-icon.png)

**HoloCore Context Engine** is a local AI knowledge and activity system for all your projects. Version `0.9.0` combines verified project knowledge, a structural code map, conversation memory, searchable agent impact history, and lightweight cross-agent handoffs behind one CLI, MCP server, and browser Console.

![HoloCore Context Engine checks Atlas, reads matching Archive knowledge, optionally recalls Animus history, and sends focused context to reduce input tokens](docs/assets/holocore-context-engine-token-savings.png)

![HoloCore Home and World storage model](docs/assets/holocore-home-world-structure.svg)

The runtime is HoloCore-native. It does not import, launch, or require the original Obsidian Second Brain, Graphify, or MemPalace applications. Those projects were behavioral references for the rewrite and are not runtime components.

## In simple terms

HoloCore gives an AI assistant three kinds of context, using plain names in the user interface:

- **Knowledge:** verified notes, rules, and decisions.
- **Code Map:** files, functions, dependencies, and relationships.
- **Activity Memory:** previous work, conversations, errors, and useful events.

You choose one **HoloCore Home**. Its `Archive` folder is one Obsidian-ready vault shared by every registered project. Internal storage keeps the older `Archive`, `Atlas`, `Animus`, and `World` names so existing installations and integrations remain compatible.

```text
<Home>/
├── Archive/                       One Obsidian vault
│   ├── Worlds/<world-id>/         Durable knowledge for one project
│   ├── Shared/                    Knowledge intentionally shared by projects
│   └── system/index.md            Vault entry point
└── worlds.json                    Registered World list

<Home>/
├── Animus/animus.db                Shared SQLite memory and chat history
├── Animus/raw-chats/<world>/        Immutable original chat audits
└── Projects/<world>/Atlas/         Per-World graph.json and atlas.html
```

Archive is shared as a vault, but retrieval remains scoped: HoloCore searches the active World's entries and `Shared`, not every other World.

## How retrieval works

HoloCore builds one route and executes it once:

`readiness check → Atlas → active World Archive + Shared → Animus when history matters → exact sources`

`holocore search` checks Atlas freshness and refreshes the local graph when required. Atlas narrows the project scope first. Archive adds verified knowledge. Animus runs last only for questions about earlier work, errors, attempts, or conversations. Every selected subsystem runs at most once, and a recursion guard prevents a HoloCore search from routing back into itself.

## Benchmark results

On the HoloCore repository, the corrected relevance-gated route returned fewer estimated retrieval tokens than Graphify-only:

| Retrieval mode | Estimated tokens/query |
|---|---:|
| Naive full corpus | ~133,866 |
| Graphify-only | ~242 |
| HoloCore | ~156 |

HoloCore used approximately 35.5% fewer retrieval tokens than Graphify across five code questions. Code queries used `check → Atlas`; architecture and prior-work queries used bounded `check → Atlas → Archive → Animus` routes. Full timings and reproduction details are in the [plain-text benchmark results](benchmarks/holocore-vs-graphify-2026-07-16.txt).

To view every registered project's structural graph in Obsidian, run `holocore obsidian-vault`, then open the generated `HoloCore Obsidian Vault` folder inside HoloCore Home as the Obsidian vault.

The shared Obsidian view can refresh automatically. `holocore watch --install --home "C:\\Brains\\Holocore Home"` installs a Windows logon trigger; it polls registered Worlds every 30 seconds, reconciles changed Worlds, and regenerates `HoloCore Obsidian Vault`. The watcher reads World sources and Atlas inputs only, so its generated Markdown cannot trigger a refresh loop. `holocore watch --uninstall` removes the trigger.

## Automatic memory and agent activity

Setup installs local capture hooks for supported clients:

- Claude Code and Codex record session, prompt, tool, subagent, stop, and session-end events supported by the client.
- Conversation content is captured at safe transcript checkpoints.
- The activity ledger records the agent, action, status, time, and affected file paths without storing tool payloads, prompts, secrets, or authorization values.

The hook reads only new transcript content, stores a raw audit under the project, distills useful Memory Shards into local Animus, and automatically promotes useful facts, decisions, preferences, entities, and summaries into the active World's Archive. Promotion uses deterministic names and deduplication so the same extraction is not repeatedly added.

Hooks are installed automatically but require client trust once:

- In Claude Code, restart the project and use `/mcp` to approve or confirm the HoloCore server.
- In Codex, restart or reopen the project and use `/hooks` to review and trust the HoloCore Stop hook.

## One-line Windows install

Open PowerShell and run:

```powershell
irm https://raw.githubusercontent.com/HassanMahomed/HoloCore/main/install.ps1 | iex
```

The installer downloads the release asset from GitHub, verifies its SHA-256 checksum when available, creates HoloCore Home, adds `holocore` to the user PATH, detects supported AI clients, and asks which detected clients to connect. No Python installation is required. On upgrades, HoloCore scans again so clients installed later can be connected to every registered project.

The downloadable Windows executable and checksum are also available on the [GitHub Releases page](https://github.com/HassanMahomed/HoloCore/releases). See the [Windows installation guide](docs/windows-installation.md) for unattended options, upgrades, and removal.

## Developer install

Install from Git:

```powershell
uv tool install "git+https://github.com/HassanMahomed/HoloCore.git"
```

Set up the first project and explicitly choose the shared Home:

```powershell
cd <project>
holocore setup --home <Home>
```

Later projects reuse the saved Home:

```powershell
cd <another-project>
holocore setup
```

On an interactive first run without `--home`, setup asks where the shared Home should live. `holocore home <Home>` selects or changes it explicitly.

After setup:

```powershell
holocore status
holocore paths
holocore worlds
holocore search "Why did we choose SQLite?"
holocore console                    # open the local browser Console
holocore connections                # installed versus connected AI clients
holocore activity affected "app.py" # agents and actions that touched an item
holocore handoff                    # bounded summary for the next agent
```

The Console brings conversations, memories, verified knowledge, the code map,
agent runs, affected items, storage locations, and AI commands into one local
view. Knowledge edits are validated; captured conversations remain immutable.

Use the World selector in the Console header to move between registered
projects without restarting the server. The Commands tab is grouped by
purpose; the other tabs are scoped to the selected World.

Console panels and triggers:

- **Conversations** — captured provider transcripts, dates, sources, and searchable text.
- **Activity** — memories, focused collections, history, provenance, and episodic context.
- **Knowledge** — editable Markdown notes with managed/user-owned provenance and source links.
- **Code Map** — project items, relationships, paths, explanations, and clusters.
- **Agent Runs** — filter each agent and subagent run by agent, action, status, tool, or affected item; see handoff totals at a glance.
- **Locations** — exact Home, project, memory, knowledge, code-map, raw-chat, and connection paths.
- **AI Commands** — commands grouped by effect for Claude, Codex, and MCP clients.
- **Maintenance** — ingest, resync, cleanup preview, diagnostics, migration, and freshness status.

The older technical names remain accepted by the CLI and MCP API for backward compatibility, but new user-facing documentation prefers project, knowledge, code map, activity, memory, item, and cluster.

Open `<Home>/Archive` as one Obsidian vault if you want Obsidian's Markdown and graph interface. Obsidian is optional; HoloCore works from the CLI and AI clients without it.

## Keep every project current

```powershell
holocore sync-all
holocore update
```

`sync-all` reconciles generated integration files and refreshes Atlas for every registered World. `update` upgrades the installed CLI and then performs the same all-World reconciliation. For a large installation, update the CLI first and reconcile separately:

```powershell
holocore update --no-sync
holocore sync-all
```

The second command can take time because it reads each registered World's source files and rebuilds its Atlas. Press `Ctrl+C` to stop a refresh safely; the installed CLI remains updated. Close active Claude/Codex MCP sessions before updating if Windows reports that `holocore.exe` is in use. Missing project folders are reported without stopping other Worlds.

Optional automatic updates are deliberately session-driven, not scheduled:

```powershell
holocore auto-update --enable
holocore auto-update                 # show status
holocore auto-update --check-now
```

HoloCore checks GitHub at most once per day after it records real activity. Offline failures are quiet and retried later. If an update is available, it is installed only after the AI session ends. There is no resident background process and no Windows Scheduled Task.

## AI clients

Setup detects Claude Code, Codex, Gemini CLI, Cursor, and OpenCode, then asks which detected clients to connect. Setup creates non-destructive project integrations:

- Claude receives `.mcp.json`, project slash commands and skills, plus a `SessionEnd` capture hook.
- Codex receives `.codex/config.toml`, `$`-invoked skills under `.agents/skills`, plus a `Stop` capture hook.
- Gemini, Cursor, and OpenCode receive their native project MCP and command formats.

Use `holocore detect-clients` to inspect detection, `holocore connections` to distinguish installed apps from working HoloCore connections, and `holocore connect` to add or repair integrations after installing another client. `holocore update` and the Windows upgrade flow rescan automatically. Existing unrelated settings are preserved; invalid files are reported and skipped rather than overwritten.

## Legacy/internal vocabulary

Existing installations, paths, and APIs keep these mappings: **Archive = Knowledge**, **Atlas = Code Map**, **Animus = Activity Memory**, **World = Project**, **Sector = Area**, **Memory Shard = Memory**, **Archive Entry = Knowledge Note**, **Signal = Item**, **Constellation = Cluster**, **Deck = Collection**, and **Chronicle = History**.

## Documentation

![HoloCore parity release workflow](docs/assets/workflow-parity-release.svg)

The current release includes Atlas clustering, audits, explain/path analysis and exports; Animus mining, checkpoints, diary, consolidation and optional embedding retrieval; conflict-aware Archive promotion; native AI-client integration generation; and automated interface/parity checks. HoloCore's HTML Atlas is portable and AI-friendly. For the richest visual knowledge graph, open the shared `Archive` vault in Obsidian.

- [Visual guide](docs/visual-guide.md)
- [User guide](docs/user-guide.md)
- [Installation guide](docs/installation.md)
- [One-line Windows installation](docs/windows-installation.md)
- [Agent activity ledger](docs/agent-activity.md)
- [What is new in v0.9](docs/v0.9.md)
- [Prerequisites and optional graph tools](docs/prerequisites.md)
- [Workflow guide](docs/workflows.md)
- [Slash-command and skill reference](docs/slash-commands.md)
- [Configuration guide](docs/configuration.md)
- [MCP reference](docs/mcp-reference.md)
- [Architecture and technical guide](docs/architecture.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Portability and AI-client guide](docs/portability-ai-clients.md)
- [Capability status](docs/capability-status.md)
- [Product identity and discovery tags](BRANDING.md)
- [Acknowledgments and third-party provenance](THIRD_PARTY_NOTICES.md)

## Safety model

HoloCore is local-first. Archive Markdown stays in the user-selected Home. Atlas, Animus, transcript cursors, and raw chats stay inside each project. Setup and reconciliation merge only HoloCore-owned integration entries. Capture hooks are non-blocking and should never prevent the parent AI client from ending a session.

Raw chats and Animus can contain sensitive information. HoloCore adds their paths to the project `.gitignore`; protect and back them up according to your own data policy.

## License and development

HoloCore is licensed under the [Apache License 2.0](LICENSE). Third-party behavioral inspirations and their licenses are recorded in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Development verification:

```powershell
$env:PYTHONPATH = "src"
python -m pytest -q
uv build
```
