"""Canonical HoloCore AI command definitions and platform renderers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class CommandDefinition:
    name: str
    description: str
    invocation: str
    write: bool = False

    @property
    def slug(self) -> str:
        return f"holocore-{self.name.replace(' ', '-')}"


COMMANDS: tuple[CommandDefinition, ...] = (
    CommandDefinition("setup", "Set up a complete HoloCore project and connect detected AI clients.", "holocore setup", True),
    CommandDefinition("init", "Initialize HoloCore in the current project.", "holocore init $ARGUMENTS", True),
    CommandDefinition("connect", "Install or repair project-local AI client connections.", "holocore connect", True),
    CommandDefinition("home", "Show HoloCore Home and the shared knowledge folder.", "holocore home"),
    CommandDefinition("worlds", "List every project registered with HoloCore Home.", "holocore worlds"),
    CommandDefinition("global-graph", "Build one code map across registered projects.", "holocore global-graph $ARGUMENTS", True),
    CommandDefinition("obsidian-vault", "Build the Obsidian view of all project knowledge.", "holocore obsidian-vault $ARGUMENTS", True),
    CommandDefinition("watch", "Automatically refresh every project and the Obsidian view.", "holocore watch $ARGUMENTS", True),
    CommandDefinition("sync-all", "Reconcile and refresh every registered project.", "holocore sync-all", True),
    CommandDefinition("update", "Update HoloCore, rescan AI clients, and reconcile every project.", "holocore update", True),
    CommandDefinition("install-check", "Check the installed HoloCore version and safe lifecycle commands.", "holocore install-check"),
    CommandDefinition("uninstall", "Remove the HoloCore CLI while preserving project and Home data.", "holocore uninstall", True),
    CommandDefinition("cleanup", "Preview or remove verified legacy HoloCore project artifacts.", "holocore cleanup $ARGUMENTS", True),
    CommandDefinition("ingest", "Add a file, folder, or URL to project memory, knowledge, and the code map.", "holocore ingest $ARGUMENTS", True),
    CommandDefinition("inbox-sync", "Add new files from this project's source Inbox.", "holocore inbox-sync", True),
    CommandDefinition("paths", "Show exactly where HoloCore stores every kind of data.", "holocore paths"),
    CommandDefinition("open-archive", "Open the visible Archive folder for Obsidian or file browsing.", "holocore open-archive"),
    CommandDefinition("search", "Search project knowledge, code, and useful memories.", 'holocore search "$ARGUMENTS"'),
    CommandDefinition("research", "Build a read-only evidence brief from the Code Map, Knowledge, and Memories.", 'holocore research "$ARGUMENTS"'),
    CommandDefinition("architecture", "Build a read-only architecture brief with structural evidence and prior context.", 'holocore architecture "$ARGUMENTS"'),
    CommandDefinition("remember", "Save a detail for later project work.", 'holocore remember "$ARGUMENTS"', True),
    CommandDefinition("recall", "Search memories from earlier project work.", 'holocore recall "$ARGUMENTS"'),
    CommandDefinition("memory-collect", "Save new project or conversation details to Memories.", "holocore memory-collect $ARGUMENTS", True),
    CommandDefinition("memory-checkpoint", "Show the latest Memories collection progress.", "holocore memory-checkpoint $ARGUMENTS"),
    CommandDefinition("memory-event", "Save an event to project Memories.", 'holocore memory-event "$ARGUMENTS"', True),
    CommandDefinition("memory-timeline", "Show the project Memories timeline.", "holocore memory-timeline $ARGUMENTS"),
    CommandDefinition("memory-consolidate", "Merge duplicate saved Memories.", "holocore memory-consolidate $ARGUMENTS", True),
    CommandDefinition("memory-export", "Export selected project Memories.", "holocore memory-export $ARGUMENTS"),
    CommandDefinition("memory-groups", "List focused Memory groups in the active project.", "holocore memory-groups"),
    CommandDefinition("memory-track", "Record a change to a named project item.", "holocore memory-track $ARGUMENTS", True),
    CommandDefinition("memory-history", "Show the remembered history for a named project item.", "holocore memory-history $ARGUMENTS"),
    CommandDefinition("code-map-refresh", "Refresh the project Code Map.", "holocore code-map-refresh", True),
    CommandDefinition("code-map-view", "Generate and open the self-contained Code Map view.", "holocore code-map-view $ARGUMENTS"),
    CommandDefinition("code-map-explain", "Explain a mapped item and its evidence.", "holocore code-map-explain $ARGUMENTS"),
    CommandDefinition("code-map-path", "Find the shortest relationship path between mapped items.", "holocore code-map-path $ARGUMENTS"),
    CommandDefinition("code-map-affected", "Find mapped items affected by a structural target.", "holocore code-map-affected $ARGUMENTS"),
    CommandDefinition("code-map-neighborhood", "Inspect a bounded relationship neighborhood.", "holocore code-map-neighborhood $ARGUMENTS"),
    CommandDefinition("code-map-groups", "List groups of related Code Map items.", "holocore code-map-groups $ARGUMENTS"),
    CommandDefinition("code-map-audit", "Audit unresolved, duplicate, orphan, and confidence issues in the Code Map.", "holocore code-map-audit"),
    CommandDefinition("code-map-export", "Export the Code Map as JSON, HTML, Markdown, and a manifest.", "holocore code-map-export $ARGUMENTS", True),
    CommandDefinition("archive-search", "Search verified project knowledge.", 'holocore archive-search "$ARGUMENTS"'),
    CommandDefinition("archive-create", "Create a verified project knowledge note.", "holocore archive-create $ARGUMENTS", True),
    CommandDefinition("status", "Show project, knowledge, code map, memory, and connection status.", "holocore status"),
    CommandDefinition("models-status", "Discover local models and show the active extraction mode.", "holocore models status"),
    CommandDefinition("models-configure", "Configure or clear the optional Home-level local model.", "holocore models $ARGUMENTS", True),
    CommandDefinition("console", "Open the local Console for chat history, memories, knowledge, code maps, agent activity, and setup.", "holocore console"),
    CommandDefinition("handoffs", "Create and manage claimable work passed between agents.", "holocore handoffs $ARGUMENTS", True),
    CommandDefinition("doctor", "Run read-only HoloCore diagnostics.", "holocore doctor"),
)
COMMAND_DEFINITIONS = COMMANDS


def get_command(name: str) -> CommandDefinition:
    normalized = name.removeprefix("holocore-").replace(" ", "-")
    compatibility = {
        "animus-sync": "memory-collect", "animus-checkpoint": "memory-checkpoint",
        "diary": "memory-event", "timeline": "memory-timeline", "consolidate": "memory-consolidate",
        "animus-export": "memory-export", "animus-decks": "memory-groups",
        "animus-signal": "memory-track", "animus-chronicle": "memory-history",
        "atlas-refresh": "code-map-refresh", "atlas-view": "code-map-view",
        "atlas-explain": "code-map-explain", "atlas-path": "code-map-path",
        "atlas-affected": "code-map-affected", "atlas-neighborhood": "code-map-neighborhood",
        "atlas-constellations": "code-map-groups", "atlas-audit": "code-map-audit",
        "atlas-export": "code-map-export",
    }
    normalized = compatibility.get(normalized, normalized)
    for command in COMMANDS:
        if command.name == normalized:
            return command
    raise KeyError(name)


def _markdown(command: CommandDefinition, platform: str, argument_token: str = "$ARGUMENTS") -> str:
    safety = "This command may write project data; confirm the requested scope before running it." if command.write else "This is a read-only operation."
    return (
        "---\n"
        f"description: {command.description}\n"
        "---\n\n"
        f"# {command.slug}\n\n{command.description}\n\n"
        f"{safety}\n\nRun the following from the project root and return its output:\n\n"
        f"```text\n{command.invocation}\n```\n\n"
        f"Arguments supplied by the user: `{argument_token}`\n"
    )


def render_claude_command(command: CommandDefinition | str) -> str:
    return _markdown(get_command(command) if isinstance(command, str) else command, "claude")


def render_cursor_command(command: CommandDefinition | str) -> str:
    return _markdown(get_command(command) if isinstance(command, str) else command, "cursor")


def render_gemini_command(command: CommandDefinition | str) -> str:
    return _markdown(get_command(command) if isinstance(command, str) else command, "gemini")


def render_opencode_command(command: CommandDefinition | str) -> str:
    return _markdown(get_command(command) if isinstance(command, str) else command, "opencode")


def render_codex_skill(command: CommandDefinition | str) -> str:
    item = get_command(command) if isinstance(command, str) else command
    invocation = item.invocation.replace('"$ARGUMENTS"', "").replace("$ARGUMENTS", "").strip()
    safety = "This may write project data; confirm the requested scope before running it." if item.write else "This is read-only."
    return (
        "---\n"
        f"name: {item.slug}\n"
        f"description: {item.description}\n"
        "---\n\n"
        f"# {item.slug}\n\n{item.description}\n\n{safety}\n\n"
        "Interpret any details following the skill mention as the user's arguments. "
        "Run the corresponding command from the project root, adding those arguments where appropriate:\n\n"
        f"```text\n{invocation}\n```\n"
    )


def render_claude_skill(command: CommandDefinition | str) -> str:
    item = get_command(command) if isinstance(command, str) else command
    return render_codex_skill(item)


def render_gemini_command(command: CommandDefinition | str) -> str:
    item = get_command(command) if isinstance(command, str) else command
    invocation = item.invocation.replace("$ARGUMENTS", "{{args}}")
    prompt = (
        f"{item.description} Run this from the project root and return the output: "
        f"{invocation}. User arguments: {{{{args}}}}"
    )
    escaped_description = item.description.replace('"', '\\"')
    escaped_prompt = prompt.replace('"""', '\\"\\"\\"')
    return f'description = "{escaped_description}"\nprompt = """{escaped_prompt}"""\n'


def _files(prefix: str, renderer, commands: Iterable[CommandDefinition] = COMMANDS) -> dict[str, str]:
    return {f"{prefix}/{item.slug}.md": renderer(item) for item in commands}


def render_claude_commands() -> dict[str, str]:
    files = _files(".claude/commands", render_claude_command)
    files.update({f".claude/skills/{item.slug}/SKILL.md": render_claude_skill(item) for item in COMMANDS})
    return files


def render_cursor_commands() -> dict[str, str]:
    return _files(".cursor/commands", render_cursor_command)


def render_gemini_commands() -> dict[str, str]:
    return {f".gemini/commands/{item.slug}.toml": render_gemini_command(item) for item in COMMANDS}


def render_opencode_commands() -> dict[str, str]:
    return _files(".opencode/commands", render_opencode_command)


def render_codex_skills() -> dict[str, str]:
    return {f".agents/skills/{item.slug}/SKILL.md": render_codex_skill(item) for item in COMMANDS}


def render_all_commands() -> dict[str, dict[str, str]]:
    return {
        "claude": render_claude_commands(), "cursor": render_cursor_commands(),
        "gemini": render_gemini_commands(), "opencode": render_opencode_commands(),
        "codex": render_codex_skills(),
    }


__all__ = [
    "CommandDefinition", "COMMANDS", "COMMAND_DEFINITIONS", "get_command",
    "render_claude_command", "render_cursor_command", "render_gemini_command",
    "render_opencode_command", "render_codex_skill", "render_claude_skill", "render_claude_commands",
    "render_cursor_commands", "render_gemini_commands", "render_opencode_commands",
    "render_codex_skills", "render_all_commands",
]
