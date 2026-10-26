# MCP reference

`holocore-mcp` is a local stdio server. One user-level connection accepts the active project root and resolves durable knowledge through the selected shared HoloCore Home.

Setup writes the exact Python executable, `-m holocore.mcp_server`, and HoloCore Home working directory into client configuration. No inherited terminal `PATH` is required.

## Client approval

- Claude stores the user-level server in `~/.claude.json`. Restart Claude, run `/mcp`, and approve or confirm `holocore`.
- Codex stores the equivalent server under the Codex user configuration. Restart or reopen the project. MCP access and Codex skills are separate from the capture hook; review the hook through `/hooks`.

## Tools

| Tool | Required inputs | Effect |
|---|---|---|
| `holocore_search` | `query`; optional `world` | Read plus generated-state refresh; check-first Atlas → active World Archive + Shared → optional Animus |
| `holocore_status` | None | Read; Home/World paths, readiness, Atlas freshness, Animus, and client/capture status |
| `holocore_archive_search` | `query` | Read; searches the active World and Shared |
| `holocore_archive_read` | `path` | Read; active World by default, `shared/` prefix for Shared |
| `holocore_archive_create` | `path`, `title`, `content` | Write; creates a validated AI-first World or Shared note |
| `holocore_atlas_refresh` | None | Write; refreshes project-local Atlas JSON |
| `holocore_atlas_search` | `query` | Read; searches project-local structural Signals |
| `holocore_atlas_explain` | `query` | Read; explains a Signal, evidence, and relationships |
| `holocore_atlas_path` | `source`, `target`; optional `max_depth` | Read; finds a shortest relationship path |
| `holocore_atlas_affected` | `query`; optional `depth` | Read; finds affected Signals |
| `holocore_atlas_neighborhood` | `query`; optional `depth` | Read; inspects a bounded local neighborhood |
| `holocore_atlas_audit` | None | Read; reports unresolved, duplicate, orphan, confidence, and coverage issues |
| `holocore_atlas_constellations` | optional `min_size` | Read; lists deterministic Atlas communities |
| `holocore_atlas_html` | None | Write; generates project-local self-contained HTML and returns its path |
| `holocore_atlas_export` | `output`; optional `formats` | Write; exports Atlas JSON, HTML, Markdown, and a manifest |
| `holocore_animus_mine` | `mode`; optional `path`, `sector` | Write; mines one explicitly scoped World using files, conversations, or Git |
| `holocore_animus_checkpoint` | `mode`, `source`; optional `sector` | Read; reads the last mining checkpoint |
| `holocore_animus_diary` | `content`; optional `title`, `sector` | Write; records one episodic diary entry |
| `holocore_animus_timeline` | optional `sector`, `limit` | Read; returns the scoped diary timeline |
| `holocore_animus_consolidate` | optional `sector` | Write; consolidates duplicate episodic entries |
| `holocore_animus_export` | optional `sector`, `limit` | Read; exports scoped Animus records |
| `holocore_remember` | `content`; optional `sector`, `source` | Write; stores or deduplicates one project-local Memory Shard |
| `holocore_recall` | `query`; optional `sector` | Read; recalls project-local episodic memory |
| `holocore_ingest_chat` | `messages`; optional `sector`, `instructions` | Write; audits raw chat, stores shards, and promotes useful extraction |
| `holocore_home` | None | Read; returns the selected Home, Archive, and registry |
| `holocore_worlds` | None | Read; lists registered Worlds |
| `holocore_global_graph` | optional `output` | Write; builds an Atlas-only graph across registered Worlds |
| `holocore_setup` | `home` | Write; registers the current World and connects supported clients |
| `holocore_sync_all` | None | Write; reconciles every registered World and ensures Atlas freshness |
| `holocore_activity_affected` | `query`; optional `limit` | Read; returns runs and actions that affected a matching file or directory |
| `holocore_activity_handoff` | optional `run_id`, `action_limit`, `item_limit` | Read; creates a bounded privacy-safe handoff from structured activity |
| `holocore_handoffs_list` | optional `status`, `limit` | Read; lists handoffs for the active project |
| `holocore_handoffs_create` | `run_id`; optional `title`, `note` | Write; creates claimable work linked to an existing run |
| `holocore_handoffs_claim` | `handoff_id`, `agent` | Write; atomically claims open work and returns its sanitized run context |
| `holocore_handoffs_close` | `handoff_id`, `outcome`; optional `agent`, `reason` | Write; completes or abandons a handoff; claimed work requires the claiming agent |
| `holocore_connections` | None | Read; reports installed and connected status for five supported AI clients |

The `holocore_setup` tool requires a user-chosen `home` input. The generated setup prompt tells the AI client to ask the user where the shared Home should live before running the write.

## Archive scope

The MCP server creates an `ArchiveView` with two roots:

- default paths resolve inside `<Home>/Knowledge/Projects/<project-id>`;
- paths beginning with `shared/` resolve inside `<Home>/Knowledge/Shared`.

Search reads both roots and labels each hit with `world` or `shared` scope. It does not search another World's private Archive section.

## Automatic capture is not an MCP tool

Claude `SessionEnd` and Codex `Stop` capture are project hooks that call the local HoloCore runtime. They use the same memory-refinement and Archive-promotion path as `holocore_ingest_chat`, but they do not require an MCP request at session end.

The files are installed by `setup` or `connect`; users still approve the MCP server through Claude `/mcp` and trust the Codex hook through `/hooks`.

## Protocol behavior

- Protocol version: `2025-06-18`
- Transport: local standard input/output
- Result shape: JSON encoded in one MCP text-content item
- Error shape: JSON-RPC code `-32000` with the exception message
- Server version: `0.10.0`

The server has no network authentication layer because it is launched as a local child process. The AI client, project configuration, operating-system permissions, and explicit client approval are the trust boundary.
