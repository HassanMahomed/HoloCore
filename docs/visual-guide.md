# HoloCore visual guide

This page explains HoloCore `0.5.0` in everyday language. You do not need to understand Python, databases, MCP, or knowledge graphs.

## HoloCore in one sentence

HoloCore gives every project a local map and memory while keeping verified knowledge in one shared, user-selected library.

## The whole system

![One HoloCore Home contains a shared Archive vault while each World keeps local Atlas, Animus, and raw chats](assets/holocore-overview.svg)

Think of the selected **HoloCore Home** as one building:

- **Archive** is the building's library.
- **Worlds** are project rooms in that library.
- **Shared** is the shelf that every project may use.
- **Atlas** is a blueprint kept at each project site.
- **Animus** is a diary kept at each project site.
- **HoloCore Engine** is the coordinator that knows where to look.

| Part | Simple comparison | What it keeps | Where it lives |
|---|---|---|---|
| **HoloCore Home** | One shared building | Archive vault and World registry | `<Home>` |
| **World Archive** | One project's library room | Verified notes and promoted memory | `<Home>/Knowledge/Projects/<world-id>` |
| **Shared** | Common shelf | Explicit cross-project knowledge | `<Home>/Knowledge/Shared` |
| **Atlas** | Project blueprint | Files, symbols, dependencies, paths, clusters, audits, exports | `<project>/graphify-out` |
| **Animus** | Project diary | Previous work and refined conversations | `<project>/.holocore` |
| **Raw chat audit** | Original evidence box | Source conversations before refinement | `<project>/.holocore/raw-chats` |

Archive is one Obsidian-ready vault, but HoloCore does not mix every project together during search. It reads the active World and `Shared`; other World sections stay out of scope.

## How installation works

![Installation selects one Home, registers the World, installs local runtime and client hooks, then asks for client trust](assets/workflow-install-ai.svg)

### Step 1 — Install once

Install HoloCore through `uv`. This provides the `holocore` command and local MCP server.

### Step 2 — Choose one Home

During first setup, choose `<Home>`. HoloCore remembers it for later projects.

```powershell
holocore setup --home <Home>
```

### Step 3 — Register a World

The current project receives a generated World ID and an Archive section:

`<Home>/Knowledge/Projects/<world-id>`

The Home registry records the project path. Durable Shared knowledge remains at `<Home>/Knowledge/Shared`.

### Step 4 — Prepare local project state

The project receives local Atlas, Animus, raw chats, capture cursor, start-here guide, commands, skills, and MCP configuration.

### Step 5 — Approve access in the client

Setup installs the files, but the client asks for trust:

- Claude Code: restart, use `/mcp`, and approve or confirm HoloCore.
- Codex: restart or reopen, use `/hooks`, and trust the HoloCore Stop hook.

This keeps the user in control of local command and server execution.

## How unified search works

## The global Atlas: a 3D World system

The Console's **Solar System Atlas** is a self-contained WebGL view. Each
registered project is a World body; its Signals orbit that body, while detailed
structural relationships remain available in the selected World Atlas. Use the
World switcher on the left or click a World in the canvas to fly toward it.

- drag the canvas to orbit the camera;
- use the wheel to change camera distance;
- click a World to focus it, then choose another World to return to the system;
- the layout uses deterministic orbital placement with bounded repulsion and
  gravity-like centre pull, so a large graph stays readable and repeatable.

This is the HoloCore adaptation of the reviewed galaxy-view, galaxy-explorer,
and galaxy_sim references: Worker-force/selection ideas from the Obsidian graph,
cinematic dark-space presentation from the explorer, and GPU-friendly orbital
physics from the simulator. No external CDN or runtime is required.

![HoloCore checks readiness and freshness before Atlas, active World and Shared Archive, optional Animus, and exact sources](assets/workflow-unified-search.svg)

### Step 1 — Ask one question

For example:

> Why did we choose SQLite, and which files depend on it?

### Step 2 — Check first

HoloCore checks required World paths and Atlas freshness. Normal unified search refreshes the project-local Atlas when it is missing or stale.

### Step 3 — Atlas narrows the project

Atlas runs once to find relevant files, functions, classes, and relationships.

### Step 4 — Archive adds trusted knowledge

Archive runs once using the original question plus bounded Atlas hints. It searches:

- the active World's durable notes;
- `Shared` notes.

It does not search every other World.

### Step 5 — Animus runs only when history matters

Questions containing ideas such as “previous,” “again,” “earlier,” “error,” or “last time” activate one project-local Animus lookup. Other questions skip it.

### Step 6 — Open exact sources

HoloCore returns labelled evidence. The AI opens only the exact notes and project files identified by the route.

### Why it cannot loop

The route is built once and moves in one direction. A context-local guard rejects the same HoloCore search calling itself. Atlas, Archive, and Animus each run at most once.

![Without HoloCore the AI receives the whole project history; HoloCore checks Atlas, reads matching Archive knowledge, optionally recalls Animus, and sends focused context](assets/holocore-context-engine-token-savings.png)

The hero image still shows the central retrieval idea: focused context instead of all files, chats, and notes.

## How automatic conversation memory works

![Claude SessionEnd and Codex Stop capture new transcript content, preserve raw evidence, refine local Animus memory, and promote useful knowledge](assets/workflow-memory-refinement.svg)

### Step 1 — A supported client reaches its hook

- Claude Code triggers `SessionEnd`.
- Codex triggers `Stop`.

Manual `ingest-chat` uses the same refinement path.

### Step 2 — Read only new messages

HoloCore remembers a byte cursor for each transcript. It reads only new user and assistant messages, ignores tool/system/analysis records, and removes adjacent duplicates.

### Step 3 — Preserve raw evidence

The original captured conversation is written under the active project. It can be audited or reprocessed later.

### Step 4 — Refine once

HoloCore uses either:

- deterministic local extraction; or
- one configured OpenAI-compatible provider call.

One pass produces summary, facts, decisions, preferences, and entities.

### Step 5 — Store local Memory Shards

Animus deduplicates the extraction, attaches provenance, and stores it in the active project database.

### Step 6 — Promote useful knowledge

Useful extraction automatically becomes a durable Markdown note in:

`<Home>/Knowledge/Projects/<world-id>/wiki/memory`

The name includes a date and content digest, so the same extraction is not repeatedly promoted.

### What stays explicit?

Automatic promotion is project-scoped. Nothing is automatically published into `Shared`. A user or AI workflow must explicitly choose a `shared/` Archive path.

### What happens after a failure?

The capture cursor advances only after successful ingestion. A later hook can retry failed content. Hook errors are returned without blocking Claude or Codex from completing the session.

## How all Worlds stay current

The selected Home remembers every registered World.

```powershell
holocore sync-all
```

This visits each available project, repairs or regenerates HoloCore-owned integration files non-destructively, and ensures Atlas freshness.

## How feature parity is checked

![HoloCore parity release workflow checks Graphify, MemPalace, Archive, interfaces, imports, documentation, and tests](assets/workflow-parity-release.svg)

HoloCore is considered feature-complete only when the parity gate verifies the
three engine surfaces, their CLI/MCP interfaces, capture and import paths, export
formats, documentation, and keyless release tests. Unsupported provider-dependent
media remains explicitly reported rather than silently treated as complete.

```powershell
holocore update
```

This updates the installed Git version through `uv`, then performs the same reconciliation. A missing project is reported without stopping the others.

## What happens during normal project work?

1. **Orient:** run `holocore status`.
2. **Map:** use fresh Atlas to identify the project area.
3. **Retrieve:** search active World and Shared Archive knowledge.
4. **Recall only when needed:** consult Animus for history.
5. **Work:** open and change exact files.
6. **Capture:** let Claude `SessionEnd` or Codex `Stop` preserve useful history.
7. **Promote:** HoloCore writes useful extraction into the active World.
8. **Share deliberately:** move or create only verified cross-project knowledge in `Shared`.
9. **Reconcile:** use `sync-all` after client or generated-file changes.

## Example: a repeated login error

Suppose an AI assistant receives:

> The login error is back. What did we do before, and which code could be affected?

1. HoloCore checks World paths and Atlas freshness.
2. Atlas identifies authentication files and dependencies.
3. The active World Archive and Shared are searched for verified authentication rules.
4. Animus runs because “is back” and “before” require prior history.
5. The AI receives labelled evidence rather than every repository file and chat.
6. Exact source files are opened and the fix is made.
7. At session end, the conversation is captured, refined, remembered, and—if useful—promoted into the active World Archive.

## Choose the right place

| What you need | Put it here |
|---|---|
| A verified decision for one project | Active World Archive |
| A verified rule intentionally shared across projects | Shared Archive |
| Rebuildable file and dependency structure | Atlas |
| Previous work, errors, attempts, and chats | Animus |
| Original captured transcript | Raw chat audit |
| A broad project answer | Unified search |

## Simple glossary

| HoloCore term | Plain meaning |
|---|---|
| **HoloCore Home** | User-selected root shared by registered projects |
| **Archive** | One vault of verified knowledge |
| **Atlas** | One project's structural map |
| **Animus** | One project's remembered history |
| **World** | Project |
| **Sector** | Area inside a project |
| **Memory Shard** | Raw remembered fragment |
| **Archive Entry** | Polished durable note |
| **Signal** | One mapped thing |
| **Constellation** | Group of related mapped things |
| **CLI** | Commands typed in a terminal |
| **MCP** | A standard way for an AI client to call HoloCore tools |
