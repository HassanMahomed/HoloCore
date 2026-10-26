"""Human-facing World layout and onboarding helpers."""
from __future__ import annotations

from pathlib import Path
import json
import os
import tomllib

from .config import Config
from .atlas import ATLAS_OUT_DIR
from .folder_layout import HomeLayout


def world_paths(root: Path, config: Config | None = None) -> dict[str, str]:
    root = root.resolve()
    config = config or Config.load(root=root)
    knowledge = HomeLayout(config.home).knowledge if config.home else config.vault
    return {
        "world": str(root),
        "world_id": config.world_id or root.name,
        "home": str(config.home or root),
        "shared_brain": str(knowledge),
        "worlds_registry": str((config.home / "worlds.json") if config.home else root / ".holocore" / "worlds.json"),
        "start_here": str((knowledge / "system" / "index.md") if config.home else root / "HOLOCORE-START-HERE.md"),
        "archive": str(knowledge),
        "world_archive": str(config.vault),
        "ingest_inbox": str((config.state_dir.parent / "Inbox") if config.home else root / "Archive" / "Inbox"),
        "raw_sources": str(config.state_dir.parent / "Sources"),
        "archive_entries": str(config.vault / "wiki"),
        "archive_index": str(config.vault / "system" / "index.md"),
        "atlas_json": str(config.atlas_graph_path),
        "atlas_html": str(config.atlas_graph_path.with_name("atlas.html")),
        "atlas_runtime": str(config.atlas_path),
        "memory_shards": str(config.animus_path),
        "raw_chats": str(config.raw_chats_path),
        "runtime": str(config.state_dir),
        "claude_mcp": str((config.home / "Connections" / ".mcp.json") if config.home else root / ".mcp.json"),
        "claude_commands": str((config.home / "Connections" / ".claude" / "commands") if config.home else root / ".claude" / "commands"),
        "claude_hooks": str((config.home / "Connections" / ".claude" / "settings.json") if config.home else root / ".claude" / "settings.json"),
        "codex_mcp": str((config.home / "Connections" / ".codex" / "config.toml") if config.home else root / ".codex" / "config.toml"),
        "codex_hooks": str((config.home / "Connections" / ".codex" / "hooks.json") if config.home else root / ".codex" / "hooks.json"),
        "codex_skills": str((config.home / "Connections" / ".agents" / "skills") if config.home else root / ".agents" / "skills"),
    }


def render_start_here(root: Path, config: Config | None = None) -> str:
    root = root.resolve()
    config = config or Config.load(root=root)
    paths = world_paths(root, config)
    return f"""# HoloCore — Start Here

This folder is one **World**: `{root.name}`.

## Where everything lives

| What | Location | What it does |
|---|---|---|
| HoloCore Home | `{paths['home']}` | Shared home used by every registered project |
| Shared second brain | `{paths['archive']}` | One visible Obsidian-ready knowledge vault |
| This World's Archive | `{paths['world_archive']}` | Durable knowledge scoped to this project |
| Source Inbox | `{paths['ingest_inbox']}` | Drop files here; HoloCore ingests new content on the next search or AI capture hook |
| Immutable raw sources | `{paths['raw_sources']}` | Content-addressed originals retained for audit and reprocessing |
| Atlas JSON | `{paths['atlas_json']}` | Machine-readable structural and semantic map |
| Atlas HTML | `{paths['atlas_html']}` | Interactive graph for people and AI tools |
| Memory Shards | `{paths['memory_shards']}` | Searchable remembered history in SQLite |
| Raw chat audit | `{paths['raw_chats']}` | Original chats retained for traceability |

The Archive is deliberately visible. In Obsidian, choose **Open folder as vault** and select:

`{paths['archive']}`

Obsidian is optional; HoloCore and connected AI clients can read the Markdown directly.

## Connect an AI client

Setup registers this World with the same shared brain. AI connection files and skills are installed once under HoloCore Home, not copied into this project. Run `holocore connect` to repair the shared bundle.

- Claude Code: run `/mcp` to approve/check HoloCore, then use `/holocore-search`.
- Codex: use `$holocore-search`; shared skills live under `Home/Connections/.agents/skills/`.
- Any MCP client: connect the shared `holocore` stdio server once and pass the active World root.

Conversations are captured automatically at supported client hook points, useful shards are promoted into this World's Archive, and Atlas refreshes before search when source files changed. Run `holocore paths` at any time to print every absolute location, or `holocore doctor` to check the connection.
"""


def format_paths(paths: dict[str, str]) -> str:
    labels = {
        "world": "World (project root)",
        "world_id": "World ID",
        "home": "HoloCore Home",
        "shared_brain": "Shared brain (Obsidian vault)",
        "worlds_registry": "Registered Worlds",
        "archive": "Knowledge vault (Obsidian)",
        "world_archive": "This project's knowledge",
        "ingest_inbox": "Automatic source Inbox",
        "raw_sources": "Immutable raw sources",
        "archive_entries": "Knowledge entries",
        "archive_index": "Knowledge index",
        "atlas_json": "Code Map graph",
        "atlas_html": "Visual Code Map",
        "atlas_runtime": "Code Map runtime mirror",
        "memory_shards": "Memory database",
        "raw_chats": "Chat history",
        "start_here": "Start-here guide",
        "claude_mcp": "Claude MCP config",
        "claude_commands": "Claude slash commands",
        "claude_hooks": "Claude capture hook",
        "codex_mcp": "Codex MCP config",
        "codex_hooks": "Codex capture hook",
        "codex_skills": "Codex skills",
        "runtime": "HoloCore runtime",
    }
    return "\n".join(f"{labels.get(key, key):27} {value}" for key, value in paths.items())


def integration_status(root: Path) -> dict[str, dict[str, object]]:
    root = root.resolve()
    paths = world_paths(root, Config.load(root=root))

    def json_mcp(path: Path, key: str = "mcpServers") -> tuple[bool, str]:
        if not path.exists():
            return False, "configuration missing"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return ("holocore" in data.get(key, {}), "connected" if "holocore" in data.get(key, {}) else "HoloCore entry missing")
        except (OSError, json.JSONDecodeError) as exc:
            return False, f"invalid JSON: {exc}"

    claude_mcp = Path(paths["claude_mcp"])
    claude_commands = Path(paths["claude_commands"])
    claude_hooks = Path(paths["claude_hooks"])
    codex_path = Path(paths["codex_mcp"])
    codex_hooks = Path(os.getenv("CODEX_HOME", Path.home() / ".codex")) / "hooks.json"
    codex_template = Path(paths["codex_hooks"])
    codex_skills = Path(paths["codex_skills"])
    claude_ok, claude_detail = json_mcp(claude_mcp)

    def capture_hook(path: Path, event: str) -> bool:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return "holocore.capture_hook" in json.dumps(data.get("hooks", {}).get(event, []))

    claude_capture = capture_hook(claude_hooks, "SessionEnd")
    codex_prompt = capture_hook(codex_hooks, "UserPromptSubmit")
    codex_stop = capture_hook(codex_hooks, "Stop")
    codex_capture = codex_prompt and codex_stop
    try:
        codex_data = tomllib.loads(codex_path.read_text(encoding="utf-8")) if codex_path.exists() else {}
        codex_ok = "holocore" in codex_data.get("mcp_servers", {})
        codex_detail = "connected" if codex_ok else "configuration or HoloCore entry missing"
    except (OSError, tomllib.TOMLDecodeError) as exc:
        codex_ok, codex_detail = False, f"invalid TOML: {exc}"
    return {
        "claude": {
            "connected": claude_ok,
            "detail": claude_detail,
            "config": str(claude_mcp),
            "slash_commands": len(list(claude_commands.glob("holocore-*.md"))),
            "skills": len(list((claude_commands.parent / "skills").glob("holocore-*/SKILL.md"))),
            "capture": claude_capture,
        },
        "codex": {
            "connected": codex_ok,
            "detail": codex_detail,
            "config": str(codex_path),
            "skills": len(list(codex_skills.glob("holocore-*/SKILL.md"))),
            "capture": codex_capture,
            "capture_status": "verified" if codex_capture else "configured, not yet verified",
            "active_hooks": str(codex_hooks),
            "template_hooks": str(codex_template),
            "user_prompt_submit": codex_prompt,
            "stop": codex_stop,
        },
    }
