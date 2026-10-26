# HoloCore folder naming and migration

**Status:** Implemented in v0.12.0. New Homes use the plain folder names; existing Homes can migrate in place with a preview and explicit apply command.

The selected Home at `C:\Brains\Holocore Home` was migrated on 2026-10-26. Its 11 registered projects remain in `worlds.json`, SQLite integrity passed, and the migration journal records all 15 directory renames and legacy junctions.

## Current layout

```text
<Home>/
├── Knowledge/                         # Obsidian-ready shared vault
│   ├── Projects/<project-id>/         # Project knowledge
│   ├── Shared/
│   └── system/
├── Projects/<project-id>/
│   ├── CodeMap/                       # Project graph
│   ├── Runtime/
│   ├── Inbox/
│   └── Sources/
├── Memories/
│   ├── animus.db                      # Existing SQLite database filename
│   └── ChatHistory/<project-id>/
├── Connections/
├── worlds.json                        # Existing project registry filename
└── layout.json                        # Home folder layout version
```

`worlds.json` remains the registry at the Home root. `animus.db` remains the database inside `Memories`. These file names and their formats are independent of the visible folder names, so existing data is preserved without a database or registry conversion.

## Upgrade and migration

Run `holocore migrate-layout` to preview the old-to-new paths. Run `holocore migrate-layout --apply` to perform the same-volume directory renames. The operation checks the SQLite database, refuses to merge when both old and new folders exist, records a journal, and leaves old folder names as junctions where the platform supports them. It verifies the database after the rename. The registry, database, project IDs, and existing content stay in place.

Normal HoloCore upgrades preserve the current folder layout. Users can choose when to run the migration. Older HoloCore versions continue to use paths such as `Archive`, `Animus`, `raw-chats`, `Worlds`, and `Atlas` through compatibility junctions created for migrated and newly initialized Homes.

If Windows reports a folder is in use, the migration stops and rolls back completed renames. Close the HoloCore MCP server and capture hooks, then rerun the preview and apply command. The `layout-migration.json` journal records a completed or rolled-back attempt.

## Verification

Automated tests cover dry-run immutability, in-place path migration, legacy alias resolution, database and registry naming, conflict refusal, and repeatable Home initialization. Before using a migrated Home, check `holocore paths` and `holocore status`; confirm the Obsidian vault opens at `Knowledge` and project code maps resolve from `CodeMap`.
