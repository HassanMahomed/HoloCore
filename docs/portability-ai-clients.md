# Portability and AI-client guide

## One Home, many project connections

Install HoloCore once, choose one shared Home, and run setup in each project:

```powershell
cd <project>
holocore setup --home <Home>
```

Later Worlds use the saved Home:

```powershell
cd <another-project>
holocore setup
```

Every client in a World reaches the same two durable scopes:

- `<Home>/Knowledge/Projects/<project-id>` for that project;
- `<Home>/Knowledge/Shared` for explicitly shared knowledge.

Atlas, Animus, raw chats, and hook cursors remain under each project.

## Claude Code

Setup creates or merges:

- `<project>/.mcp.json`
- `<project>/.claude/commands/`
- `<project>/.claude/skills/`
- `<project>/.claude/settings.json` with a `SessionEnd` capture hook

After setup:

1. Restart Claude Code in the project.
2. Run `/mcp`.
3. Approve or confirm the HoloCore project server.
4. Use `/holocore-search` or `/mcp__holocore__search`.

The `SessionEnd` hook captures new transcript content automatically. MCP approval and hook installation are separate client controls; verify both with `holocore status`.

## Codex

Setup creates or merges:

- `<project>/.codex/config.toml`
- `<project>/.codex/hooks.json` with a `Stop` capture hook
- `<project>/.agents/skills/`

After setup:

1. Restart Codex or reopen the project.
2. Run `/hooks`.
3. Review and trust the HoloCore Stop hook.
4. Invoke `$holocore-search`.

Codex project skills use `$`, not HoloCore slash commands. The MCP server and Stop hook both run through the exact Python environment recorded during setup.

## Gemini

Gemini MCP settings are merged into `<project>/.gemini/settings.json`. HoloCore commands are TOML definitions under `<project>/.gemini/commands`.

Automatic session capture is currently implemented for Claude and Codex only.

## Cursor and OpenCode

Setup uses each client's native project MCP and command format. Restart or reload the project after setup. If command discovery is unavailable in a client version, use HoloCore MCP tools or run `holocore search` in the project terminal.

Automatic session capture is currently implemented for Claude and Codex only.

## Generic MCP shape

The generated connection uses the exact HoloCore Python environment:

```json
{
  "mcpServers": {
    "holocore": {
      "command": "<python-executable>",
      "args": ["-m", "holocore.mcp_server"],
      "cwd": "<project>"
    }
  }
}
```

Codex stores the equivalent fields in TOML. OpenCode uses its command-array form. Prefer `holocore connect` over hand editing because it preserves unrelated configuration and repairs HoloCore-owned entries consistently.

## Move or copy one World

1. Install HoloCore on the destination machine.
2. Copy or clone `<project>`.
3. Decide whether project-local `.holocore` history should travel.
4. Select the destination machine's Home.
5. Run `holocore setup --home <Home>` from the copied project.
6. Run `holocore doctor`, then approve Claude `/mcp` or Codex `/hooks` as applicable.

Because World IDs include the normalized absolute path, a copied project at a different path may register with a different ID. HoloCore does not silently merge the old and new World sections.

## Move the shared Home

HoloCore does not automate Home migration.

1. Stop clients that may be writing Archive or Animus data.
2. Copy the complete `<Home>` directory to the destination.
3. Run `holocore home <new-Home>`.
4. Run setup from each World whose registry path changed.
5. Run `holocore sync-all`.

Keep the old Home until the new Archive and registry are verified. Selecting another Home changes the pointer; it does not move files.

## Reconcile after upgrades or client changes

```powershell
holocore sync-all
```

This reruns non-destructive setup and refreshes Atlas for every registered available World.

```powershell
holocore update
```

This reinstalls HoloCore from Git through `uv`, then performs the same reconciliation.

## Portability and privacy contract

The portable durable brain is `<Home>/Knowledge` plus `<Home>/worlds.json`. Project `.holocore` contains generated structure, raw transcripts, and episodic history. Copy it only when that local state should travel.

Do not copy `animus.db` while HoloCore is writing to it. Treat raw chats and Animus as sensitive. Obsidian remains optional; when used, open `<Home>/Knowledge` as the one vault.
