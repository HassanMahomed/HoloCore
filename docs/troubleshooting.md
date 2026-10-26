# Troubleshooting

## `holocore` or `holocore-mcp` is not recognized

Run `uv tool list` and confirm HoloCore is installed. Reinstall from Git, then open a new terminal:

```powershell
uv tool install --force "git+https://github.com/VenomD846/HoloCore.git"
```

## `ModuleNotFoundError: holocore`

Reinstall the tool through `uv`. Editable installs and `PYTHONPATH` are development-only alternatives.

## First setup chose the wrong Home

Show the current selection:

```powershell
holocore home
```

Select another:

```powershell
holocore home <Home>
```

This changes the pointer only. It does not move the old Archive or registry. Run `holocore setup` in each project that should register with the new Home.

## A project is missing from `holocore worlds`

Run setup from that project:

```powershell
cd <project>
holocore setup
```

If it belongs to another Home, pass `--home <Home>`.

## `sync-all` reports `missing-world`

The registry contains a project root that no longer exists at that path. Other Worlds continue to reconcile. Restore or move the project back, set up its new location as a World, or edit the registry only after backing it up and confirming the stale entry.

## Claude cannot start or see HoloCore

From the project:

```powershell
holocore connect --platform claude
holocore doctor
```

Restart Claude, run `/mcp`, and approve or confirm the `holocore` project server. Setup cannot bypass Claude's approval step.

## Codex reports invalid TOML or cannot see HoloCore

Run:

```powershell
holocore connect --platform codex
holocore doctor
```

HoloCore can replace a recognizable HoloCore MCP block when an older generated Windows path made it invalid TOML. It preserves unrelated Codex settings. Restart or reopen the project afterward.

## Automatic capture is off

Run:

```powershell
holocore connect
holocore status
```

Then complete the client trust step:

- Claude: `/mcp` for server approval; capture runs at `SessionEnd`.
- Codex: `/hooks` to review and trust the HoloCore `Stop` hook.

The expected files are `<project>/.claude/settings.json` and `<project>/.codex/hooks.json`. If either file contains invalid JSON, HoloCore skips it and reports a warning instead of overwriting it.

## Review and verify capture hooks

HoloCore capture hooks run with the `uv`-managed interpreter, not the system
Python installation. The expected command is:

```text
C:\Users\<user>\AppData\Roaming\uv\tools\holocore\Scripts\python.exe -m holocore.capture_hook --client <client>
```

After changing a hook command, the client treats it as a new definition. Trust
must be refreshed for the current definition, and an already-open task does not
reload it.

- **Codex:** restart Codex, open the Codex CLI, run `/hooks`, and trust the
  current user-level `UserPromptSubmit` and `Stop` definitions. Then restart
  the Desktop app and create a new task. On Windows, the `commandWindows`
  value must use the executable form shown by `/hooks`; keep legacy duplicate
  project-local hooks disabled unless they are intentionally needed.
- **Claude Code:** restart the project, approve the current hook when prompted,
  and let the `SessionEnd` event fire. Run `/mcp` separately to approve the
  HoloCore MCP server; MCP approval does not trust capture hooks.

To verify a completed capture, use the paths reported by `holocore paths` and
inspect the latest diagnostic records:

```powershell
holocore paths
Get-Content "<Home>\Projects\<world>\Runtime\capture-diagnostics.jsonl" -Tail 5
```

Look for `captured: true` and `committed: true`, followed by a new raw audit
under `<Home>\Animus\raw-chats\<world>`. `holocore animus-checkpoint` may be
`null` on installations that use the runtime diagnostic record as the cursor;
that alone does not prove capture failed.

## A session was not captured

Automatic capture needs a supported hook payload and a readable transcript. Check:

1. The hook is trusted and shown as on by `holocore status`.
2. The client ended the session or turn through the expected hook event.
3. `<project>/.holocore/raw-chats` is writable.
4. The configured remote provider, if any, is reachable.

Capture reads only new transcript bytes. If ingestion fails, the cursor is not advanced, so a later hook can retry.

## `status` reports Atlas missing or stale

For an explicit refresh:

```powershell
holocore atlas-refresh
```

Normal `holocore search` checks and refreshes Atlas automatically before routing. `holocore sync-all` does the same for every registered World.

## Search returns another scope than expected

Archive search reads the active World and `Shared`. Results include an `archive_scope` label. It does not search other World folders.

Archive creation targets the active World by default. Use a `shared/` path only when you intentionally want Shared.

## Animus reports an unknown World or Sector

Run the command from an initialized World. Default Sectors are `general`, `project`, and `conversations`. Setup records the generated World ID in project configuration.

## Archive rejects a path or update

Archive rejects traversal, protected directories, invalid note shape, and unexpected overwrite conflicts. Use a relative Markdown path inside the active World, or a `shared/` prefix for Shared.

## Legacy project Archive was not merged

Setup copies Markdown from an older `<project>/Archive` into the active World's `Imported` folder. Identical files are skipped. Conflicting destination files are not overwritten. Review setup warnings and compare the two locations manually.

## `mine` captured too much

`mine` recursively stores eligible whole-file content. Choose a narrow directory before running it. There is no CLI rollback command.

## `holocore update` fails

The built-in updater requires `uv` and network access to the Git repository. Reinstall manually with `uv tool install --force`, then run:

```powershell
holocore sync-all
```

## Obsidian shows only part of the knowledge

Open `<Home>/Knowledge` as the vault root. Opening `Worlds/<world-id>` alone hides `Shared` and other World sections.

## An original reference application is missing

That is expected. HoloCore does not require the original Obsidian Second Brain, Graphify, or MemPalace runtimes. An error that tries to launch one of them is a HoloCore regression.
