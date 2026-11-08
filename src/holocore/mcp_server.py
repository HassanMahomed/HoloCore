from __future__ import annotations

import json
import sys
from pathlib import Path

from .engine import HoloCoreEngine
from .commands import COMMANDS
from .home import HomeManager
from .lifecycle import sync_all
from .memories import MemoryStore
from .memory_collection import CollectionOptions, MemoryCollector
from . import __version__


def _tool(name, description, properties=None, required=None):
    values = dict(properties or {})
    values.setdefault("root", {"type": "string", "description": "Active project folder for the shared HoloCore connection."})
    return {"name": name, "description": description, "inputSchema": {"type": "object", "properties": values, "required": required or []}}
TOOLS = [
    _tool("holocore_search", "Unified relevance-gated search", {"query": {"type": "string"}, "world": {"type": "string"}}, ["query"]),
    _tool("holocore_research", "Build a read-only research evidence brief", {"question": {"type": "string"}, "sector": {"type": "string"}}, ["question"]),
    _tool("holocore_architecture", "Build a read-only architecture evidence brief", {"question": {"type": "string"}, "sector": {"type": "string"}}, ["question"]),
    _tool("holocore_status", "Show project, knowledge, code map, memory, and connection status"),
    _tool("holocore_models_discover", "Discover supported optional model runtimes on loopback", {"base_url": {"type": "string"}}),
    _tool("holocore_models_status", "Show the active extraction mode and local-model health"),
    _tool("holocore_models_configure", "Select one installed local model for HoloCore Home", {"provider": {"type": "string", "enum": ["ollama"]}, "model": {"type": "string"}, "base_url": {"type": "string"}}, ["provider", "model"]),
    _tool("holocore_models_clear", "Remove the Home-level local-model selection and restore deterministic extraction"),
    _tool("holocore_archive_search", "Search verified project knowledge", {"query": {"type": "string"}}, ["query"]),
    _tool("holocore_archive_read", "Read a project knowledge note", {"path": {"type": "string"}}, ["path"]),
    _tool("holocore_archive_create", "Create or update a project knowledge note", {"path": {"type": "string"}, "title": {"type": "string"}, "content": {"type": "string"}}, ["path", "title", "content"]),
    _tool("holocore_code_map_refresh", "Refresh the project Code Map"),
    _tool("holocore_code_map_search", "Search files, code, and Knowledge map items", {"query": {"type": "string"}}, ["query"]),
    _tool("holocore_code_map_explain", "Explain a mapped item and its source", {"query": {"type": "string"}}, ["query"]),
    _tool("holocore_code_map_path", "Find the shortest relationship path between items", {"source": {"type": "string"}, "target": {"type": "string"}, "max_depth": {"type": "integer"}}, ["source", "target"]),
    _tool("holocore_code_map_affected", "Find agent actions linked to a file or code item", {"query": {"type": "string"}, "depth": {"type": "integer"}}, ["query"]),
    _tool("holocore_code_map_neighborhood", "Inspect a local relationship neighborhood", {"query": {"type": "string"}, "depth": {"type": "integer"}}, ["query"]),
    _tool("holocore_code_map_audit", "Audit Code Map quality and unresolved relationships"),
    _tool("holocore_code_map_groups", "List groups of related Code Map items", {"min_size": {"type": "integer"}}),
    _tool("holocore_code_map_html", "Generate the interactive Code Map view"),
    _tool("holocore_code_map_export", "Export Code Map JSON, HTML, Markdown, and a manifest", {"output": {"type": "string"}, "formats": {"type": "array"}}, ["output"]),
    _tool("holocore_remember", "Save a detail for later project work", {"content": {"type": "string"}, "sector": {"type": "string"}, "source": {"type": "string"}}, ["content"]),
    _tool("holocore_recall", "Search memories from earlier project work", {"query": {"type": "string"}, "sector": {"type": "string"}}, ["query"]),
    _tool("holocore_ingest_chat", "Store raw chat and distill summary/facts", {"messages": {"type": "array"}, "sector": {"type": "string"}, "instructions": {"type": "string"}}, ["messages"]),
    _tool("holocore_home", "Show the selected HoloCore Home and knowledge vault"),
    _tool("holocore_worlds", "List projects registered with the shared HoloCore Home"),
    _tool("holocore_global_graph", "Build one Code Map across registered projects", {"output": {"type": "string"}}),
    _tool("holocore_setup", "Register this project and connect supported AI clients. Ask the user to choose the shared HoloCore Home location first.", {"home": {"type": "string", "description": "User-chosen shared HoloCore Home directory"}}, ["home"]),
    _tool("holocore_sync_all", "Reconcile every registered project after an upgrade", {"regenerate": {"type": "boolean", "description": "Regenerate integrations with current plain-language names"}}),
    _tool("holocore_activity_affected", "Find agent actions and runs that affected a file or directory", {"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
    _tool("holocore_activity_handoff", "Create a privacy-bounded handoff from the latest or selected agent run", {"run_id": {"type": "string"}, "action_limit": {"type": "integer"}, "item_limit": {"type": "integer"}}),
    _tool("holocore_handoffs_list", "List claimable and completed agent handoffs", {"status": {"type": "string"}, "limit": {"type": "integer"}}),
    _tool("holocore_handoffs_create", "Create claimable work linked to a recorded agent run", {"run_id": {"type": "string"}, "title": {"type": "string"}, "note": {"type": "string"}}, ["run_id"]),
    _tool("holocore_handoffs_claim", "Atomically claim an open handoff and return its sanitized run context", {"handoff_id": {"type": "string"}, "agent": {"type": "string"}}, ["handoff_id", "agent"]),
    _tool("holocore_handoffs_close", "Complete or abandon a claimed handoff", {"handoff_id": {"type": "string"}, "outcome": {"type": "string", "enum": ["completed", "abandoned"]}, "agent": {"type": "string"}, "reason": {"type": "string"}}, ["handoff_id", "outcome"]),
    _tool("holocore_connections", "Show which supported AI clients are installed and connected"),
    _tool("holocore_ingest", "Add a local file, folder, or URL to project sources, memories, knowledge, and the Code Map", {"source": {"type": "string"}, "title": {"type": "string"}}, ["source"]),
    _tool("holocore_inbox_sync", "Ingest new files from this World's visible source Inbox"),
    _tool("holocore_memory_collect", "Collect one explicitly scoped project using files, conversations, or Git history", {"mode": {"type": "string"}, "path": {"type": "string"}, "sector": {"type": "string"}}, ["mode"]),
    _tool("holocore_memory_checkpoint", "Show the latest Memories collection progress", {"mode": {"type": "string"}, "source": {"type": "string"}, "sector": {"type": "string"}}, ["mode", "source"]),
    _tool("holocore_memory_event", "Save an event to project Memories", {"content": {"type": "string"}, "title": {"type": "string"}, "sector": {"type": "string"}}, ["content"]),
    _tool("holocore_memory_timeline", "Show the project Memories timeline", {"sector": {"type": "string"}, "limit": {"type": "integer"}}),
    _tool("holocore_memory_consolidate", "Merge duplicate saved Memories", {"sector": {"type": "string"}}),
    _tool("holocore_memory_export", "Export selected project Memories", {"sector": {"type": "string"}, "limit": {"type": "integer"}}),
    _tool("holocore_memory_groups", "List focused project Memory groups"),
    _tool("holocore_memory_track", "Record a change to a named project item", {"name": {"type": "string"}, "relation": {"type": "string"}, "value": {"type": "string"}, "kind": {"type": "string"}, "group": {"type": "string"}}, ["name", "relation", "value"]),
    _tool("holocore_memory_history", "Show remembered history for a named project item", {"item": {"type": "string"}, "group": {"type": "string"}, "limit": {"type": "integer"}}),
]
PROMPTS = [
    {
        "name": command.name,
        "description": command.description,
        "arguments": [{"name": "arguments", "description": "Optional command details", "required": False}],
    }
    for command in COMMANDS
]


def get_prompt(name: str, arguments: dict) -> dict:
    command = next((item for item in COMMANDS if item.name == name), None)
    if command is None:
        raise ValueError(f"Unknown HoloCore prompt: {name}")
    supplied = str(arguments.get("arguments", "")).strip()
    invocation = command.invocation.replace("$ARGUMENTS", supplied).strip()
    if name == "setup":
        text = (
            "Before running setup, ask the user where the one shared HoloCore Home should live. "
            "Explain that its Archive folder is the Obsidian vault shared by every project. "
            "After the user chooses a path, run: holocore setup --home <chosen-path>"
        )
    else:
        text = f"Run this from the current World and return the focused result: {invocation}"
    return {
        "description": command.description,
        "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
    }


def call(engine: HoloCoreEngine, name: str, args: dict):
    compatibility_tools = {
        "holocore_atlas_refresh": "holocore_code_map_refresh",
        "holocore_atlas_search": "holocore_code_map_search",
        "holocore_atlas_explain": "holocore_code_map_explain",
        "holocore_atlas_path": "holocore_code_map_path",
        "holocore_atlas_affected": "holocore_code_map_affected",
        "holocore_atlas_neighborhood": "holocore_code_map_neighborhood",
        "holocore_atlas_audit": "holocore_code_map_audit",
        "holocore_atlas_constellations": "holocore_code_map_groups",
        "holocore_atlas_html": "holocore_code_map_html",
        "holocore_atlas_export": "holocore_code_map_export",
        "holocore_animus_mine": "holocore_memory_collect",
        "holocore_animus_checkpoint": "holocore_memory_checkpoint",
        "holocore_animus_diary": "holocore_memory_event",
        "holocore_animus_timeline": "holocore_memory_timeline",
        "holocore_animus_consolidate": "holocore_memory_consolidate",
        "holocore_animus_export": "holocore_memory_export",
        "holocore_animus_decks": "holocore_memory_groups",
        "holocore_animus_signal": "holocore_memory_track",
        "holocore_animus_chronicle": "holocore_memory_history",
    }
    name = compatibility_tools.get(name, name)
    if name == "holocore_search": return [r.__dict__ for r in engine.search(args["query"], args.get("world"))]
    if name in {"holocore_research", "holocore_architecture"}:
        from .guided_workflows import run_guided_workflow
        return run_guided_workflow(
            engine.root, kind=name.removeprefix("holocore_"), question=args["question"], sector=args.get("sector")
        )
    if name == "holocore_status": return engine.status()
    if name.startswith("holocore_models_"):
        from .local_models import LocalModelManager
        config = engine.router.config
        manager = LocalModelManager(config.home or HomeManager().home)
        if name == "holocore_models_discover": return manager.discover(args.get("base_url"))
        if name == "holocore_models_status": return manager.status(config.llm)
        if name == "holocore_models_configure": return manager.configure(args["provider"], args["model"], base_url=args.get("base_url"))
        return manager.clear()
    if name == "holocore_archive_search": return engine.router.archive.search(args["query"])
    if name == "holocore_archive_read": return engine.router.archive.read(args["path"])
    if name == "holocore_archive_create": return engine.router.archive.create(args["path"], f"# {args['title']}\n\n## For future Claude\n{args['content']}")
    if name == "holocore_code_map_refresh": return engine.refresh()
    if name == "holocore_code_map_search": return engine.router.atlas.search(args["query"])
    if name == "holocore_code_map_explain": return engine.router.atlas.explain(args["query"])
    if name == "holocore_code_map_path": return engine.router.atlas.shortest_path(args["source"], args["target"], max_depth=args.get("max_depth"))
    if name == "holocore_code_map_affected": return engine.router.atlas.affected(args["query"], depth=args.get("depth", 2))
    if name == "holocore_code_map_neighborhood": return engine.router.atlas.neighborhood(args["query"], depth=args.get("depth", 1))
    if name == "holocore_code_map_audit": return engine.router.atlas.audit()
    if name == "holocore_code_map_groups": return engine.router.atlas.constellations(min_size=args.get("min_size", 2))
    if name == "holocore_code_map_html":
        return engine.ensure_atlas()
    if name == "holocore_code_map_export":
        from .atlas_exports import export_atlas
        return export_atlas(engine.router.atlas, args["output"], args.get("formats"))
    if name == "holocore_remember": return engine.remember(args["content"], args.get("sector", "general"), args.get("source", ""))
    if name == "holocore_recall":
        engine.router.ensure_memory_scope()
        return [r.__dict__ for r in engine.router.memories.search(args["query"], world=engine.router.world_id, sector=args.get("sector"))]
    if name == "holocore_ingest_chat": return engine.ingest_chat(args["messages"], sector=args.get("sector", "conversations"), source="mcp", custom_instructions=args.get("instructions", ""))
    if name == "holocore_home":
        manager = HomeManager(); return {"home": str(manager.home), "archive": str(manager.archive), "worlds_file": str(manager.worlds_path)}
    if name == "holocore_worlds": return HomeManager().list_worlds()
    if name == "holocore_global_graph":
        from .global_graph import build_global_graph
        manager = HomeManager(); output = args.get("output") or str(manager.home / "global-graph" / "graph.json")
        return build_global_graph(manager.home, output=output)
    if name == "holocore_setup": return engine.setup(home=Path(args["home"]).expanduser())
    if name == "holocore_sync_all": return sync_all(regenerate=bool(args.get("regenerate", False)))
    if name in {"holocore_activity_affected", "holocore_activity_handoff"}:
        from .activity import ActivityLedger
        from .config import Config
        config = Config.load(root=engine.root); project_id = config.world_id or engine.root.name
        ledger = ActivityLedger(config.memory_path)
        if name == "holocore_activity_affected":
            return ledger.affected(args["query"], project_id=project_id, limit=int(args.get("limit", 100)))
        return ledger.handoff(project_id=project_id, run_id=args.get("run_id"), action_limit=int(args.get("action_limit", 50)), item_limit=int(args.get("item_limit", 100)))
    if name == "holocore_connections":
        from .install import inspect_client_connections
        return inspect_client_connections()
    if name.startswith("holocore_handoffs_"):
        from .activity import ActivityLedger
        from .config import Config
        config = Config.load(root=engine.root); project_id = config.world_id or engine.root.name
        ledger = ActivityLedger(config.memory_path)
        if name == "holocore_handoffs_list":
            return ledger.handoffs(project_id=project_id, status=args.get("status"), limit=int(args.get("limit", 100)))
        if name == "holocore_handoffs_create":
            return ledger.create_handoff(project_id=project_id, run_id=args["run_id"], title=args.get("title", ""), note=args.get("note", ""))
        if name == "holocore_handoffs_claim":
            return ledger.claim_handoff(args["handoff_id"], project_id=project_id, agent=args["agent"])
        return ledger.close_handoff(
            args["handoff_id"], project_id=project_id,
            outcome=args["outcome"], agent=args.get("agent", ""), reason=args.get("reason", ""),
        )
    if name == "holocore_ingest": return engine.ingest_source(args["source"], title=args.get("title"))
    if name == "holocore_inbox_sync": return engine.sync_inbox()
    if name.startswith("holocore_memory_"):
        root = engine.root; config = engine.router.config; memories = MemoryStore(config.memory_path); world = config.world_id or root.name
        memories.create_world(world, root.name, {"root": str(root)})
        sector = args.get("sector")
        if sector: memories.create_sector(world, sector)
        if name == "holocore_memory_collect":
            return MemoryCollector(memories).collect(CollectionOptions(mode=args["mode"], world=world, sector=sector or "project", root=Path(args.get("path") or root).resolve()))
        if name == "holocore_memory_checkpoint": return memories.get_checkpoint(world=world, sector=sector, mode=args["mode"], source_ref=args["source"])
        if name == "holocore_memory_event": return memories.record_diary(args["content"], world=world, sector=sector or "project", title=args.get("title", ""), source_ref="mcp")
        if name == "holocore_memory_timeline": return memories.timeline(world=world, sector=sector, limit=int(args.get("limit", 100)))
        if name == "holocore_memory_consolidate": return memories.consolidate(world=world, sector=sector)
        if name == "holocore_memory_export": return memories.timeline(world=world, sector=sector, limit=int(args.get("limit", 1000)))
        if name == "holocore_memory_groups": return [deck.__dict__ for deck in memories.decks(world=world)]
        if name == "holocore_memory_track": return memories.record_signal_chronicle(args["name"], args["relation"], args["value"], world=world, kind=args.get("kind", "concept"), room=args.get("group"), source_ref="mcp").__dict__
        if name == "holocore_memory_history": return [event.__dict__ for event in memories.signal_chronicle(world=world, entity=args.get("item"), room=args.get("group"), limit=int(args.get("limit", 100))) ]
    raise ValueError(f"Unknown HoloCore tool: {name}")


def _engine_for(args: dict, fallback: HoloCoreEngine) -> HoloCoreEngine:
    requested = str(args.get("root") or "").strip()
    if requested:
        return HoloCoreEngine(Path(requested).expanduser().resolve())
    world = str(args.get("world") or "").strip().casefold()
    worlds = HomeManager().list_worlds().get("worlds", [])
    if world:
        match = next((item for item in worlds if world in {
            str(item.get("id", "")).casefold(),
            str(item.get("name", "")).casefold(),
            str(item.get("root", "")).casefold(),
        }), None)
        if match:
            return HoloCoreEngine(Path(str(match["root"])))
    if len(worlds) == 1:
        return HoloCoreEngine(Path(str(worlds[0]["root"])))
    return fallback


def main() -> int:
    engine = HoloCoreEngine(Path.cwd())
    for line in sys.stdin:
        request = {}
        try:
            request = json.loads(line); method = request.get("method"); params = request.get("params", {})
            if method == "initialize": result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}, "prompts": {}}, "serverInfo": {"name": "holocore", "version": __version__}}
            elif method == "tools/list": result = {"tools": TOOLS}
            elif method == "tools/call":
                arguments = params.get("arguments", {})
                result = {"content": [{"type": "text", "text": json.dumps(call(_engine_for(arguments, engine), params["name"], arguments), ensure_ascii=False, default=str)}]}
            elif method == "prompts/list": result = {"prompts": PROMPTS}
            elif method == "prompts/get": result = get_prompt(params["name"], params.get("arguments", {}))
            else: result = {}
            # JSON-RPC notifications (including MCP's notifications/initialized)
            # must not receive a response.  Responding with id=null produces an
            # invalid MCP message for clients such as Claude Desktop/Cowork.
            if "id" not in request or request["id"] is None:
                continue
            response = {"jsonrpc": "2.0", "id": request.get("id"), "result": result}
        except Exception as exc:
            if "id" not in request or request["id"] is None:
                continue
            response = {"jsonrpc": "2.0", "id": request.get("id"), "error": {"code": -32000, "message": str(exc)}}
        print(json.dumps(response), flush=True)
    return 0


if __name__ == "__main__": raise SystemExit(main())
