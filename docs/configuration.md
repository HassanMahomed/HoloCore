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
| `memories` | Shared SQLite path for project-scoped Memories |
| `animus` | Legacy v0.x alias accepted when reading existing configuration |
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

The default is deterministic, local, and keyless. HoloCore can discover an already-running Ollama service on loopback and list its installed models without downloading or starting anything:

```powershell
holocore models discover
holocore models configure --provider ollama --model "<installed-model>"
holocore models status
```

The explicit selection is stored without credentials at `<Home>/Runtime/local-model.json` and is shared by registered projects using that Home. `holocore models clear` removes only this selection and restores deterministic extraction.

Discovery accepts only credential-free HTTP loopback origins. It never scans the LAN or a public endpoint. If the configured runtime or model becomes unavailable, extraction makes one bounded provider attempt and continues through the deterministic fallback.

For automation or an existing OpenAI-compatible provider, environment variables remain the highest-precedence configuration:

```powershell
$env:HOLOCORE_LLM_BASE_URL = "https://provider.example/v1"
$env:HOLOCORE_LLM_MODEL = "example-model"
$env:HOLOCORE_LLM_API_KEY = "<secret>"
```

Older project-local `llm` settings remain compatible and are not rewritten. Their existing shape is:

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

If `base_url` or `model` is absent, HoloCore uses deterministic extraction. One pass produces summary, facts, decisions, preferences, and entities. Raw audits and provider/fallback provenance remain local, and useful extraction is promoted through the existing project Knowledge path.

## Private state

Setup adds `.holocore/raw-chats/` and `.holocore/animus.db*` to the project `.gitignore`. The files can contain sensitive content even though they are local. Back them up, encrypt them, or delete them according to your own retention policy.
