from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .archive import Archive
from .commands import render_all_commands
from .config import Config
from .home import HomeManager
from .layout import world_paths
from .policy import render_policy


CLIENT_PROBES = {
    "claude": ("claude", Path.home() / ".claude"),
    "codex": ("codex", Path(os.getenv("CODEX_HOME", Path.home() / ".codex"))),
    "cursor": ("cursor", Path(os.getenv("APPDATA", Path.home())) / "Cursor"),
    "gemini": ("gemini", Path.home() / ".gemini"),
    "opencode": ("opencode", Path.home() / ".config" / "opencode"),
}


def _client_root() -> Path:
    """Return the real user profile, or an isolated root used by test/portable environments."""
    explicit = os.getenv("HOLOCORE_CLIENT_CONFIG_ROOT")
    if explicit:
        return Path(explicit).expanduser().resolve()
    config_home = os.getenv("HOLOCORE_CONFIG_HOME")
    if config_home:
        # HOLOCORE_CONFIG_HOME is used for isolated/portable HoloCore runs.
        # Keep client mutations in the same boundary instead of touching the
        # signed-in user's real Claude, Codex, Cursor, or Gemini settings.
        return Path(config_home).expanduser().resolve() / "clients"
    return Path.home()


def detect_installed_clients() -> dict[str, dict[str, object]]:
    """Detect supported local AI clients without changing their configuration."""
    result: dict[str, dict[str, object]] = {}
    for client, (command, config_path) in CLIENT_PROBES.items():
        executable = shutil.which(command)
        evidence: list[str] = []
        if executable:
            evidence.append(str(Path(executable)))
        if config_path.exists():
            evidence.append(str(config_path))
        if client == "cursor":
            local_cursor = Path(os.getenv("LOCALAPPDATA", Path.home())) / "Programs" / "cursor" / "Cursor.exe"
            if local_cursor.is_file():
                evidence.append(str(local_cursor))
        result[client] = {"installed": bool(evidence), "evidence": evidence}
    return result


def _connection_config_path(client: str) -> Path:
    root = _client_root()
    paths = {
        "claude": root / ".claude.json",
        "codex": Path(os.getenv("CODEX_HOME", root / ".codex")) / "config.toml",
        "cursor": root / ".cursor" / "mcp.json",
        "gemini": root / ".gemini" / "settings.json",
        "opencode": root / ".config" / "opencode" / "opencode.json",
    }
    return paths[client]


def _is_holocore_entry(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    serialized = json.dumps(value, ensure_ascii=False, default=str).casefold()
    return "holocore" in serialized


def inspect_client_connections() -> dict[str, dict[str, object]]:
    """Read supported client configs and report HoloCore connection health."""
    detected = detect_installed_clients()
    result: dict[str, dict[str, object]] = {}
    for client in ("claude", "codex", "cursor", "gemini", "opencode"):
        evidence = list(detected.get(client, {}).get("evidence", []))
        config_path = _connection_config_path(client)
        installed = bool(detected.get(client, {}).get("installed")) or config_path.exists()
        state = "not-installed"
        detail = "No executable or client configuration was found."
        connected = False
        if config_path.exists():
            try:
                if client == "codex":
                    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
                    connected = _is_holocore_entry(data.get("mcp_servers", {}).get("holocore"))
                else:
                    data = json.loads(config_path.read_text(encoding="utf-8"))
                    group = "mcp" if client == "opencode" else "mcpServers"
                    connected = _is_holocore_entry(data.get(group, {}).get("holocore"))
            except (OSError, UnicodeError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
                state = "invalid-config"
                detail = f"Could not read client configuration: {exc}"
            else:
                if connected:
                    state = "connected"
                    detail = "HoloCore MCP entry found."
                else:
                    state = "needs-connection"
                    detail = "Client configuration exists but has no HoloCore MCP entry."
        elif installed:
            state = "needs-connection"
            detail = "Client detected; run `holocore connect` to add HoloCore."
        result[client] = {
            "installed": installed,
            "connected": connected,
            "state": state,
            "detection_evidence": evidence,
            "config_path": str(config_path),
            "detail": detail,
        }
    return result


@dataclass
class BootstrapReport:
    root: Path
    created: list[Path]
    skipped: list[Path]
    git: str
    updated: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    home: Path | None = None
    world_id: str | None = None

    def as_dict(self) -> dict:
        return {
            "root": str(self.root),
            "created": [str(p) for p in self.created],
            "updated": [str(p) for p in self.updated],
            "skipped": [str(p) for p in self.skipped],
            "git": self.git,
            "home": str(self.home) if self.home else None,
            "world_id": self.world_id,
            "paths": world_paths(self.root, Config.load(root=self.root)),
            "next_steps": [
                "Restart or reopen your AI client in this World.",
                "Claude Code: run /mcp once to approve HoloCore, then /holocore-search.",
                "Codex: invoke $holocore-search (project skills are in .agents/skills).",
                "Obsidian: open the shared HoloCore Home Archive as one vault (optional).",
            ],
            "warnings": self.warnings,
            "connections": inspect_client_connections(),
        }


def _mcp_server(root: Path) -> dict:
    # Using the exact Python environment is more reliable than assuming an AI
    # desktop/client inherited the shell PATH containing `holocore-mcp`.
    frozen = bool(getattr(sys, "frozen", False))
    return {
        "command": str(Path(sys.executable).resolve()),
        "args": ["mcp"] if frozen else ["-m", "holocore.mcp_server"],
        "cwd": str(root),
    }


def _write_generated(target: Path, content: str, created: list[Path], updated: list[Path], skipped: list[Path]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text(content, encoding="utf-8")
        created.append(target)
    elif target.read_text(encoding="utf-8") != content:
        target.write_text(content, encoding="utf-8")
        updated.append(target)
    else:
        skipped.append(target)


def _merge_json(target: Path, patch: dict, created: list[Path], updated: list[Path], skipped: list[Path], warnings: list[str]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text(json.dumps(patch, indent=2), encoding="utf-8")
        created.append(target)
        return
    try:
        current = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        skipped.append(target)
        warnings.append(f"Could not merge invalid JSON in {target}: {exc}")
        return

    def merge(base: dict, incoming: dict) -> None:
        for key, value in incoming.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                merge(base[key], value)
            else:
                base[key] = value

    before = json.dumps(current, sort_keys=True)
    merge(current, patch)
    if json.dumps(current, sort_keys=True) == before:
        skipped.append(target)
    else:
        target.write_text(json.dumps(current, indent=2), encoding="utf-8")
        updated.append(target)


def _merge_codex_toml(target: Path, server: dict, created: list[Path], updated: list[Path], skipped: list[Path]) -> None:
    command = json.dumps(server["command"])
    args = json.dumps(server["args"])
    cwd = json.dumps(server["cwd"])
    block = f'[mcp_servers.holocore]\ncommand = {command}\nargs = {args}\ncwd = {cwd}\n'
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(block, encoding="utf-8")
        created.append(target)
        return
    original = target.read_text(encoding="utf-8")
    try:
        current = tomllib.loads(original).get("mcp_servers", {}).get("holocore", {})
        if current == server:
            skipped.append(target)
            return
    except tomllib.TOMLDecodeError:
        # Replace a recognizable HoloCore section even when an older generated
        # Windows path made that section invalid TOML.
        pass
    pattern = re.compile(r"(?ms)^\[mcp_servers\.holocore\]\s*\n.*?(?=^\[|\Z)")
    replacement = pattern.sub(lambda _match: block + "\n", original) if pattern.search(original) else original.rstrip() + "\n\n" + block
    if replacement == original:
        skipped.append(target)
    else:
        target.write_text(replacement, encoding="utf-8")
        updated.append(target)


def _merge_instructions(target: Path, instructions: str, created: list[Path], updated: list[Path], skipped: list[Path]) -> None:
    if not target.exists():
        target.write_text(instructions, encoding="utf-8")
        created.append(target)
        return
    original = target.read_text(encoding="utf-8")
    marker = "<!-- holocore:project-instructions -->"
    if marker in original or original == instructions:
        skipped.append(target)
        return
    addition = (
        f"\n\n{marker}\n## HoloCore project context\n\n"
        "Read `HOLOCORE.md` before project-context work. Use HoloCore's check-first route and never loop a result back into HoloCore.\n"
    )
    target.write_text(original.rstrip() + addition, encoding="utf-8")
    updated.append(target)


def _protect_private_state(root: Path, created: list[Path], updated: list[Path], skipped: list[Path]) -> None:
    target = root / ".gitignore"
    marker = "# HoloCore private conversation data"
    block = f"{marker}\n.holocore/raw-chats/\n.holocore/animus.db*\n"
    if not target.exists():
        target.write_text(block, encoding="utf-8")
        created.append(target)
        return
    original = target.read_text(encoding="utf-8")
    if marker in original:
        skipped.append(target)
    else:
        target.write_text(original.rstrip() + "\n\n" + block, encoding="utf-8")
        updated.append(target)


def _merge_capture_hook(target: Path, event: str, client: str, created: list[Path], updated: list[Path], skipped: list[Path], warnings: list[str]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    data: dict = {}
    if existed:
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            skipped.append(target)
            warnings.append(f"Could not install automatic {client} capture hook in invalid JSON {target}: {exc}")
            return
        if not isinstance(data, dict):
            skipped.append(target)
            warnings.append(f"Could not install automatic {client} capture hook because {target} is not a JSON object")
            return
    executable = str(Path(sys.executable).resolve())
    module_args = f"capture-hook --client {client}" if getattr(sys, "frozen", False) else f"-m holocore.capture_hook --client {client}"
    command = f'"{executable}" {module_args}' if " " in executable else f"{executable} {module_args}"
    handler = {"type": "command", "command": command, "timeout": 15000 if client == "gemini" else 15}
    if client != "gemini":
        handler.update({"async": event != "SessionEnd", "statusMessage": "Recording activity in HoloCore"})
    if client == "codex":
        handler["commandWindows"] = command
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        skipped.append(target)
        warnings.append(f"Could not install automatic {client} capture hook because 'hooks' in {target} is not an object")
        return
    groups = hooks.setdefault(event, [])
    if not isinstance(groups, list):
        skipped.append(target)
        warnings.append(f"Could not install automatic {client} capture hook because hooks.{event} in {target} is not a list")
        return
    for group in groups:
        serialized = json.dumps(group)
        if "holocore.capture_hook" not in serialized and "capture-hook --client" not in serialized:
            continue
        changed = False
        for item in group.get("hooks", []) if isinstance(group, dict) else []:
            item_command = str(item.get("command", "")) if isinstance(item, dict) else ""
            if isinstance(item, dict) and item.get("type") == "command" and ("holocore.capture_hook" in item_command or "capture-hook --client" in item_command):
                if any(item.get(key) != value for key, value in handler.items()):
                    item.update(handler); changed = True
        if changed:
            target.write_text(json.dumps(data, indent=2), encoding="utf-8")
            updated.append(target)
        else:
            skipped.append(target)
        return
    groups.append({"hooks": [handler]})
    target.write_text(json.dumps(data, indent=2), encoding="utf-8")
    (updated if existed else created).append(target)


def _merge_cursor_hook(target: Path, event: str, created: list[Path], updated: list[Path], skipped: list[Path], warnings: list[str]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    try:
        data = json.loads(target.read_text(encoding="utf-8")) if existed else {"version": 1, "hooks": {}}
    except (OSError, json.JSONDecodeError) as exc:
        skipped.append(target); warnings.append(f"Could not install Cursor activity hook in invalid JSON {target}: {exc}"); return
    if not isinstance(data, dict) or not isinstance(data.setdefault("hooks", {}), dict):
        skipped.append(target); warnings.append(f"Could not install Cursor activity hook because {target} has an invalid hooks object"); return
    hooks = data["hooks"]
    entries = hooks.setdefault(event, [])
    if not isinstance(entries, list):
        skipped.append(target); warnings.append(f"Could not install Cursor activity hook because hooks.{event} in {target} is not a list"); return
    executable = str(Path(sys.executable).resolve())
    args = "capture-hook --client cursor" if getattr(sys, "frozen", False) else "-m holocore.capture_hook --client cursor"
    command = f'"{executable}" {args}' if " " in executable else f"{executable} {args}"
    hook = {"command": command, "timeout": 15}
    if hook in entries:
        skipped.append(target); return
    entries.append(hook)
    target.write_text(json.dumps(data, indent=2), encoding="utf-8")
    (updated if existed else created).append(target)


def _opencode_activity_plugin() -> str:
    executable = json.dumps(str(Path(sys.executable).resolve()))
    args = json.dumps(["capture-hook", "--client", "opencode"] if getattr(sys, "frozen", False) else ["-m", "holocore.capture_hook", "--client", "opencode"])
    return f'''// Generated by HoloCore. Records tool activity and affected paths only.
import {{ spawn }} from "node:child_process"
const executable = {executable}
const args = {args}
const emit = (directory, payload) => {{
  try {{
    const child = spawn(executable, args, {{ stdio: ["pipe", "ignore", "ignore"], windowsHide: true }})
    child.stdin.end(JSON.stringify({{ cwd: directory, ...payload }}))
  }} catch {{ /* Activity capture must never interrupt OpenCode. */ }}
}}
export const HoloCoreActivity = async ({{ directory }}) => ({{
  event: async ({{ event }}) => {{
    if (["session.created", "session.idle", "session.error", "file.edited"].includes(event?.type))
      emit(directory, {{ event: event.type, hook_event_name: event.type, properties: event.properties ?? {{}} }})
  }},
  "tool.execute.after": async (input, output) => emit(directory, {{
    event: "tool.execute.after", hook_event_name: "tool.execute.after",
    session_id: input?.sessionID ?? input?.session_id, tool_name: input?.tool,
    tool_input: input?.args ?? {{}}, is_error: Boolean(output?.error)
  }})
}})
'''


def _migrate_legacy_state(root: Path, config: Config, created: list[Path], updated: list[Path], warnings: list[str]) -> None:
    """Copy useful legacy state once; never remove the project-local source."""
    target_db = config.animus_path
    legacy_databases = (
        root / ".holocore" / "animus.db",
        config.state_dir.parent / "Animus" / "animus.db",
    )
    for legacy_db in legacy_databases:
        if not legacy_db.is_file() or legacy_db.resolve() == target_db.resolve():
            continue
        from .animus import Animus

        existed = target_db.exists()
        report = Animus(target_db).merge_database(legacy_db, world=config.world_id)
        (updated if existed else created).append(target_db)
        warnings.append(
            f"Migrated {report['shards']} legacy Memory Shards into shared Animus storage; "
            "the source database was preserved."
        )
    legacy_chat_directories = (
        root / ".holocore" / "raw-chats",
        config.state_dir.parent / "Animus" / "raw-chats",
    )
    for legacy_chats in legacy_chat_directories:
        if not legacy_chats.is_dir() or legacy_chats.resolve() == config.raw_chats_path.resolve():
            continue
        config.raw_chats_path.mkdir(parents=True, exist_ok=True)
        for source in sorted(legacy_chats.rglob("*")):
            if not source.is_file():
                continue
            destination = config.raw_chats_path / source.relative_to(legacy_chats)
            if not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                created.append(destination)


def bootstrap_project(
    project: Path,
    *,
    init_git: bool = True,
    platforms: list[str] | None = None,
    home: Path | None = None,
) -> BootstrapReport:
    root = project.resolve()
    root.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    updated: list[Path] = []
    skipped: list[Path] = []
    warnings: list[str] = []
    selected = set(["codex", "claude", "gemini", "cursor", "opencode", "generic"] if platforms is None else platforms)
    home_manager = HomeManager(home)
    registration = home_manager.register_world(root, import_archive=True)
    world_id = str(registration["world"]["id"])
    config = Config.for_world(root)
    world_storage = config.state_dir.parent
    connections = home_manager.home / "Connections"
    server = _mcp_server(home_manager.home)
    mcp = {"mcpServers": {"holocore": server}}
    instructions = render_policy()
    _migrate_legacy_state(root, config, created, updated, warnings)

    generated = {
        "policy.md": instructions,
        "mcp.json": json.dumps(mcp, indent=2),
    }
    for relative, content in generated.items():
        _write_generated(connections / relative, content, created, updated, skipped)

    if "codex" in selected:
        _merge_codex_toml(connections / ".codex/config.toml", server, created, updated, skipped)
        active_codex_home = Path(os.getenv("CODEX_HOME", _client_root() / ".codex"))
        _merge_codex_toml(active_codex_home / "config.toml", server, created, updated, skipped)
        active_codex = active_codex_home / "hooks.json"
        for event in ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "SubagentStart", "SubagentStop", "Stop", "SessionEnd"):
            _merge_capture_hook(active_codex, event, "codex", created, updated, skipped, warnings)
            _merge_capture_hook(connections / ".codex/hooks.json", event, "codex", created, updated, skipped, warnings)
    if "claude" in selected:
        _merge_json(connections / ".mcp.json", mcp, created, updated, skipped, warnings)
        _merge_json(_client_root() / ".claude.json", mcp, created, updated, skipped, warnings)
        for event in ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "SubagentStart", "SubagentStop", "Stop", "SessionEnd"):
            _merge_capture_hook(connections / ".claude/settings.json", event, "claude", created, updated, skipped, warnings)
            _merge_capture_hook(_client_root() / ".claude/settings.json", event, "claude", created, updated, skipped, warnings)
        _write_generated(
            connections / ".claude/commands/holocore.md",
            """---
description: Show HoloCore status, paths, and the available project commands.
---

# HoloCore

Run `holocore status` and `holocore paths` from the project root. HoloCore checks
Atlas first, then the active World wiki, and consults Animus
only when the question needs history. Use `/holocore-search` for a focused
knowledge search or `/holocore-doctor` to diagnose the MCP connection.
""",
            created, updated, skipped,
        )
    if "gemini" in selected:
        gemini_connections = connections / ".gemini/settings.json"
        gemini_user = _client_root() / ".gemini/settings.json"
        _merge_json(gemini_connections, mcp, created, updated, skipped, warnings)
        _merge_json(gemini_user, mcp, created, updated, skipped, warnings)
        for event in ("AfterTool", "AfterAgent", "SessionEnd"):
            _merge_capture_hook(gemini_connections, event, "gemini", created, updated, skipped, warnings)
            _merge_capture_hook(gemini_user, event, "gemini", created, updated, skipped, warnings)
    if "cursor" in selected:
        _merge_json(connections / ".cursor/mcp.json", mcp, created, updated, skipped, warnings)
        _merge_json(_client_root() / ".cursor/mcp.json", mcp, created, updated, skipped, warnings)
        for event in ("postToolUse", "postToolUseFailure", "afterFileEdit", "subagentStop", "stop"):
            _merge_cursor_hook(connections / ".cursor/hooks.json", event, created, updated, skipped, warnings)
            _merge_cursor_hook(_client_root() / ".cursor/hooks.json", event, created, updated, skipped, warnings)
        _write_generated(connections / ".cursor/rules/holocore.mdc", instructions, created, updated, skipped)
    if "opencode" in selected:
        opencode_patch = {"mcp": {"holocore": {"type": "local", "command": [server["command"], *server["args"]], "cwd": str(home_manager.home)}}}
        _merge_json(connections / "opencode.json", opencode_patch, created, updated, skipped, warnings)
        _merge_json(_client_root() / ".config/opencode/opencode.json", opencode_patch, created, updated, skipped, warnings)
        plugin = _opencode_activity_plugin()
        _write_generated(connections / ".config/opencode/plugins/holocore-activity.js", plugin, created, updated, skipped)
        _write_generated(_client_root() / ".config/opencode/plugins/holocore-activity.js", plugin, created, updated, skipped)

    for platform, command_files in render_all_commands().items():
        if platform in selected:
            for relative, content in command_files.items():
                # Commands and skills are installed once in Home, not copied into every World.
                _write_generated(connections / relative, content, created, updated, skipped)

    git = "skipped"
    if init_git and not (root / ".git").exists():
        try:
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
            git = "created"
        except (OSError, subprocess.CalledProcessError):
            git = "unavailable"
    elif (root / ".git").exists():
        git = "existing"
    return BootstrapReport(root, created, skipped, git, updated, warnings, home_manager.home, world_id)


def bootstrap(
    project: Path,
    *,
    init_git: bool = True,
    platforms: list[str] | None = None,
    home: Path | None = None,
) -> dict:
    return bootstrap_project(project, init_git=init_git, platforms=platforms, home=home).as_dict()


__all__ = [
    "BootstrapReport",
    "bootstrap",
    "bootstrap_project",
    "detect_installed_clients",
    "inspect_client_connections",
]
