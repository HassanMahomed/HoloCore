"""Cross-project structural graph aggregation.

Only Code Map structure is merged. Knowledge notes and Memories records remain
owned by their project and are never copied into this graph.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from .config import Config


def build_global_graph(home: str | Path, *, output: str | Path | None = None) -> dict[str, Any]:
    manager_root = Path(home).expanduser().resolve()
    registry_path = manager_root / "worlds.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8")) if registry_path.is_file() else {"worlds": []}
    nodes: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    worlds: list[dict[str, Any]] = []
    for world in registry.get("worlds", []):
        if not isinstance(world, dict) or not isinstance(world.get("root"), str):
            continue
        world_id = str(world.get("id") or Path(world["root"]).name)
        graph_path = Config.load(root=Path(world["root"])).atlas_graph_path
        if not graph_path.is_file():
            graph_path = Path(world["root"]) / "holocore-out" / "graph.json"
        if not graph_path.is_file():
            graph_path = Path(world["root"]) / "graphify-out" / "graph.json"
        if not graph_path.is_file():
            continue
        try:
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        world_record = {"id": world_id, "name": str(world.get("name") or world_id), "root": world["root"], "nodes": 0, "links": 0}
        worlds.append(world_record)
        world_node_id = f"world:{world_id}"
        nodes.append({"id": world_node_id, "label": world_record["name"], "kind": "world", "world": world_id, "source": "atlas"})
        id_map: dict[str, str] = {}
        for node in graph.get("nodes", []):
            if not isinstance(node, dict) or "id" not in node:
                continue
            local_id = str(node["id"])
            global_id = f"{world_id}:{local_id}"
            id_map[local_id] = global_id
            nodes.append({**node, "id": global_id, "world": world_id, "local_id": local_id})
            world_record["nodes"] += 1
        for edge in graph.get("links", graph.get("edges", [])):
            if not isinstance(edge, dict):
                continue
            source, target = str(edge.get("source", edge.get("from", ""))), str(edge.get("target", edge.get("to", "")))
            if source not in id_map or target not in id_map:
                continue
            links.append({**edge, "source": id_map[source], "target": id_map[target], "world": world_id})
            world_record["links"] += 1
        links.append({"source": world_node_id, "target": f"{world_id}:{next(iter(id_map))}"} if id_map else {"source": world_node_id, "target": world_node_id, "self": True})
    result = {
        "directed": True,
        "multigraph": False,
        "graph": {"generator": "holocore-global-code-map", "home": str(manager_root), "worlds": worlds, "source": "Code Map only; Knowledge and Memories remain project-owned"},
        "nodes": nodes,
        "links": [edge for edge in links if not edge.get("self")],
    }
    if output is not None:
        destination = Path(output).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        result["path"] = str(destination)
    return result


__all__ = ["build_global_graph"]
