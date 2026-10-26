"""Standard-library, HoloCore-native structural Atlas."""

from __future__ import annotations

import ast
from collections import deque
from datetime import datetime, timezone
import hashlib
import json
import os
import shutil
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
import tokenize
from typing import Any, Iterable, Iterator, Mapping
import unicodedata


ATLAS_VERSION = "2"
DEFAULT_AFFECTED_RELATIONS = frozenset(
    {"calls", "imports", "imports_from", "inherits", "references", "uses"}
)
_EXCLUDES = frozenset(
    {
        ".agents", ".cache", ".claude", ".codex", ".cursor", ".gemini", ".git", ".hg",
        ".holocore", ".mypy_cache", ".next", ".obsidian", ".opencode", ".pytest_cache", ".ruff_cache",
        ".svn", ".tox", ".turbo", ".venv", ".vercel", "__pycache__", "build", "coverage",
        "dist", "graphify", "graphify-out", "holocore-out", "Imported", "imported",
        "node_modules", "out", "raw", "venv",
    }
)


def content_hash(content: bytes | str | os.PathLike[str]) -> str:
    """Return location-independent SHA-256 for *content*."""
    if isinstance(content, os.PathLike):
        content = Path(content).read_bytes()
    elif isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _key(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"[^\w./:-]+", "_", value, flags=re.UNICODE)
    return re.sub(r"_+", "_", value).strip("_")


def _file_id(relative: str) -> str:
    return f"file:{_key(relative)}"


def _symbol_id(relative: str, qualified: str) -> str:
    return f"symbol:{_key(relative)}:{_key(qualified)}"


def _module(relative: str) -> str:
    parts = list(PurePosixPath(relative).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _edge(source: str, target: str, relation: str, owner: str, line: int = 1) -> dict[str, Any]:
    return {
        "source": source, "target": target, "relation": relation,
        "source_file": owner, "source_location": f"L{line}",
        "confidence": "EXTRACTED",
    }


def _dotted(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


class _PyVisitor(ast.NodeVisitor):
    def __init__(self, relative: str, digest: str, module: str, aliases: Mapping[str, str]):
        self.relative, self.digest, self.module = relative, digest, module
        self.aliases = aliases
        self.file_id = _file_id(relative)
        self.scopes: list[tuple[str, str, str]] = []
        self.nodes: list[dict[str, Any]] = []
        self.edges: list[dict[str, Any]] = []
        self.references: list[dict[str, Any]] = []

    def _owner(self) -> str:
        return self.scopes[-1][1] if self.scopes else self.file_id

    def _qualname(self, name: str) -> str:
        return ".".join([scope[0] for scope in self.scopes] + [name])

    def _symbol(self, node: ast.AST, name: str, kind: str) -> tuple[str, str]:
        qualname = self._qualname(name)
        node_id = _symbol_id(self.relative, qualname)
        self.nodes.append({
            "id": node_id,
            "label": name if kind == "class" else f"{name}()",
            "kind": kind, "type": kind, "file_type": "code", "language": "python",
            "qualified_name": f"{self.module}.{qualname}".strip("."),
            "source_file": self.relative,
            "source_location": f"L{getattr(node, 'lineno', 1)}",
            "content_hash": self.digest, "confidence": "EXTRACTED",
        })
        relation = "method" if self.scopes and self.scopes[-1][2] == "class" and kind == "function" else "contains"
        self.edges.append(_edge(self._owner(), node_id, relation, self.relative, getattr(node, "lineno", 1)))
        return qualname, node_id

    def _target(self, node: ast.AST) -> str | None:
        dotted = _dotted(node)
        if not dotted:
            return None
        parts = dotted.split(".")
        if parts[0] in {"self", "cls"}:
            classes = [scope[0] for scope in self.scopes if scope[2] == "class"]
            return ".".join([self.module, *classes, *parts[1:]]).strip(".") if classes else None
        if parts[0] in self.aliases:
            return ".".join([self.aliases[parts[0]], *parts[1:]])
        if len(parts) == 1:
            # Prefer the nearest lexical scope, then resolution falls back to module/global.
            parents = [scope[0] for scope in self.scopes[:-1]]
            return ".".join([self.module, *parents, parts[0]]).strip(".")
        return dotted

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        _, node_id = self._symbol(node, node.name, "class")
        for base in node.bases:
            target = self._target(base)
            if target:
                self.references.append({"source": node_id, "target": target, "relation": "inherits", "line": node.lineno})
        self.scopes.append((node.name, node_id, "class"))
        self.generic_visit(node)
        self.scopes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function(node)

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        _, node_id = self._symbol(node, node.name, "function")
        self.scopes.append((node.name, node_id, "function"))
        self.generic_visit(node)
        self.scopes.pop()

    def visit_Call(self, node: ast.Call) -> None:
        target = self._target(node.func)
        if target:
            self.references.append({"source": self._owner(), "target": target, "relation": "calls", "line": node.lineno})
        self.generic_visit(node)


ATLAS_OUT_DIR = "holocore-out"
LEGACY_ATLAS_OUT_DIR = "graphify-out"


class Atlas:
    """Incremental structural graph and in-process query facade."""

    def __init__(
        self,
        root: str | os.PathLike[str],
        output: str | os.PathLike[str] | None = None,
        *,
        exclude: Iterable[str] = (),
        runtime_output: str | os.PathLike[str] | None = None,
        knowledge_roots: Mapping[str, str | os.PathLike[str]] | None = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        if output is None:
            legacy = self.root / LEGACY_ATLAS_OUT_DIR
            current = self.root / ATLAS_OUT_DIR
            if legacy.is_dir() and not current.exists():
                shutil.move(str(legacy), str(current))
            destination = Path(ATLAS_OUT_DIR) / "graph.json"
        else:
            destination = Path(output)
        self.graph_path = destination if destination.is_absolute() else self.root / destination
        # Native runtime compatibility path; graph_path remains the public
        # node-link output contract used by structural tooling.
        self.output = Path(runtime_output).expanduser().resolve() if runtime_output else self.root / ".holocore" / "atlas.json"
        self.knowledge_roots = {
            _key(name): Path(path).expanduser().resolve()
            for name, path in (knowledge_roots or {}).items()
            if Path(path).expanduser().is_dir()
        }
        self.exclude = _EXCLUDES | frozenset(exclude)
        self._graph: dict[str, Any] | None = None

    def refresh(self) -> dict[str, Any]:
        previous = self._read(missing_ok=True)
        old_files = previous.get("graph", {}).get("files", {}) if previous else {}
        current = self._snapshot()
        records: dict[str, dict[str, Any]] = {}
        reused = extracted = 0
        for relative, info in current.items():
            old = old_files.get(relative)
            if (
                isinstance(old, dict)
                and old.get("content_hash") == info["content_hash"]
                and old.get("extractor_version") == ATLAS_VERSION
                and isinstance(old.get("fragment"), dict)
            ):
                records[relative] = {**old, **info}
                reused += 1
            else:
                records[relative] = {**info, **self._extract(relative, info["content_hash"])}
                extracted += 1

        nodes, links = self._assemble(records)
        deleted = sorted(set(old_files) - set(current))
        generated = _now()
        digest = self._source_digest(current)
        files = {
            relative: {
                "content_hash": record["content_hash"], "size": record["size"],
                "mtime_ns": record["mtime_ns"], "language": record.get("language", "generic"),
                "module": record.get("module", ""), "extractor_version": ATLAS_VERSION,
                "fragment": record["fragment"],
            }
            for relative, record in sorted(records.items())
        }
        stats = {
            "files": len(files), "nodes": len(nodes), "links": len(links),
            "edges": len(links), "extracted": extracted, "reused": reused,
            "deleted": len(deleted),
        }
        graph = {
            "directed": True, "multigraph": False,
            "graph": {
                "schema_version": 1, "atlas_version": ATLAS_VERSION,
                "generator": "holocore-atlas",
                "source_root": str(self.root), "generated_at": generated,
                "hash_algorithm": "sha256", "source_digest": digest, "files": files,
                "freshness": {"state": "fresh", "checked_at": generated, "source_digest": digest},
                "extracted_files": extracted, "reused_files": reused,
                "deleted_files": len(deleted),
                "stats": stats,
            },
            "nodes": nodes, "links": links,
        }
        self._write(graph)
        if self.output != self.graph_path:
            self._write(graph, destination=self.output)
        self._graph = graph
        return graph

    update = refresh
    build = refresh

    def load_graph(self) -> dict[str, Any]:
        if self._graph is None:
            self._graph = self._read()
        return self._graph

    def _load(self) -> dict[str, Any]:  # compatibility
        if not self.graph_path.exists():
            self.refresh()
        return self.load_graph()

    def freshness(self) -> dict[str, Any]:
        checked = _now()
        if not self.graph_path.is_file():
            return {"state": "missing", "fresh": False, "checked_at": checked, "changed": [], "deleted": []}
        graph = self._read()
        saved = graph.get("graph", {}).get("files", {})
        current = self._snapshot()
        changed = sorted(path for path, info in current.items() if path not in saved or saved[path].get("content_hash") != info["content_hash"])
        deleted = sorted(set(saved) - set(current))
        state = "fresh" if not changed and not deleted else "stale"
        return {
            "state": state, "fresh": state == "fresh", "checked_at": checked,
            "generated_at": graph.get("graph", {}).get("generated_at"),
            "source_digest": self._source_digest(current),
            "saved_source_digest": graph.get("graph", {}).get("source_digest"),
            "changed": changed, "deleted": deleted, "files": len(current),
        }

    status = freshness

    def is_current(self) -> bool:
        return bool(self.freshness()["fresh"])

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        terms = [_key(term) for term in re.findall(r"[\w./:-]+", query, flags=re.UNICODE)]
        if not terms or limit <= 0:
            return []
        found: list[tuple[int, str, dict[str, Any]]] = []
        for node in self.load_graph().get("nodes", []):
            label = _key(str(node.get("label", "")))
            qualified = _key(str(node.get("qualified_name", "")))
            source = _key(str(node.get("source_file", "")))
            node_id = _key(str(node.get("id", "")))
            score = sum(
                (8 if term == label else 0) + (5 if term in label else 0)
                + (3 if term in qualified else 0) + (2 if term in source else 0)
                + (1 if term in node_id else 0)
                for term in terms
            )
            if score:
                found.append((score, str(node["id"]), node))
        found.sort(key=lambda item: (-item[0], item[1]))
        return [dict(node, score=score) for score, _, node in found[:limit]]

    def resolve(self, query: str) -> str | None:
        nodes = self.load_graph().get("nodes", [])
        if any(str(node.get("id")) == query for node in nodes):
            return query
        normalized = unicodedata.normalize("NFKC", query).casefold().rstrip("/\\")
        labels = [node for node in nodes if str(node.get("label", "")).casefold().rstrip("()") == normalized.rstrip("()")]
        if len(labels) == 1:
            return str(labels[0]["id"])
        sources = [node for node in nodes if str(node.get("source_file", "")).casefold() == normalized and node.get("kind") == "file"]
        if len(sources) == 1:
            return str(sources[0]["id"])
        qualified = [node for node in nodes if str(node.get("qualified_name", "")).casefold() == normalized]
        if len(qualified) == 1:
            return str(qualified[0]["id"])
        partial = [node for node in nodes if normalized in str(node.get("label", "")).casefold()]
        return str(partial[0]["id"]) if len(partial) == 1 else None

    def shortest_path(self, source: str, target: str, *, max_depth: int | None = None) -> dict[str, Any] | None:
        source_id, target_id = self.resolve(source), self.resolve(target)
        if source_id is None or target_id is None:
            return None
        graph = self.load_graph()
        outgoing: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        for link in graph.get("links", graph.get("edges", [])):
            outgoing.setdefault(str(link["source"]), []).append((str(link["target"]), link))
        queue: deque[str] = deque([source_id])
        previous: dict[str, tuple[str, dict[str, Any]] | None] = {source_id: None}
        depths = {source_id: 0}
        while queue:
            current = queue.popleft()
            if current == target_id:
                break
            if max_depth is not None and depths[current] >= max_depth:
                continue
            for neighbor, link in sorted(outgoing.get(current, []), key=lambda pair: pair[0]):
                if neighbor not in previous:
                    previous[neighbor] = (current, link)
                    depths[neighbor] = depths[current] + 1
                    queue.append(neighbor)
        if target_id not in previous:
            return None
        ids, path_links, cursor = [target_id], [], target_id
        while previous[cursor] is not None:
            parent, link = previous[cursor]  # type: ignore[misc]
            ids.append(parent)
            path_links.append(dict(link))
            cursor = parent
        ids.reverse(); path_links.reverse()
        by_id = {str(node["id"]): node for node in graph.get("nodes", [])}
        return {
            "source": source_id, "target": target_id, "depth": len(path_links),
            "nodes": [dict(by_id[node_id]) for node_id in ids], "links": path_links,
        }

    query_path = shortest_path

    def path(self, source: str, target: str, *, max_depth: int | None = None) -> list[dict[str, Any]]:
        """Return the ordered nodes on a directed shortest path."""
        result = self.shortest_path(source, target, max_depth=max_depth)
        return [] if result is None else result["nodes"]

    def affected(
        self,
        query: str,
        *,
        depth: int = 2,
        relations: Iterable[str] = DEFAULT_AFFECTED_RELATIONS,
    ) -> list[dict[str, Any]]:
        seed = self.resolve(query)
        if seed is None or depth < 1:
            return []
        graph = self.load_graph()
        incoming: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        outgoing: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        for link in graph.get("links", graph.get("edges", [])):
            source, target = str(link["source"]), str(link["target"])
            incoming.setdefault(target, []).append((source, link))
            outgoing.setdefault(source, []).append((target, link))
        allowed, seen = set(relations), {seed}
        queue: deque[tuple[str, int]] = deque([(seed, 0)])
        # Class/member seeding keeps callers bound to methods visible.
        for member, link in outgoing.get(seed, []):
            if link.get("relation") in {"method", "contains"} and member not in seen:
                seen.add(member); queue.append((member, 0))
        hits: list[tuple[str, int, str]] = []
        while queue:
            current, current_depth = queue.popleft()
            if current_depth >= depth:
                continue
            for source, link in incoming.get(current, []):
                relation = str(link.get("relation", ""))
                if relation in allowed and source not in seen:
                    seen.add(source); hits.append((source, current_depth + 1, relation))
                    queue.append((source, current_depth + 1))
        by_id = {str(node["id"]): node for node in graph.get("nodes", [])}
        relation_rank = {"calls": 0, "inherits": 1, "references": 2, "uses": 3, "imports_from": 4, "imports": 5}
        hits.sort(key=lambda hit: (hit[1], relation_rank.get(hit[2], 99), hit[0]))
        return [dict(by_id[node_id], depth=level, via_relation=relation) for node_id, level, relation in hits]

    affected_nodes = affected

    def neighborhood(self, query: str, *, depth: int = 1, relations: Iterable[str] | None = None) -> dict[str, Any]:
        """Return a deterministic local subgraph around a Signal."""
        seed = self.resolve(query)
        if seed is None or depth < 0:
            return {"seed": None, "depth": depth, "nodes": [], "links": []}
        graph = self.load_graph(); links = [dict(link) for link in graph.get("links", graph.get("edges", []))]
        allowed = set(relations) if relations is not None else None; adjacency: dict[str, set[str]] = {}
        for link in links:
            if allowed is not None and link.get("relation") not in allowed: continue
            a, b = str(link["source"]), str(link["target"]); adjacency.setdefault(a, set()).add(b); adjacency.setdefault(b, set()).add(a)
        distances = {seed: 0}; queue: deque[str] = deque([seed])
        while queue:
            current = queue.popleft()
            if distances[current] >= depth: continue
            for neighbor in sorted(adjacency.get(current, ())):
                if neighbor not in distances: distances[neighbor] = distances[current] + 1; queue.append(neighbor)
        ids = set(distances); by_id = {str(node["id"]): node for node in graph.get("nodes", [])}
        return {"seed": seed, "depth": depth, "nodes": [dict(by_id[node_id], distance=distances[node_id]) for node_id in sorted(ids) if node_id in by_id], "links": [link for link in links if str(link["source"]) in ids and str(link["target"]) in ids and (allowed is None or link.get("relation") in allowed)]}

    def explain(self, query: str) -> dict[str, Any]:
        """Explain resolution and structural evidence for a Signal query."""
        graph = self.load_graph(); matches = self.search(query, limit=20); resolved = self.resolve(query)
        node = next((dict(item) for item in graph.get("nodes", []) if str(item.get("id")) == resolved), None)
        links = [dict(link) for link in graph.get("links", graph.get("edges", [])) if resolved and (str(link.get("source")) == resolved or str(link.get("target")) == resolved)]
        return {"query": query, "resolved": resolved, "match_count": len(matches), "matches": matches, "signal": node, "relationships": sorted(links, key=lambda item: (str(item.get("relation", "")), str(item.get("source", "")), str(item.get("target", ""))))}

    def constellations(self, *, min_size: int = 2) -> list[dict[str, Any]]:
        """Return deterministic weakly-connected Signal communities."""
        graph = self.load_graph()
        by_id = {str(node["id"]): node for node in graph.get("nodes", [])}
        neighbors: dict[str, set[str]] = {node_id: set() for node_id in by_id}
        for link in graph.get("links", graph.get("edges", [])):
            source, target = str(link.get("source")), str(link.get("target"))
            if source in by_id and target in by_id:
                neighbors[source].add(target); neighbors[target].add(source)
        seen: set[str] = set(); communities: list[list[str]] = []
        for seed in sorted(by_id):
            if seed in seen:
                continue
            queue, component = deque([seed]), []
            seen.add(seed)
            while queue:
                current = queue.popleft(); component.append(current)
                for neighbor in sorted(neighbors[current]):
                    if neighbor not in seen:
                        seen.add(neighbor); queue.append(neighbor)
            if len(component) >= max(1, int(min_size)):
                communities.append(sorted(component))
        communities.sort(key=lambda members: (-len(members), members[0]))
        result = []
        for members in communities:
            digest = hashlib.sha256("\n".join(members).encode("utf-8")).hexdigest()[:16]
            result.append({"id": f"constellation:{digest}", "label": str(by_id[members[0]].get("label", members[0])), "size": len(members), "signal_ids": members, "relations": sorted({str(link.get("relation", "")) for link in graph.get("links", []) if str(link.get("source")) in members and str(link.get("target")) in members}), "signals": [dict(by_id[node_id]) for node_id in members]})
        return result

    clusters = constellations

    def audit(self) -> dict[str, Any]:
        """Return deterministic quality findings without changing the graph."""
        graph = self.load_graph(); nodes = graph.get("nodes", []); links = graph.get("links", graph.get("edges", [])); ids = {str(node.get("id")) for node in nodes}; degree = {node_id: 0 for node_id in ids}; counts: dict[tuple[str, str, str], int] = {}; unresolved = []
        for link in links:
            source, target, relation = str(link.get("source")), str(link.get("target")), str(link.get("relation", "")); counts[(source, target, relation)] = counts.get((source, target, relation), 0) + 1
            if source not in ids or target not in ids: unresolved.append(dict(link))
            else: degree[source] += 1; degree[target] += 1
        duplicates = [{"source": a, "target": b, "relation": r, "count": n} for (a, b, r), n in sorted(counts.items()) if n > 1]
        confidence = {value: sum(1 for node in nodes if node.get("confidence") == value) for value in ("EXTRACTED", "INFERRED", "AMBIGUOUS")}
        return {"schema_version": 1, "source_digest": graph.get("graph", {}).get("source_digest", ""), "orphan_signals": [str(node["id"]) for node in nodes if degree.get(str(node["id"]), 0) == 0], "unresolved_relationships": unresolved, "duplicate_edges": duplicates, "confidence": confidence, "source_coverage": {"files": graph.get("graph", {}).get("stats", {}).get("files", 0), "signals": len(nodes), "relationships": len(links)}, "ok": not unresolved and not duplicates}

    def write_report(self, output: str | os.PathLike[str] | None = None) -> Path:
        """Write a concise, auditable Graphify-style Markdown report."""
        graph = self.load_graph()
        metadata = graph.get("graph", {})
        stats = metadata.get("stats", {})
        constellations = self.constellations()
        target = Path(output) if output is not None else self.graph_path.parent / "GRAPH_REPORT.md"
        lines = [
            "# HoloCore Code Map Report",
            "",
            f"- Generated: {metadata.get('generated_at', 'unknown')}",
            f"- Source root: `{metadata.get('source_root', self.root)}`",
            f"- Files: {stats.get('files', 0)}",
            f"- Items: {stats.get('nodes', len(graph.get('nodes', [])))}",
            f"- Relationships: {stats.get('links', len(graph.get('links', [])))}",
            f"- Clusters: {len(constellations)}",
            f"- Source digest: `{metadata.get('source_digest', '')}`",
            "",
            "## Clusters",
            "",
        ]
        for item in constellations[:50]:
            lines.append(f"- **{item['label']}** — {item['size']} items")
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = "\n".join(lines) + "\n"
        if not target.exists() or target.read_text(encoding="utf-8") != payload:
            target.write_text(payload, encoding="utf-8")
        return target

    def _snapshot(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        if not self.root.is_dir():
            return result
        output = self.graph_path.resolve()
        for path in self._files():
            try:
                if path.resolve() == output:
                    continue
                raw, stat = path.read_bytes(), path.stat()
                relative = path.relative_to(self.root).as_posix()
            except (OSError, ValueError):
                continue
            result[relative] = {"content_hash": content_hash(raw), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        for name, knowledge_root in self.knowledge_roots.items():
            for folder in (knowledge_root / "wiki", knowledge_root / "system"):
                if not folder.is_dir():
                    continue
                for path in sorted(folder.rglob("*.md")):
                    try:
                        raw, stat = path.read_bytes(), path.stat()
                        relative = f".knowledge/{name}/{path.relative_to(knowledge_root).as_posix()}"
                    except (OSError, ValueError):
                        continue
                    result[relative] = {
                        "content_hash": content_hash(raw),
                        "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                    }
        return dict(sorted(result.items()))

    def _files(self) -> Iterator[Path]:
        # In a Git World, use Git's own ignore engine so reference sources,
        # generated output, local AI-client configuration, and other ignored
        # material do not flood the user-facing Atlas. One Git call is both
        # faster and more accurate than walking every ignored directory.
        try:
            listed = subprocess.run(
                [
                    "git", "-C", str(self.root), "ls-files", "--cached",
                    "--others", "--exclude-standard", "-z",
                ],
                check=False,
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            listed = None
        if listed is not None and listed.returncode == 0:
            for raw in sorted(filter(None, listed.stdout.split(b"\0"))):
                try:
                    relative = raw.decode("utf-8", errors="surrogateescape")
                    path = self.root / PurePosixPath(relative)
                except (UnicodeError, ValueError):
                    continue
                if any(part in self.exclude for part in PurePosixPath(relative).parts):
                    continue
                if path.is_file():
                    yield path
            return
        for directory, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(name for name in dirnames if name not in self.exclude)
            base = Path(directory)
            for name in sorted(filenames):
                path = base / name
                if name not in self.exclude and path.is_file():
                    yield path

    def _source_path(self, relative: str) -> Path:
        parts = PurePosixPath(relative).parts
        if len(parts) >= 3 and parts[0] == ".knowledge" and parts[1] in self.knowledge_roots:
            return self.knowledge_roots[parts[1]] / PurePosixPath(*parts[2:])
        return self.root / PurePosixPath(relative)

    def _extract(self, relative: str, digest: str) -> dict[str, Any]:
        path = self._source_path(relative)
        python = path.suffix.casefold() == ".py"
        language = {".js": "javascript", ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript", ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".sql": "sql", ".md": "markdown", ".markdown": "markdown"}.get(path.suffix.casefold(), "generic")
        file_node = {
            "id": _file_id(relative), "label": PurePosixPath(relative).name,
            "kind": "file", "type": "file", "file_type": "code" if python else "generic",
            "language": "python" if python else language, "source_file": relative,
            "source_location": "L1", "content_hash": digest, "confidence": "EXTRACTED",
        }
        if python:
            return self._extract_python(path, relative, digest, file_node)
        return self._extract_generic(path, relative, digest, file_node)

    def _extract_python(self, path: Path, relative: str, digest: str, file_node: dict[str, Any]) -> dict[str, Any]:
        module = _module(relative)
        fragment: dict[str, Any] = {"nodes": [file_node], "edges": [], "references": []}
        try:
            with tokenize.open(path) as handle:
                tree = ast.parse(handle.read(), filename=relative)
        except (OSError, SyntaxError, UnicodeError) as exc:
            file_node["parse_error"] = f"{type(exc).__name__}: {exc}"
            return {"language": "python", "module": module, "fragment": fragment}
        aliases: dict[str, str] = {}
        imports: list[dict[str, Any]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    aliases[alias.asname or alias.name.split(".")[0]] = alias.name
                    imports.append({"source": file_node["id"], "target": alias.name, "relation": "imports", "line": node.lineno})
            elif isinstance(node, ast.ImportFrom) and node.module:
                base = "." * node.level + node.module
                for alias in node.names:
                    target = base if alias.name == "*" else f"{base}.{alias.name}"
                    if alias.name != "*":
                        aliases[alias.asname or alias.name] = target
                    imports.append({"source": file_node["id"], "target": target, "relation": "imports_from", "line": node.lineno})
        visitor = _PyVisitor(relative, digest, module, aliases)
        visitor.visit(tree)
        fragment["nodes"].extend(visitor.nodes)
        fragment["edges"].extend(visitor.edges)
        fragment["references"].extend(imports + visitor.references)
        return {"language": "python", "module": module, "fragment": fragment}

    def _extract_generic(self, path: Path, relative: str, digest: str, file_node: dict[str, Any]) -> dict[str, Any]:
        fragment: dict[str, Any] = {"nodes": [file_node], "edges": [], "references": []}
        try:
            raw = path.read_bytes()
        except OSError:
            return {"language": str(file_node.get("language", "generic")), "module": "", "fragment": fragment}
        if b"\0" in raw[:8192]:
            file_node["binary"] = True
            file_node["kind"] = file_node["type"] = "media"
            file_node["file_type"] = "media"
            # Graphify-style media coverage: a neighbouring Markdown sidecar
            # becomes explicit evidence for the media node.
            sidecar = path.with_suffix(".md")
            if sidecar.is_file():
                sidecar_rel = sidecar.relative_to(self.root).as_posix() if sidecar.is_relative_to(self.root) else ""
                if sidecar_rel:
                    fragment["references"].append({"source": file_node["id"], "target": sidecar_rel, "relation": "described_by", "line": 1})
            return {"language": str(file_node.get("language", "generic")), "module": "", "fragment": fragment}
        text = raw.decode("utf-8", errors="replace")
        language = str(file_node.get("language", "generic"))
        # Lightweight language-aware extraction keeps Atlas useful without
        # requiring a parser runtime for every ecosystem.
        if path.suffix.casefold() in {".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs", ".cs", ".cpp", ".c"}:
            patterns = [
                (r"\b(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", "function"),
                (r"\bclass\s+([A-Za-z_$][\w$]*)", "class"),
                (r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=", "variable"),
                (r"\b(?:def|fn|func)\s+([A-Za-z_$][\w$]*)", "function"),
            ]
            for pattern, kind in patterns:
                for match in re.finditer(pattern, text):
                    line = text.count("\n", 0, match.start()) + 1
                    name = match.group(1)
                    node_id = f"symbol:{_key(relative)}:{_key(name)}:{line}"
                    fragment["nodes"].append({"id": node_id, "label": name, "qualified_name": name, "kind": kind, "type": kind, "file_type": "code", "language": language, "source_file": relative, "source_location": f"L{line}", "content_hash": digest, "confidence": "EXTRACTED"})
                    fragment["edges"].append(_edge(file_node["id"], node_id, "contains", relative, line))
            for match in re.finditer(r"(?:import|require\s*\(|from)\s*[\"']([^\"']+)", text):
                fragment["references"].append({"source": file_node["id"], "target": match.group(1), "relation": "imports", "line": text.count("\n", 0, match.start()) + 1})
        if path.suffix.casefold() not in {".md", ".markdown"}:
            file_node["line_count"] = text.count("\n") + (1 if text else 0)
            return {"language": language, "module": "", "fragment": fragment}
        file_node["language"] = "markdown"
        title = next(
            (
                match.group(1).strip()
                for line in text.splitlines()
                if (match := re.match(r"^#\s+(.+?)\s*$", line))
            ),
            "",
        )
        if title:
            file_node["label"] = title
        elif relative.startswith(".knowledge/") and relative.endswith("/system/index.md"):
            file_node["label"] = "World Archive Index"
        is_knowledge = relative.startswith(".knowledge/")
        if is_knowledge:
            file_node["kind"] = file_node["type"] = "archive_entry"
            file_node["file_type"] = "knowledge"
            file_node["semantic"] = True
        occurrences: dict[str, int] = {}
        current_section = ""
        for line_number, line in enumerate(text.splitlines(), 1):
            heading = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
            if heading:
                label = heading.group(1).strip()
                current_section = label.casefold()
                anchor = re.sub(r"[^\w]+", "-", label.casefold()).strip("-") or "section"
                occurrences[anchor] = occurrences.get(anchor, 0) + 1
                node_id = f"section:{_key(relative)}:{_key(anchor)}:{occurrences[anchor]}"
                fragment["nodes"].append({
                    "id": node_id, "label": label, "kind": "section", "type": "section",
                    "file_type": "generic", "language": "markdown", "source_file": relative,
                    "source_location": f"L{line_number}", "content_hash": digest, "confidence": "EXTRACTED",
                })
                fragment["edges"].append(_edge(file_node["id"], node_id, "contains", relative, line_number))
            bullet = re.match(r"^\s*[-*]\s+(.+?)\s*$", line)
            if is_knowledge and bullet and current_section:
                label = re.sub(r"\s+", " ", bullet.group(1)).strip()
                if 2 <= len(label) <= 240:
                    kind = "concept" if current_section in {"entities", "summary", "concepts", "topics"} else (current_section.rstrip("s") or "concept")
                    stable = _key(label)[:96] if kind == "concept" else hashlib.sha256(label.casefold().encode("utf-8")).hexdigest()[:20]
                    semantic_id = f"{kind}:{stable}"
                    fragment["nodes"].append({
                        "id": semantic_id, "label": label, "kind": kind, "type": kind,
                        "file_type": "knowledge", "language": "semantic", "source_file": relative,
                        "source_location": f"L{line_number}", "content_hash": digest,
                        "confidence": "EXTRACTED", "semantic": True,
                    })
                    relation = "mentions" if kind == "concept" else "records"
                    fragment["edges"].append(_edge(file_node["id"], semantic_id, relation, relative, line_number))
            for link in re.finditer(r"\[[^\]]+\]\(([^)#]+)(?:#[^)]+)?\)", line):
                target = link.group(1).strip()
                if "://" not in target and not target.startswith(("mailto:", "#")):
                    fragment["references"].append({"source": file_node["id"], "target": target, "relation": "references", "line": line_number})
        return {"language": "markdown", "module": "", "fragment": fragment}

    def _assemble(self, records: Mapping[str, Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        nodes: dict[str, dict[str, Any]] = {}
        links: list[dict[str, Any]] = []
        references: list[tuple[str, Mapping[str, Any]]] = []
        module_files: dict[str, str] = {}
        symbols: dict[str, list[str]] = {}
        terminals: dict[str, list[str]] = {}
        for relative, record in records.items():
            fragment = record["fragment"]
            for node in fragment.get("nodes", []):
                nodes[str(node["id"])] = dict(node)
                qualified = str(node.get("qualified_name", ""))
                if qualified:
                    symbols.setdefault(qualified, []).append(str(node["id"]))
                    terminals.setdefault(qualified.rsplit(".", 1)[-1], []).append(str(node["id"]))
            links.extend(dict(link) for link in fragment.get("edges", []))
            references.extend((relative, reference) for reference in fragment.get("references", []))
            if record.get("module"):
                module_files[str(record["module"])] = _file_id(relative)
        for relative, reference in references:
            target = self._resolve_reference(relative, str(reference["target"]), records, module_files, symbols, terminals)
            if target and str(reference["source"]) in nodes and target in nodes:
                links.append(_edge(str(reference["source"]), target, str(reference["relation"]), relative, int(reference.get("line", 1))))
        unique: dict[tuple[str, str, str], dict[str, Any]] = {}
        for link in links:
            source = str(link["source"])
            target = str(link["target"])
            if source == target:
                continue
            key = (source, target, str(link.get("relation", "")))
            evidence = {
                "source_file": str(link.get("source_file", "")),
                "source_location": str(link.get("source_location", "")),
            }
            if key not in unique:
                unique[key] = {**link, "occurrences": [evidence], "occurrence_count": 1}
            elif evidence not in unique[key]["occurrences"]:
                unique[key]["occurrences"].append(evidence)
                unique[key]["occurrence_count"] = len(unique[key]["occurrences"])
        return [nodes[key] for key in sorted(nodes)], [unique[key] for key in sorted(unique)]

    def _resolve_reference(
        self,
        relative: str,
        target: str,
        records: Mapping[str, Mapping[str, Any]],
        module_files: Mapping[str, str],
        symbols: Mapping[str, list[str]],
        terminals: Mapping[str, list[str]],
    ) -> str | None:
        module = str(records[relative].get("module", ""))
        if target.startswith("."):
            level = len(target) - len(target.lstrip("."))
            suffix = target[level:]
            target = ".".join([*module.split(".")[:-level], suffix]).strip(".")
        candidates = [target]
        if module and not target.startswith(module + "."):
            candidates.append(f"{module}.{target}")
            package = module.rsplit(".", 1)[0] if "." in module else ""
            if package:
                candidates.append(f"{package}.{target}")
        for candidate in candidates:
            if len(symbols.get(candidate, [])) == 1:
                return symbols[candidate][0]
            if candidate in module_files:
                return module_files[candidate]
            prefix = candidate
            while "." in prefix:
                prefix = prefix.rsplit(".", 1)[0]
                if prefix in module_files:
                    return module_files[prefix]
        if "." not in target:
            terminal = target.rsplit(".", 1)[-1]
            if len(terminals.get(terminal, [])) == 1:
                return terminals[terminal][0]
        # Relative Markdown/file reference.
        candidate_path = (PurePosixPath(relative).parent / PurePosixPath(target)).as_posix()
        normalized = os.path.normpath(candidate_path).replace("\\", "/")
        return _file_id(normalized) if normalized in records else None

    @staticmethod
    def _source_digest(snapshot: Mapping[str, Mapping[str, Any]]) -> str:
        digest = hashlib.sha256()
        for relative, info in sorted(snapshot.items()):
            digest.update(relative.encode("utf-8") + b"\0" + str(info["content_hash"]).encode("ascii") + b"\n")
        return digest.hexdigest()

    def _read(self, *, missing_ok: bool = False) -> dict[str, Any]:
        try:
            data = json.loads(self.graph_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            if missing_ok:
                return {}
            raise
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Cannot read Atlas graph {self.graph_path}: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("nodes", []), list):
            raise RuntimeError(f"Invalid Atlas graph at {self.graph_path}")
        if "links" not in data and "edges" in data:
            data = {**data, "links": data["edges"]}
        return data

    def _write(self, graph: Mapping[str, Any], *, destination: Path | None = None) -> None:
        destination = destination or self.graph_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(graph, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        fd, temporary = tempfile.mkstemp(prefix="atlas-", suffix=".tmp", dir=destination.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload); handle.flush(); os.fsync(handle.fileno())
            os.replace(temporary, destination)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise


# Keep the public name introduced by the concurrent prototype while making it
# use the complete native implementation.
AtlasStore = Atlas

__all__ = ["Atlas", "AtlasStore", "ATLAS_VERSION", "DEFAULT_AFFECTED_RELATIONS", "content_hash"]
