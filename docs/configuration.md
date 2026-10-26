# Configuration guide

## Shared Home selection

HoloCore remembers one user-selected Home outside any project. Show it with:

```powershell
holocore home
```

Select and initialize another Home with:

```powershell
holocore home <Home>
```

The user-level pointer contains the absolute Home path. `HOLOCORE_CONFIG_HOME` may be used to select the directory that stores this pointer, which is useful for isolated or portable profiles.

Changing Home selects another `Knowledge` vault and `worlds.json`; it does not copy or delete data from the previous Home. Re-run `holocore setup` in each project that should join the new Home.

## Home and Archive paths

The default Home is `%USERPROFILE%\HoloCore` on Windows or `~/HoloCore` on other systems. This is only a fallback; users can select any writable Home with `holocore setup --home <Home>`.

```text
<Home>/
├── Knowledge/
│   ├── Projects/<project-id>/         Active-World durable knowledge
│   ├── Shared/                    Explicit cross-project knowledge
│   └── system/index.md
└── worlds.json
```

`<Home>/Knowledge` is the only Obsidian vault root. The active World uses `Archive/Projects/<project-id>`. Archive operations target that World by default; paths beginning with `shared/` target `Knowledge/Shared`.

## Project configuration

`holocore setup` merges `<project>/.holocore/config.json` with schema version `2`. Its important fields are:

| Field | Meaning |
|---|---|
| `world` | Human-readable project name |
| `world_id` | Generated registry ID |
| `root` | Project root |
| `home` | Selected shared Home |
| `archive` | This World's Archive path |
| `shared_archive` | Shared Archive path |
| `state_dir` | Project-local generated runtime |
| `animus` | Project-local SQLite path |
| `raw_chats` | Project-local raw audit directory |
| `llm` | Optional memory-refinement provider settings |

Use `holocore paths` rather than constructing paths from these fields manually.

For uninitialized or legacy Worlds, HoloCore still recognizes `.holocore/world.json` and fallback `HOLOCORE_VAULT` or `HOLOCORE_STATE` locations. Running `holocore setup` upgrades the project to the shared-Home layout.

## Command options

- `--root <project>` selects a World when a command has no positional project path.
- `--json` appears before the subcommand and emits stable machine-readable JSON.
- `setup [<project>] --home <Home>` registers a World with an explicit Home.
- `setup` and `connect` accept repeatable `--platform` values.
- `search --world <world-id>` overrides Animus scope for advanced retrieval.
- `mine`, `remember`, and `recall` accept Sector options.

## AI-client configuration

HoloCore uses the exact Python executable from its installed environment to launch the MCP server. Project files include:

- Claude MCP: `<project>/.mcp.json`
- Claude capture: `<project>/.claude/settings.json` at `SessionEnd`
- Codex MCP: `<project>/.codex/config.toml`
- Codex capture: `<project>/.codex/hooks.json` at `Stop`
- Codex skills: `<project>/.agents/skills`

Run `holocore connect` to merge or repair these entries. HoloCore preserves unrelated settings. It skips malformed JSON with a warning and can replace a recognizable HoloCore TOML section when an older generated Windows path made that section invalid.

Client-side trust cannot be pre-approved:

- Claude: use `/mcp`.
- Codex: use `/hooks`.

## Optional memory-refinement provider

The default is local and keyless. To use an OpenAI-compatible endpoint, add `llm` to `<project>/.holocore/config.json`:

```json
{
  "llm": {
    "base_url": "https://provider.example/v1",
    "model": "example-model",
    "api_key_env": "HOLOCORE_LLM_API_KEY",
    "custom_instructions": "Extract verified project facts, decisions, preferences, constraints, and outcomes. Exclude secrets and filler."
  }
}
```

Set the secret outside the file:

```powershell
$env:HOLOCORE_LLM_API_KEY = "<secret>"
```

If `base_url` or `model` is absent, HoloCore uses deterministic local extraction. One extraction pass produces summary, facts, decisions, preferences, and entities. Raw chats remain under the project, shards remain in local Animus, and useful extraction is promoted into the active World's Archive.

## Private state

Setup adds `.holocore/raw-chats/` and `.holocore/animus.db*` to the project `.gitignore`. The files can contain sensitive content even though they are local. Back them up, encrypt them, or delete them according to your own retention policy.
