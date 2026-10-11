from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from .config import Config
from .engine import HoloCoreEngine
from .home import HomeManager
from .layout import format_paths, world_paths
from .lifecycle import installation_check, sync_all, uninstall_tool, update_install
from .animus import Animus
from .animus_mining import AnimusMiner, MiningOptions
from .animus_retrieval import AnimusRetriever
from .archive_source import sync_archive_source
from .archive_promote import promote_sources
from . import __version__


PLATFORMS = ("claude", "codex", "gemini", "cursor", "opencode", "generic")


def _platform_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--platform", action="append", choices=PLATFORMS, help="Configure only this AI client (repeatable). Default: all clients.")
    parser.add_argument("-y", "--yes", action="store_true", help="Connect every detected AI client without prompting")


def _selected_platforms(explicit: list[str] | None, assume_yes: bool) -> list[str]:
    if explicit:
        return list(dict.fromkeys(explicit))
    from .install import detect_installed_clients

    detected = detect_installed_clients()
    available = [name for name, detail in detected.items() if detail["installed"]]
    if assume_yes or not sys.stdin.isatty():
        return available
    if not available:
        print("No supported AI clients were detected. HoloCore will install its local data folders only.")
        return []
    print("Detected AI clients:")
    selected: list[str] = []
    for name in available:
        evidence = ", ".join(str(item) for item in detected[name]["evidence"])
        try:
            answer = input(f"  Connect {name.title()}? [Y/n] ({evidence}) ").strip().casefold()
        except EOFError:
            # Redirected or headless callers cannot answer a prompt. Preserve
            # the documented default and select the clients already detected.
            return available
        if answer in {"", "y", "yes"}:
            selected.append(name)
    return selected


def _selected_home(explicit: str | None) -> Path:
    manager = HomeManager(explicit)
    if explicit or manager.pointer_path.exists():
        return manager.home
    default = manager.default_home
    if sys.stdin.isatty():
        answer = input(f"Where should HoloCore store the shared second brain? [{default}] ").strip()
        return Path(answer).expanduser().resolve() if answer else default
    return default


def _format_setup(value: dict) -> str:
    paths = value["paths"]
    atlas = value["atlas"]
    return f"""HoloCore is ready.

Project
  {value['world']}

Your data
  HoloCore Home             {paths['home']}
  Shared knowledge vault    {paths['archive']}
  This project's knowledge  {paths['world_archive']}
  Code map                  {paths['atlas_json']} ({atlas['nodes']} items)
  Code map visual           {paths['atlas_html']}
  Activity memory           {paths['memory_shards']}
  Raw chat audit            {paths['raw_chats']}
  Source Inbox              {paths['ingest_inbox']}
  Immutable raw sources     {paths['raw_sources']}

AI access
  One shared connection bundle  {paths['home']}/Connections
  MCP tools accept the active project root, so clients connect once at user scope.
  Project-local connection, skill, hook, and policy copies are no longer generated.

Obsidian is optional. To use it, open this folder as a vault:
  {paths['archive']}

Full local guide:
  {paths['start_here']}"""


def _format_connect(value: dict) -> str:
    changed = len(value.get("created", [])) + len(value.get("updated", []))
    warnings = value.get("warnings", [])
    suffix = "\n\nWarnings:\n  " + "\n  ".join(warnings) if warnings else ""
    return f"""HoloCore's shared AI connection bundle is ready ({changed} files created or repaired).

Claude Code
  Shared config: {value['paths']['claude_mcp']}
  Link this once at user scope, then pass the active World root to HoloCore tools.

Codex
  Shared config: {value['paths']['codex_mcp']}
  Skills: {value['paths']['codex_skills']}
  Link this once at user scope, then invoke $holocore-search.{suffix}"""


def _format_status(value: dict) -> str:
    ready = value["readiness"]["ready"]
    claude = value["integrations"]["claude"]
    codex = value["integrations"]["codex"]
    return f"""HoloCore {'is ready' if ready else 'needs attention'}.

Project:         {value['world']}
Knowledge:       {value['paths']['archive']}
Code map:        {value['paths']['atlas_json']} ({'fresh' if value['atlas'].get('fresh') else 'missing or stale'})
Activity memory: {value['paths']['memory_shards']}

AI connections
  Claude Code: {'connected' if claude['connected'] else claude['detail']} · capture {'on' if claude['capture'] else 'off'} · {claude['slash_commands']} slash commands · {claude['skills']} skills
  Codex:       {'connected' if codex['connected'] else codex['detail']} · capture {'on' if codex['capture'] else 'off'} · {codex['skills']} skills

Run `holocore paths` for every location or `holocore connect` to repair AI connections."""


def _open_folder(path: Path) -> None:
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def main() -> int:
    parser = argparse.ArgumentParser(prog="holocore", description="Unified local knowledge engine")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--root", default=".", help="World/project directory (default: current directory)")
    parser.add_argument("--json", action="store_true", help="Print stable machine-readable JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", help="Set up a Project and connect detected AI clients")
    setup.add_argument("path", nargs="?")
    setup.add_argument("--git", action="store_true", help="Initialize Git if needed (off by default)")
    setup.add_argument("--home", help="Shared HoloCore Home directory; first setup prompts when omitted")
    _platform_options(setup)
    init = sub.add_parser("init", help="Initialize storage and integrations without building Atlas")
    init.add_argument("path", nargs="?")
    init.add_argument("--no-git", action="store_true")
    init.add_argument("--home", help="Shared HoloCore Home directory")
    _platform_options(init)
    connect = sub.add_parser("connect", help="Install or repair AI client connections")
    connect.add_argument("path", nargs="?")
    connect.add_argument("--home", help="Shared HoloCore Home directory")
    _platform_options(connect)
    sub.add_parser("paths", help="Show every HoloCore data and integration location")
    sub.add_parser("open-archive", help="Open the visible Archive folder")
    sub.add_parser("status")
    sub.add_parser("detect-clients", help="Find supported AI clients installed on this computer")
    sub.add_parser("connections", help="Show which installed AI clients are connected to HoloCore")
    console = sub.add_parser("console", help="Open the local HoloCore browser Console")
    console.add_argument("--port", type=int, default=0, help="Local port (0 chooses a free port)")
    console.add_argument("--no-open", action="store_true", help="Print the URL without opening a browser")
    console.add_argument("--export", action="store_true", help="Write Home\\Console\\console.html and exit")
    sub.add_parser("doctor")
    home_command = sub.add_parser("home", help="Show or select the shared HoloCore Home")
    home_command.add_argument("path", nargs="?")
    home_command.add_argument("--path", dest="home_path", help="Shared HoloCore Home directory (alternative to the positional path)")
    sub.add_parser("worlds", aliases=["projects"], help="List projects linked to HoloCore Home")
    global_graph = sub.add_parser("global-graph", help="Build one code map across registered projects")
    global_graph.add_argument("--output")
    obsidian_vault = sub.add_parser("obsidian-vault", help="Build the Obsidian vault view of all registered Worlds")
    obsidian_vault.add_argument("--output", help="Optional output folder; defaults to HoloCore Obsidian Vault in Home")
    watch = sub.add_parser("watch", help="Automatically refresh every World and the Obsidian vault")
    watch.add_argument("--home", help="Shared HoloCore Home directory")
    watch.add_argument("--interval", type=float, default=30.0, help="Polling interval in seconds (default: 30)")
    watch.add_argument("--once", action="store_true", help="Run one change check and exit")
    watch.add_argument("--install", action="store_true", help="Install an automatic Windows logon task")
    watch.add_argument("--uninstall", action="store_true", help="Remove the automatic Windows logon task")
    sync_all_command = sub.add_parser("sync-all", help="Reconcile every registered Project")
    sync_all_command.add_argument("--detected", action="store_true", help="Connect only AI clients detected on this computer")
    sync_all_command.add_argument("-y", "--yes", action="store_true", help="Accept all detected AI clients without prompting")
    update = sub.add_parser("update", help="Update HoloCore and reconcile every World")
    update.add_argument("--no-sync", action="store_true", help="Only update the CLI; skip the potentially lengthy World/Atlas reconciliation")
    update.add_argument("-y", "--yes", action="store_true", help="Connect every detected AI client without prompting")
    auto_update = sub.add_parser("auto-update", help="Enable, disable, check, or inspect lightweight session-driven updates")
    auto_group = auto_update.add_mutually_exclusive_group()
    auto_group.add_argument("--enable", action="store_true")
    auto_group.add_argument("--disable", action="store_true")
    auto_group.add_argument("--check-now", action="store_true")
    sub.add_parser("install-check", help="Show installed version and safe upgrade/uninstall commands")
    sub.add_parser("uninstall", help="Uninstall the HoloCore CLI while preserving project and Home data")
    cleanup = sub.add_parser("cleanup", help="Safely remove legacy HoloCore files from this project")
    cleanup.add_argument("--apply", action="store_true", help="Actually remove verified legacy artifacts")
    search = sub.add_parser("search"); search.add_argument("query"); search.add_argument("--world")
    sub.add_parser("atlas-refresh")
    sub.add_parser("atlas-html"); sub.add_parser("atlas-view")
    atlas_search = sub.add_parser("atlas-search"); atlas_search.add_argument("query")
    atlas_explain = sub.add_parser("atlas-explain"); atlas_explain.add_argument("query")
    atlas_path = sub.add_parser("atlas-path"); atlas_path.add_argument("source"); atlas_path.add_argument("target"); atlas_path.add_argument("--max-depth", type=int)
    atlas_affected = sub.add_parser("atlas-affected"); atlas_affected.add_argument("query"); atlas_affected.add_argument("--depth", type=int, default=2)
    atlas_neighborhood = sub.add_parser("atlas-neighborhood"); atlas_neighborhood.add_argument("query"); atlas_neighborhood.add_argument("--depth", type=int, default=1)
    atlas_export = sub.add_parser("atlas-export"); atlas_export.add_argument("output"); atlas_export.add_argument("--format", action="append", dest="formats")
    sub.add_parser("atlas-audit")
    atlas_constellations = sub.add_parser("atlas-constellations"); atlas_constellations.add_argument("--min-size", type=int, default=2)
    mine = sub.add_parser("mine"); mine.add_argument("path", nargs="?", default="."); mine.add_argument("--sector", default="project"); mine.add_argument("--mode", choices=("files", "conversations", "git"), default="files"); mine.add_argument("--ignore", action="append", default=[])
    sync = sub.add_parser("animus-sync"); sync.add_argument("--sector", default="project"); sync.add_argument("--mode", choices=("files", "conversations", "git"), default="files")
    checkpoint = sub.add_parser("animus-checkpoint"); checkpoint.add_argument("--sector", default="project"); checkpoint.add_argument("--mode", choices=("files", "conversations", "git"), default="files"); checkpoint.add_argument("--source", default="")
    diary = sub.add_parser("diary"); diary.add_argument("content"); diary.add_argument("--title", default=""); diary.add_argument("--sector", default="project"); diary.add_argument("--kind", default="episode"); diary.add_argument("--source", default="cli")
    timeline = sub.add_parser("timeline"); timeline.add_argument("--sector", default=None); timeline.add_argument("--limit", type=int, default=100)
    consolidate = sub.add_parser("consolidate"); consolidate.add_argument("--sector", default=None)
    export = sub.add_parser("animus-export"); export.add_argument("--sector", default=None); export.add_argument("--limit", type=int, default=1000)
    decks = sub.add_parser("animus-decks", aliases=["animus-rooms"]); decks.add_argument("--world", default=None)
    signal = sub.add_parser("animus-signal", aliases=["animus-entity"]); signal.add_argument("name"); signal.add_argument("relation"); signal.add_argument("value"); signal.add_argument("--kind", default="concept"); signal.add_argument("--deck", "--room", dest="room"); signal.add_argument("--source", default="cli")
    chronicle = sub.add_parser("animus-chronicle", aliases=["animus-entity-timeline"]); chronicle.add_argument("--signal", "--entity", dest="entity"); chronicle.add_argument("--deck", "--room", dest="room"); chronicle.add_argument("--limit", type=int, default=100)
    remember = sub.add_parser("remember"); remember.add_argument("content"); remember.add_argument("--sector", default="general"); remember.add_argument("--source", default="")
    recall = sub.add_parser("recall"); recall.add_argument("query"); recall.add_argument("--sector")
    ingest = sub.add_parser("ingest-chat"); ingest.add_argument("file"); ingest.add_argument("--sector", default="conversations"); ingest.add_argument("--instructions", default="")
    source_ingest = sub.add_parser("ingest", help="Ingest a file, folder, or URL")
    source_ingest.add_argument("source")
    source_ingest.add_argument("--title")
    sub.add_parser("inbox-sync", help="Ingest new files from the visible World Inbox")
    sub.add_parser("archive-init")
    archive_search = sub.add_parser("archive-search"); archive_search.add_argument("query")
    archive_create = sub.add_parser("archive-create"); archive_create.add_argument("path"); archive_create.add_argument("content")
    archive_source = sub.add_parser("archive-source-sync", help="One-way sync curated Markdown into the Second Brain World")
    archive_source.add_argument("--source", required=True, help="Existing curated Markdown directory")
    promote = sub.add_parser("archive-promote", help="Generate locally reasoned entries in this World's wiki")
    promote.add_argument("--source", required=True, help="Project or documentation directory to promote")
    promote.add_argument("--scope", choices=("docs", "all"), default="docs", help="Promote documentation only (default) or all supported text")
    promote.add_argument("--dry-run", action="store_true", help="Plan changes without writing files")
    promote.add_argument("--no-summarize", action="store_true", help="Skip local-AI reasoning and preserve a deterministic excerpt")
    knowledge = sub.add_parser("knowledge", help="Search or add verified project knowledge")
    knowledge_sub = knowledge.add_subparsers(dest="knowledge_command", required=True)
    knowledge_search = knowledge_sub.add_parser("search"); knowledge_search.add_argument("query")
    knowledge_add = knowledge_sub.add_parser("add"); knowledge_add.add_argument("path"); knowledge_add.add_argument("content")
    code_map = sub.add_parser("map", help="Explore the project code map")
    map_sub = code_map.add_subparsers(dest="map_command", required=True)
    map_sub.add_parser("view")
    map_search = map_sub.add_parser("search"); map_search.add_argument("query")
    memory = sub.add_parser("memory", help="Search or add project memory")
    memory_sub = memory.add_subparsers(dest="memory_command", required=True)
    memory_search = memory_sub.add_parser("search"); memory_search.add_argument("query"); memory_search.add_argument("--area", dest="sector")
    memory_add = memory_sub.add_parser("add"); memory_add.add_argument("content"); memory_add.add_argument("--area", dest="sector", default="general"); memory_add.add_argument("--source", default="cli")
    activity = sub.add_parser("activity", help="Inspect agent activity and affected items")
    activity_sub = activity.add_subparsers(dest="activity_command", required=True)
    activity_sub.add_parser("status", help="Show recorded run, action, and item counts")
    activity_runs = activity_sub.add_parser("runs", help="List agent runs"); activity_runs.add_argument("--limit", type=int, default=100)
    activity_show = activity_sub.add_parser("show", help="Show actions in one run"); activity_show.add_argument("run_id"); activity_show.add_argument("--limit", type=int, default=500)
    activity_affected = activity_sub.add_parser("affected", help="Find activity that affected a file or directory"); activity_affected.add_argument("query"); activity_affected.add_argument("--limit", type=int, default=100)
    handoff = sub.add_parser("handoff", help="Create a bounded handoff from the latest or selected agent run")
    handoff.add_argument("run_id", nargs="?")
    handoff.add_argument("--actions", type=int, default=50)
    handoff.add_argument("--items", type=int, default=100)
    runs = sub.add_parser("runs", help="Inspect recorded agent runs and actions")
    runs_sub = runs.add_subparsers(dest="runs_command", required=True)
    runs_list = runs_sub.add_parser("list"); runs_list.add_argument("--limit", type=int, default=100)
    runs_show = runs_sub.add_parser("show"); runs_show.add_argument("run_id"); runs_show.add_argument("--limit", type=int, default=500)
    sub.add_parser("activity-status", help="Show Agent Activity Ledger counts")
    capture_command = sub.add_parser("capture-hook", help=argparse.SUPPRESS); capture_command.add_argument("--client", choices=("claude", "codex"), required=True)
    sub.add_parser("mcp", help=argparse.SUPPRESS)
    args = parser.parse_args()
    root = Path(getattr(args, "path", None) or args.root).resolve() if args.command in {"setup", "init", "connect"} else Path(args.root).resolve()

    if args.command == "cleanup":
        from .cleanup import cleanup_legacy
        value = cleanup_legacy(root, apply=args.apply)
        print(json.dumps(value, indent=2, default=str))
        return 0
    if args.command == "capture-hook":
        from .capture_hook import run
        try:
            payload = json.loads(sys.stdin.read() or "{}")
            run(payload if isinstance(payload, dict) else {}, args.client)
            return 0
        except Exception:
            return 0
    if args.command == "mcp":
        from .mcp_server import main as mcp_main
        return mcp_main()
    if args.command == "detect-clients":
        from .install import detect_installed_clients
        value = detect_installed_clients()
        if args.json:
            print(json.dumps(value, indent=2, default=str))
        else:
            for name, detail in value.items():
                state = "detected" if detail["installed"] else "not found"
                evidence = f" ({', '.join(detail['evidence'])})" if detail["evidence"] else ""
                print(f"{name.title():10} {state}{evidence}")
        return 0
    if args.command == "connections":
        from .install import inspect_client_connections
        value = inspect_client_connections()
        if args.json:
            print(json.dumps(value, indent=2, default=str))
        else:
            for name, detail in value.items():
                print(f"{name.title():10} {detail['state']:16} {detail['detail']}")
        return 0
    if args.command == "console":
        from .console import export_console, serve_console
        if args.export:
            print(export_console(root))
            return 0
        return serve_console(root, port=args.port, open_browser=not args.no_open)
    elif args.command == "paths":
        value = world_paths(root, Config.load(root=root))
        output = json.dumps(value, indent=2) if args.json else format_paths(value)
        print(output)
        return 0
    if args.command == "home":
        manager = HomeManager()
        selected_path = args.home_path or args.path
        value = manager.select_home(selected_path) if selected_path else {
            "home": str(manager.home), "archive": str(manager.archive), "worlds_file": str(manager.worlds_path), "selected": manager.pointer_path.exists()
        }
        print(json.dumps(value, indent=2, default=str) if args.json else f"HoloCore Home: {value['home']}\nObsidian Archive: {Path(value['home']) / 'Archive'}\nWorld registry: {Path(value['home']) / 'worlds.json'}")
        return 0
    if args.command in {"worlds", "projects"}:
        value = HomeManager().list_worlds()
        if args.json:
            print(json.dumps(value, indent=2, default=str))
        else:
            lines = [f"Registered Projects ({value['count']}):"] + [f"  {item['name']}  {item['root']}" for item in value["worlds"]]
            print("\n".join(lines))
        return 0
    if args.command == "global-graph":
        from .global_graph import build_global_graph
        manager = HomeManager()
        output = Path(args.output).expanduser() if args.output else manager.home / "global-graph" / "graph.json"
        value = build_global_graph(manager.home, output=output)
        print(json.dumps(value, indent=2, default=str) if args.json else f"Global code map written to {value['path']}")
        return 0
    if args.command == "obsidian-vault":
        from .obsidian_vault import build_obsidian_vault
        manager = HomeManager()
        value = build_obsidian_vault(manager.home, output=args.output)
        print(json.dumps(value, indent=2, default=str) if args.json else f"Obsidian vault written to {value['path']}")
        return 0
    if args.command == "watch":
        from .obsidian_watch import install_windows_task, uninstall_windows_task, watch_home
        home = HomeManager(args.home).home if args.home else HomeManager().home
        if args.uninstall:
            value = uninstall_windows_task()
        elif args.install:
            value = install_windows_task(home, interval=args.interval)
        else:
            value = {"cycles": watch_home(home, interval=args.interval, once=args.once)}
        print(json.dumps(value, indent=2, default=str) if args.json else (f"Automatic refresh installed: {value['task']}" if args.install else f"Automatic refresh removed: {value['task']}" if args.uninstall else f"Watch completed after {value['cycles']} cycle(s)."))
        return 0
    if args.command == "sync-all":
        platforms = _selected_platforms(None, args.yes or args.json) if args.detected else None
        value = sync_all(platforms=platforms)
        print(json.dumps(value, indent=2, default=str) if args.json else f"Reconciled {value['updated']} of {value['count']} Worlds ({value['failed']} failed).")
        return 0
    if args.command == "update":
        value = update_install(reconcile=False)
        detected_platforms = [] if value.get("update_queued") else _selected_platforms(None, args.yes or args.json)
        reconciliation = None if args.no_sync or value.get("update_queued") else sync_all(platforms=detected_platforms)
        value["detected_platforms"] = detected_platforms
        value["reconciliation"] = reconciliation
        if args.json:
            print(json.dumps(value, indent=2, default=str))
        elif value.get("update_queued"):
            print("HoloCore update queued. It will install and rescan AI clients after this process exits.")
        elif reconciliation is None:
            print("HoloCore updated. World reconciliation skipped. Run `holocore sync-all` when ready.")
        else:
            print(f"HoloCore updated. Reconciled {reconciliation['updated']} of {reconciliation['count']} Worlds.")
        return 0
    if args.command == "auto-update":
        from .auto_update import configure as configure_auto_update, maybe_check, status as auto_update_status
        home = HomeManager().home
        if args.enable:
            value = configure_auto_update(home, enabled=True)
        elif args.disable:
            value = configure_auto_update(home, enabled=False)
        elif args.check_now:
            value = maybe_check(home, current_version=__version__, force=True)
        else:
            value = auto_update_status(home)
        print(json.dumps(value, indent=2, default=str) if args.json else (
            "Automatic updates enabled. HoloCore will check at most once per day during active sessions." if args.enable
            else "Automatic updates disabled." if args.disable
            else f"Automatic updates: {'enabled' if value['enabled'] else 'disabled'} | latest: {value.get('latest_version') or 'not checked'}"
        ))
        return 0
    if args.command == "install-check":
        value = installation_check()
        print(json.dumps(value, indent=2) if args.json else f"Installed HoloCore: {value['installed_version']}\nUpdate: {value['upgrade_command']}\nUninstall: {value['uninstall_command']}\nProject and Home data are preserved.")
        return 0
    if args.command == "uninstall":
        value = uninstall_tool()
        print(json.dumps(value, indent=2) if args.json else "HoloCore CLI uninstalled. Project and Home data were preserved.")
        return 0
    if args.command == "open-archive":
        config = Config.load(root=root)
        archive = (config.home / "Archive") if config.home else config.vault
        if not archive.is_dir():
            parser.error(f"Archive does not exist yet: {archive}. Run `holocore setup` first.")
        _open_folder(archive)
        value = {"opened": str(archive)}
        print(json.dumps(value, indent=2) if args.json else f"Opened Archive: {archive}")
        return 0

    engine = HoloCoreEngine(root)
    if args.command == "setup": value = engine.setup(git=args.git, platforms=_selected_platforms(args.platform, args.yes or args.json), home=_selected_home(args.home))
    elif args.command == "init": value = engine.initialize(git=not args.no_git, platforms=_selected_platforms(args.platform, args.yes or args.json), home=_selected_home(args.home))
    elif args.command == "connect": value = engine.connect(platforms=_selected_platforms(args.platform, args.yes or args.json), home=_selected_home(args.home))
    elif args.command in {"status", "doctor"}: value = engine.status()
    elif args.command == "search": value = [r.__dict__ for r in engine.search(args.query, args.world)]
    elif args.command == "atlas-refresh": value = engine.refresh()
    elif args.command in {"atlas-html", "atlas-view"}:
        atlas = engine.ensure_atlas()
        html = Path(atlas["html"])
        if args.command == "atlas-view":
            import webbrowser
            webbrowser.open(html.resolve().as_uri())
        value = {**atlas, "html": str(html), "opened": args.command == "atlas-view"}
    elif args.command == "atlas-search": value = engine.router.atlas.search(args.query)
    elif args.command == "atlas-explain": value = engine.router.atlas.explain(args.query)
    elif args.command == "atlas-path": value = engine.router.atlas.shortest_path(args.source, args.target, max_depth=args.max_depth)
    elif args.command == "atlas-affected": value = engine.router.atlas.affected(args.query, depth=args.depth)
    elif args.command == "atlas-neighborhood": value = engine.router.atlas.neighborhood(args.query, depth=args.depth)
    elif args.command == "atlas-audit": value = engine.router.atlas.audit()
    elif args.command == "atlas-constellations": value = engine.router.atlas.constellations(min_size=args.min_size)
    elif args.command == "atlas-export":
        from .atlas_exports import export_atlas
        value = export_atlas(engine.router.atlas, args.output, args.formats)
    elif args.command in {"mine", "animus-sync", "animus-checkpoint", "diary", "timeline", "consolidate", "animus-export", "animus-decks", "animus-rooms", "animus-signal", "animus-entity", "animus-chronicle", "animus-entity-timeline"}:
        config = Config.load(root=root); animus = Animus(config.animus_path); world = config.world_id or root.name
        animus.create_world(world, root.name, {"root": str(root)})
        if getattr(args, "sector", None): animus.create_sector(world, args.sector)
        if args.command in {"animus-decks", "animus-rooms"}: value = [deck.__dict__ for deck in animus.decks(world=world)]
        elif args.command in {"animus-signal", "animus-entity"}: value = animus.record_signal_chronicle(args.name, args.relation, args.value, world=world, kind=args.kind, room=args.room, source_ref=args.source).__dict__
        elif args.command in {"animus-chronicle", "animus-entity-timeline"}: value = [event.__dict__ for event in animus.signal_chronicle(world=world, entity=args.entity, room=args.room, limit=args.limit)]
        elif args.command in {"mine", "animus-sync"}:
            path = Path(args.path if args.command == "mine" else root).resolve()
            value = AnimusMiner(animus).mine(MiningOptions(mode=args.mode, world=world, sector=args.sector, root=path, ignore=tuple(args.ignore) or MiningOptions().ignore))
        elif args.command == "animus-checkpoint":
            source = args.source or str(root.resolve()); value = animus.get_checkpoint(world=world, sector=args.sector, mode=args.mode, source_ref=source)
        elif args.command == "diary": value = animus.record_diary(args.content, world=world, sector=args.sector, title=args.title, kind=args.kind, source_ref=args.source)
        elif args.command == "timeline": value = animus.timeline(world=world, sector=args.sector, limit=args.limit)
        elif args.command == "consolidate": value = animus.consolidate(world=world, sector=args.sector)
        else:
            diary = [{"id": row.id, "world_id": row.world_id, "sector_id": row.sector_id,
                      "occurred_at": row.occurred_at, "title": row.title, "content": row.content,
                      "kind": row.kind, "metadata": dict(row.metadata),
                      "provenance": [p.__dict__ for p in row.provenance]}
                     for row in animus.timeline(world=world, sector=args.sector, limit=args.limit)]
            # Export both the episodic timeline and distilled memory so an AI
            # client can consume one scoped Animus payload.
            shards = [{"id": row.id, "world_id": row.world_id, "sector_id": row.sector_id,
                       "content": row.content, "route_hint": row.route_hint,
                       "metadata": dict(row.metadata), "created_at": row.created_at,
                       "updated_at": row.updated_at, "kind": "memory_shard"}
                      for row in animus.shards(world=world, sector=args.sector)][-args.limit:]
            value = diary + shards
    elif args.command == "remember": value = engine.remember(args.content, args.sector, args.source)
    elif args.command == "recall":
        engine.router.ensure_memory_scope()
        value = [r.__dict__ for r in engine.router.animus.search(args.query, world=engine.router.world_id, sector=args.sector)]
    elif args.command == "ingest-chat":
        from .conversation_import import ConversationImporter
        imported = ConversationImporter().import_file(args.file)
        value = engine.ingest_chat(imported["messages"], sector=args.sector, source=str(Path(args.file).resolve()), custom_instructions=args.instructions)
        value["provider"] = imported["provider"]
        value["message_count"] = imported["message_count"]
    elif args.command == "ingest": value = engine.ingest_source(args.source, title=args.title)
    elif args.command == "inbox-sync": value = engine.sync_inbox()
    elif args.command == "archive-init": value = engine.router.archive.init_vault()
    elif args.command == "archive-search": value = engine.router.archive.search(args.query)
    elif args.command == "archive-create": value = engine.router.archive.create(args.path, args.content)
    elif args.command == "archive-source-sync": value = sync_archive_source(args.source, HomeManager().home)
    elif args.command == "archive-promote":
        config = Config.load(root=root)
        value = promote_sources(
            args.source,
            config.vault,
            scope=args.scope,
            summarize=not args.no_summarize,
            dry_run=args.dry_run,
            world=config.world_id or root.name,
            llm=config.llm,
        )
    elif args.command == "knowledge":
        value = engine.router.archive.search(args.query) if args.knowledge_command == "search" else engine.router.archive.create(args.path, args.content)
    elif args.command == "map":
        if args.map_command == "search":
            value = engine.router.atlas.search(args.query)
        else:
            atlas = engine.ensure_atlas(); html = Path(atlas["html"]); __import__("webbrowser").open(html.resolve().as_uri()); value = {**atlas, "opened": True}
    elif args.command == "memory":
        if args.memory_command == "add":
            value = engine.remember(args.content, args.sector, args.source)
        else:
            engine.router.ensure_memory_scope(); value = [r.__dict__ for r in engine.router.animus.search(args.query, world=engine.router.world_id, sector=args.sector)]
    elif args.command in {"activity", "handoff", "runs", "activity-status"}:
        from .activity import ActivityLedger
        config = Config.load(root=root); ledger = ActivityLedger(config.animus_path); project_id = config.world_id or root.name
        if args.command == "activity-status": value = ledger.status(project_id=project_id)
        elif args.command == "handoff": value = ledger.handoff(project_id=project_id, run_id=args.run_id, action_limit=args.actions, item_limit=args.items)
        elif args.command == "activity":
            if args.activity_command == "status": value = ledger.status(project_id=project_id)
            elif args.activity_command == "runs": value = ledger.runs(project_id=project_id, limit=args.limit)
            elif args.activity_command == "show": value = {"run_id": args.run_id, "actions": ledger.actions(args.run_id, limit=args.limit)}
            else: value = ledger.affected(args.query, project_id=project_id, limit=args.limit)
        elif args.runs_command == "list": value = ledger.runs(project_id=project_id, limit=args.limit)
        else: value = {"run_id": args.run_id, "actions": ledger.actions(args.run_id, limit=args.limit)}
    else: raise AssertionError(args.command)

    if args.json:
        output = json.dumps(value, indent=2, ensure_ascii=False, default=str)
    elif args.command == "setup": output = _format_setup(value)
    elif args.command in {"init", "connect"}: output = _format_connect(value)
    elif args.command in {"status", "doctor"}: output = _format_status(value)
    else: output = value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False, default=str)
    print(output)
    return 0


if __name__ == "__main__": raise SystemExit(main())
