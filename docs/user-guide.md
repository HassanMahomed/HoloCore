# User guide

## Mental model

- **HoloCore Home is the shared bookshelf.** You choose one location for all durable knowledge.
- **Projects** keep their own code and activity context.
- **Knowledge** holds verified notes and decisions. `<Home>/Knowledge` is the optional Obsidian-ready view.
- **Shared knowledge** contains notes intentionally useful across projects.
- **The Code Map** shows project files, symbols, and relationships.
- **Memories** help an AI assistant recall useful details from earlier work and conversations.
- **HoloCore** checks readiness, uses the Code Map and relevant Knowledge, then searches Memories only when prior history matters.

Older technical names remain in storage and APIs for compatibility: `World` means project, `Atlas` means Code Map, `Archive` means Knowledge store, and `Animus` means memory store.

See the [visual guide](visual-guide.md) for illustrated versions of these workflows.

## What lives where

| Location | Owner | Contents |
|---|---|---|
| `<Home>/Knowledge` | You | One visible Markdown/Obsidian vault |
| `<Home>/Knowledge/Projects/<project-id>` | One project | Verified notes and promoted memories |
| `<Home>/Knowledge/Shared` | Shared by choice | Reusable cross-project knowledge |
| `<Home>/worlds.json` | HoloCore | Registered project IDs and roots |
| `<Home>/Projects/<project-id>/CodeMap/graph.json` | HoloCore | Machine-readable project structure used by AI clients |
| `<Home>/Projects/<project-id>/CodeMap/atlas.html` | HoloCore | Interactive Code Map for people |
| `<Home>/Memories/animus.db` | HoloCore | Shared memory and agent activity database |
| `<Home>/Memories/ChatHistory/<project-id>` | HoloCore | Original conversation audits |
| `<Home>/Connections` | HoloCore | Shared AI-client connections and commands |

The Home knowledge vault is visible and user-owned. Project runtime data is generated and can contain sensitive history. The Code Map respects Git ignore rules, so ignored reference applications, build output, and local configuration do not flood the map.

## First setup

```powershell
cd <project>
holocore setup --home <Home>
```

Later projects use the saved Home:

```powershell
cd <another-project>
holocore setup
```

Useful orientation commands:

```powershell
holocore home
holocore projects
holocore paths
holocore status
```

## Approve automatic client access

Setup installs connections and capture hooks, but the client asks you to trust them.

- **Claude Code:** restart the project, run `/mcp`, and approve or confirm HoloCore. Conversation capture runs at `SessionEnd`.
- **Codex:** restart or reopen the project, run `/hooks`, and trust the HoloCore hook. Conversation capture runs at `Stop`.

After approval, Claude can use `/holocore-search` and Codex can use `$holocore-search`.

## Everyday retrieval

```powershell
holocore search "Which files implement authentication?"
holocore search "What happened the last time login failed?"
holocore map search "authentication"
holocore knowledge search "authentication policy"
holocore memory search "previous login failure"
```

Unified search follows one bounded route:

1. Check required paths and Code Map freshness.
2. Refresh the Code Map when unified search finds it stale.
3. Search the Code Map once.
4. Search the active project's Knowledge and `Shared` once using map hints.
5. Search project Memories once only when the question needs history.
6. Return labelled evidence for exact-source inspection.

Other projects are not searched merely because they share the same vault.

## Saving knowledge

Store a concise event in Memories:

```powershell
holocore remember "Decision: keep the local keyless baseline" --sector project --source "meeting"
```

Import an exported chat manually:

```powershell
holocore ingest-chat <chat-export.json>
```

Create a durable note for the active project:

```powershell
holocore archive-create "wiki/deployment.md" "Verified deployment procedure."
```

Create a cross-project note by using the `shared/` prefix:

```powershell
holocore archive-create "shared/wiki/naming.md" "Naming rule shared by all Worlds."
```

Use Shared only when the knowledge is genuinely cross-project. Normal knowledge notes and automatic memory promotion target the active project.

## What automatic capture does

At Claude `SessionEnd` or Codex `Stop`, HoloCore:

1. Reads only new user and assistant messages from the transcript.
2. Stores a raw audit inside the project.
3. Distills summary, facts, decisions, preferences, and entities in one local or configured provider pass.
4. Deduplicates and saves useful memories in HoloCore's shared local memory store.
5. Promotes useful extraction into the active project's knowledge folder.
6. Checks the Code Map and refreshes it if project files changed.

The transcript cursor advances only after successful ingestion. Hook failures are reported without blocking the parent AI client.

Automatic does not mean unreviewed knowledge becomes global. Promotion is limited to the active project, and `Shared` always requires an explicit target.

## Keep registered projects current

```powershell
holocore sync-all
```

This reruns non-destructive integration setup and ensures Code Map freshness for every available project in `<Home>/worlds.json`.

```powershell
holocore update
```

This updates HoloCore, rescans supported AI clients, then reconciles registered projects.

## Open the visual views

Open the shared knowledge vault:

```powershell
holocore open-archive
```

Or open `<Home>/Knowledge` manually in Obsidian.

Generate and open the active project's Code Map:

```powershell
holocore atlas-view
```

The knowledge vault and Code Map answer different questions: Knowledge links notes; the Code Map shows project structure. Open `<Home>/Knowledge` in Obsidian for the Markdown view, or use the portable Code Map in a browser.

## Technical names kept for compatibility

| Term | Simple definition |
|---|---|
| **Archive** | Verified knowledge |
| **Atlas** | Structural map |
| **Animus** | Remembered history |
| **World** | Project |
| **Sector** | Area inside a project |
| **Memory Shard** | Raw remembered fragment |
| **Archive Entry** | Polished durable note |
| **Signal** | One mapped thing |
| **Constellation** | Group of related mapped things |

## Current limits

- Unified search is deterministic and keyword-gated rather than LLM-planned.
- Code Map extraction is richest for Python; other files receive generic structural items.
- `mine` recursively stores eligible whole-file content, so choose a narrow source.
- Remote memory refinement requires an explicitly configured OpenAI-compatible provider; the local fallback remains the default.
- Raw chats and memories can contain sensitive information; protect the corresponding HoloCore Home folders.
- Selecting another Home does not move the old knowledge vault or project registry automatically.

See [capability status](capability-status.md) before relying on a new surface.
