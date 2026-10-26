# Prerequisites and optional tools

HoloCore works as a standalone local application. Obsidian, Graphify, MemPalace, an AI client, and a remote LLM provider are not required for the core CLI.

## Required

| Requirement | Why it is needed |
|---|---|
| `uv` | Installs, updates, and isolates the HoloCore CLI and MCP server |
| A terminal | Runs setup and lifecycle commands |
| A user-selected writable `<Home>` | Stores the one shared Archive and World registry |
| Write permission in each `<project>` | Stores local Atlas, Animus, raw chats, and client integration |

Windows with PowerShell is the first-class validated environment. Other operating systems may work but currently receive less testing.

## Optional

| Tool | When you need it |
|---|---|
| Git | Installs from the repository URL and supports normal project workflows |
| Python 3.11 or newer | Source-checkout development only; `uv tool install` manages normal installation |
| Web browser | Opens project-local Atlas HTML |
| Obsidian | Browses the one linked Markdown Archive vault |
| AI client | Uses MCP, generated commands, skills, and automatic capture |
| OpenAI-compatible LLM | Optional higher-quality conversation extraction; local keyless extraction is the default |

## Choose the Home carefully

The Home should be a stable, user-owned directory with enough space for all durable notes:

```text
<Home>/
├── Archive/
└── worlds.json
```

The first interactive setup asks for this location, or you can pass it explicitly:

```powershell
holocore setup --home <Home>
```

If no Home has been selected and setup is non-interactive, HoloCore uses `%USERPROFILE%\HoloCore` on Windows or `~/HoloCore` elsewhere.

HoloCore remembers the choice. Selecting another Home later does not move existing data.

## Which graph should I open?

HoloCore provides two related views.

### Atlas: one World's structure

Atlas maps files, code symbols, and relationships for the active project:

```powershell
cd <project>
holocore atlas-view
```

The main graph is generated at `<project>/holocore-out/atlas.html` and opens in a normal browser. The legacy path `<project>/.holocore/atlas.html` is refreshed as an identical compatibility copy. AI clients consume `<project>/holocore-out/graph.json` directly. Existing `graphify-out` folders are migrated automatically when HoloCore next refreshes Atlas.

The viewer uses a force-directed layout with collision spacing rather than placing everything in a circle. It shows only the most important labels until you search, hover, select a Signal, or enable **More labels**. Use **Fit**, zoom, drag-to-pan, and type/relation filters to inspect dense Worlds.

### Archive: durable knowledge across Worlds

Archive contains verified Markdown notes:

1. Install Obsidian.
2. Choose **Open folder as vault**.
3. Select `<Home>/Knowledge`.
4. Open Obsidian's Graph view.

Use this single vault root. `Worlds/<world-id>` keeps project knowledge separated, while `Shared` holds explicitly reusable notes.

## AI-client trust

Project setup writes integration files, but the AI client remains in control:

- Claude Code requires approval or confirmation through `/mcp`.
- Codex requires review and trust of the automatic Stop hook through `/hooks`.

Automatic conversation capture is implemented for Claude `SessionEnd` and Codex `Stop`.

## First-run readiness check

```powershell
uv tool install "git+https://github.com/VenomD846/HoloCore.git"
cd <project>
holocore setup --home <Home>
holocore doctor
holocore paths
```

Then restart the AI client, complete its trust step, and run `holocore status` again.
