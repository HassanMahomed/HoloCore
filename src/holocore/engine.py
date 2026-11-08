from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path

from .config import Config
from .install import bootstrap
from .ingest import RawIngestor
from .layout import integration_status, world_paths
from .llm import provider_from_config
from .memory_pipeline import MemoryExtraction, MemoryRefinementPipeline
from .models import Result
from .router import Router


class HoloCoreEngine:
    """The single HoloCore runtime: wiki, graph, memory, install, CLI and MCP."""

    def __init__(self, root: Path, config: Config | None = None):
        self.root = root.resolve(); self.router = Router(self.root, config); self._cache: dict[tuple[str, str | None], list[Result]] = {}; self._inbox_synced = False

    def _reload(self) -> None:
        self.router = Router(self.root, Config.load(root=self.root))
        self._cache.clear()
    def initialize(self, *, git: bool = True, platforms: list[str] | None = None, home: Path | None = None) -> dict:
        value = bootstrap(self.root, init_git=git, platforms=platforms, home=home)
        self._reload()
        self.router.ensure_memory_scope()
        return value
    def connect(self, *, platforms: list[str] | None = None, home: Path | None = None) -> dict:
        return self.initialize(git=False, platforms=platforms, home=home)
    def setup(self, *, git: bool = False, platforms: list[str] | None = None, home: Path | None = None) -> dict:
        installation = self.initialize(git=git, platforms=platforms, home=home)
        from .lifecycle import installation_check
        atlas = self.refresh()
        overview = self._ensure_project_overview()
        if overview.get("changed"):
            atlas = self.refresh()
        html = str(atlas["html"])
        return {
            "ready": True,
            "world": str(self.root),
            "installation": installation,
            "installation_check": installation_check(),
            "atlas": atlas,
            "project_wiki": overview,
            "atlas_html": html,
            "paths": world_paths(self.root, self.router.config),
            "next_steps": installation["next_steps"],
        }

    def _ensure_project_overview(self) -> dict:
        """Maintain one useful project wiki from verified Atlas structure."""
        graph = json.loads(self.router.atlas.graph_path.read_text(encoding="utf-8"))
        nodes = [
            node for node in graph.get("nodes", [])
            if not str(node.get("source_file") or "").endswith("/wiki/project-overview.md")
        ]
        files = [node for node in nodes if node.get("kind") == "file"]
        languages = Counter(str(node.get("language") or "other") for node in files)
        areas = Counter(
            str(node.get("source_file") or "root").replace("\\", "/").split("/", 1)[0]
            for node in files
        )
        symbols = []
        for node in nodes:
            if node.get("kind") not in {"class", "function", "component"}:
                continue
            label = str(node.get("label") or "").strip()
            if label and label not in symbols:
                symbols.append(label)
            if len(symbols) == 15:
                break
        language_text = ", ".join(f"{name} ({count})" for name, count in languages.most_common(6)) or "none"
        area_text = ", ".join(f"{name} ({count})" for name, count in areas.most_common(8)) or "root"
        evidence = (
            f"{self.root.name} is a project with {len(files)} indexed source files and {len(nodes)} mapped signals. "
            f"Its main source areas are {area_text}. Languages and document types are {language_text}. "
            f"Representative named symbols are {', '.join(symbols) or 'not yet available'}."
        )
        extraction = MemoryExtraction.from_mapping(
            provider_from_config(self.router.config.llm or {}).extract(
                [{"role": "user", "content": evidence}],
                instructions="Write a concise project overview using only the supplied Atlas evidence.",
            )
        )
        marker = "<!-- holocore:managed-project-overview -->"
        body = "\n".join(
            [
                marker,
                "## For future Claude",
                "Use this overview for orientation, then inspect Atlas evidence and exact source files.",
                "",
                f"# {self.root.name} Project Overview",
                "",
                "## Summary",
                "",
                extraction.summary or evidence,
                "",
                "## Main Areas",
                "",
                *[f"- {name}: {count} indexed files" for name, count in areas.most_common(8)],
                "",
                "## Languages and Document Types",
                "",
                *[f"- {name}: {count}" for name, count in languages.most_common(8)],
                "",
                "## Representative Symbols",
                "",
                *([f"- {label}" for label in symbols] or ["- No named symbols extracted yet."]),
                "",
                "## Provenance",
                "",
                f"- Atlas: `{self.router.atlas.graph_path}`",
                f"- Files: {len(files)}",
                f"- Signals: {len(nodes)}",
            ]
        )
        relative = "wiki/project-overview.md"
        target = self.router.config.vault / relative
        archive = self.router.archive.world
        if target.exists():
            existing = target.read_text(encoding="utf-8-sig")
            if marker not in existing:
                return {"changed": False, "path": str(target), "reason": "user-owned overview preserved"}
            existing_body = existing.split("---", 2)[-1].strip()
            if existing_body == body.strip():
                return {"changed": False, "path": str(target), "reason": "overview is current"}
            report = archive.update(relative, body=body, expected_content=existing)
            return {"changed": True, "path": str(target), **report}
        report = archive.create(
            relative,
            body,
            frontmatter={
                "type": "project-overview",
                "tags": ["project-overview", "wiki", "atlas"],
                "provenance": {"system": "holocore", "operation": "atlas-project-overview"},
            },
        )
        return {"changed": True, "path": str(target), **report}
    def paths(self) -> dict[str, str]:
        return world_paths(self.root, self.router.config)
    def _atlas_html_paths(self) -> list[Path]:
        graph = self.router.atlas.graph_path
        return list(dict.fromkeys((
            graph.parent / "atlas.html",
            graph.with_suffix(".html"),
        )))
    def _write_atlas_views(self) -> list[Path]:
        from .atlas_html import generate_atlas_views
        return generate_atlas_views(self.router.atlas.graph_path, self._atlas_html_paths())
    def _atlas_views_current(self) -> bool:
        from .atlas_html import ATLAS_VIEWER_VERSION
        marker = f'name="holocore-atlas-viewer" content="{ATLAS_VIEWER_VERSION}"'
        graph_time = self.router.atlas.graph_path.stat().st_mtime_ns
        for path in self._atlas_html_paths():
            try:
                if path.stat().st_mtime_ns < graph_time or marker not in path.read_text(encoding="utf-8")[:1024]:
                    return False
            except OSError:
                return False
        return True
    def ensure_atlas(self) -> dict:
        freshness = self.router.atlas.freshness()
        if freshness.get("fresh"):
            generated_html = not self._atlas_views_current()
            html = self._write_atlas_views()[0] if generated_html else self._atlas_html_paths()[0]
            return {"refreshed": False, "html": str(html), "generated_html": generated_html, **freshness}
        return {"refreshed": True, **self.refresh()}
    def search(self, query: str, world: str | None = None) -> list[Result]:
        key = (query, world)
        if key in self._cache:
            return self._cache[key]
        atlas_ready = False
        if not self._inbox_synced and self.router.config.home:
            atlas_ready = bool(self.sync_inbox().get("atlas"))
        if not atlas_ready:
            self.ensure_atlas()
        self._cache[key] = self.router.search(query, world)
        return self._cache[key]
    def status(self) -> dict:
        required = tuple(
            path for path in (
                self.router.config.state_dir,
                self.router.config.vault,
            ) if path is not None
        )
        readiness = {"ready": all(path.is_dir() for path in required), "missing": [str(path) for path in required if not path.is_dir()]}
        try:
            memories = self.router.memories.status(self.router.world_id)
        except KeyError:
            memories = {"ready": False, "backend": "sqlite", "database": str(self.router.config.memory_path), "world": self.router.world_id, "worlds": 0, "sectors": 0, "memory_shards": 0, "source_references": 0}
        from .local_models import LocalModelManager
        manager = LocalModelManager(self.router.config.home or self.router.config.state_dir)
        local_model = manager.status(self.router.config.llm)
        return {"world": str(self.root), "world_id": self.router.world_id, "readiness": readiness, "paths": self.paths(), "integrations": integration_status(self.root), "archive": self.router.archive.health(), "atlas": self.router.atlas.freshness(), "memories": memories, "animus": memories, "local_model": local_model}
    def refresh(self) -> dict:
        graph = self.router.atlas.refresh(); self._cache.clear()
        html = self._write_atlas_views()[0]
        report = self.router.atlas.write_report()
        communities = self.router.atlas.write_community_knowledge()
        return {
            "path": str(self.router.atlas.graph_path), "html": str(html), "report": str(report),
            "community_knowledge": communities, "nodes": len(graph.get("nodes", [])),
            "edges": len(graph.get("links", graph.get("edges", []))),
            "constellations": communities["count"], "fresh": True,
        }
    def mine(self, world: Path | None = None, sector: str = "project") -> dict:
        self.router.ensure_memory_scope()
        root = (world or self.root).resolve(); added = deduplicated = 0
        for path in root.rglob("*"):
            if not path.is_file() or any(part in {".git", ".holocore", ".venv", "__pycache__", "engines", "graphify-out", "holocore-out"} for part in path.parts): continue
            try: content = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError): continue
            shard = self.router.memories.ingest(content, world=self.router.world_id, sector=sector, source_ref=str(path))
            added += int(not shard.deduplicated); deduplicated += int(shard.deduplicated)
        return {"added": added, "deduplicated": deduplicated}
    def remember(self, content: str, sector: str = "general", source: str = "") -> dict:
        self.router.ensure_memory_scope()
        shard = self.router.memories.ingest(content, world=self.router.world_id, sector=sector, source_ref=source or "manual"); self._cache.clear(); return {"id": shard.id, "created": shard.action == "inserted", "deduplicated": shard.action in {"deduplicated", "unchanged"}, "action": shard.action}
    def _promote_extraction(self, extraction, source: str) -> dict:
        useful = bool(extraction.decisions or extraction.facts or len(extraction.summary.strip()) >= 40)
        if not useful:
            return {"promoted": False, "reason": "no durable facts or decisions"}
        payload = json.dumps(extraction.__dict__, ensure_ascii=False, sort_keys=True, default=list)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
        prefix = "wiki/memory"
        relative = f"{prefix}/{date.today().isoformat()}-{digest}.md"
        target = self.router.config.vault / relative
        if target.exists():
            return {"promoted": False, "deduplicated": True, "path": relative}
        sections = [
            f"# Conversation memory {date.today().isoformat()}",
            "",
            "Related: [[system/index|World Archive index]]",
            "",
            "## Summary",
            extraction.summary or "No summary recorded.",
        ]
        for title, values in (("Facts", extraction.facts), ("Decisions", extraction.decisions), ("Preferences", extraction.preferences), ("Entities", extraction.entities)):
            if values:
                sections.extend(["", f"## {title}", *[f"- {item}" for item in values]])
        result = self.router.archive.create(relative, "\n".join(sections), provenance={"system": "holocore", "operation": "memory-promotion", "world": self.router.world_id, "source": source})
        return {"promoted": True, **result}

    @staticmethod
    def _chunks(text: str, size: int = 6000):
        for start in range(0, len(text), size):
            value = text[start : start + size].strip()
            if value:
                yield start // size, value

    def _integrate_source_item(self, item: dict) -> dict:
        text = str(item.get("text", "")).strip()
        digest = str(item.get("content_hash") or "")
        if not text or not digest:
            return {"memory_shards": [], "archive_entry": None, "warning": item.get("warning")}
        relative = f"wiki/sources/{digest[:20]}.md"
        target = self.router.config.vault / relative
        if item.get("deduplicated") and target.is_file():
            return {"memory_shards": [], "archive_entry": {"deduplicated": True, "path": relative}, "warning": item.get("warning")}

        self.router.ensure_memory_scope()
        shard_ids = []
        source_ref = str(item.get("raw_path") or item.get("source") or "source")
        for index, chunk in self._chunks(text):
            shard = self.router.memories.ingest(
                chunk,
                world=self.router.world_id,
                sector="sources",
                source_ref=source_ref,
                chunk_index=index,
                route_hint="raw-source",
                transformations=("text-extraction",),
                metadata={"content_hash": digest, "media_type": item.get("media_type")},
            )
            shard_ids.append(shard.id)

        settings = self.router.config.llm or {}
        maximum = max(1000, int(settings.get("max_ingest_chars", 50000)))
        extraction = MemoryExtraction.from_mapping(
            provider_from_config(settings).extract(
                [{"role": "user", "content": text[:maximum]}],
                instructions=str(settings.get("source_instructions", "Extract only source-supported facts, decisions, entities, and a concise durable summary.")),
            )
        )
        title = str(item.get("title") or "Ingested source").strip()
        sections = [
            f"# {title}",
            "",
            "Related: [[system/index|World Archive index]]",
            "",
            "## Source",
            f"- Original: `{item.get('source')}`",
            f"- Immutable raw copy: `{item.get('raw_path')}`",
            f"- Media type: `{item.get('media_type')}`",
            f"- SHA-256: `{digest}`",
            "",
            "## Summary",
            extraction.summary or "No textual summary was extracted.",
        ]
        for heading, values in (("Facts", extraction.facts), ("Decisions", extraction.decisions), ("Entities", extraction.entities)):
            if values:
                sections.extend(["", f"## {heading}", *[f"- {value}" for value in values]])
        if target.is_file():
            archive_entry = {"deduplicated": True, "path": relative}
        else:
            archive_entry = self.router.archive.create(
                relative,
                "\n".join(sections),
                frontmatter={
                    "type": "source",
                    "tags": ["source"],
                    "source_hash": digest,
                    "source_type": item.get("media_type"),
                },
                provenance={"system": "holocore", "operation": "source-ingest", "source": item.get("source"), "raw_path": item.get("raw_path")},
            )
        return {"memory_shards": shard_ids, "archive_entry": archive_entry, "warning": item.get("warning")}

    def _refresh_source_map(self) -> dict:
        entries = []
        source_root = self.router.config.vault / "wiki" / "sources"
        for path in sorted(source_root.glob("*.md")) if source_root.is_dir() else []:
            try:
                text = path.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            title = next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), path.stem)
            entries.append((title, path.relative_to(self.router.config.vault).as_posix()))
        target = self.router.config.vault / "system" / "source-map.md"
        marker = "<!-- holocore:managed-source-map -->"
        if not entries and not target.exists():
            return {"updated": False, "path": str(target), "entries": 0}
        if target.exists():
            existing = target.read_text(encoding="utf-8-sig", errors="replace")
            if marker not in existing:
                return {"updated": False, "path": str(target), "warning": "Existing user-owned HOLOCORE-SOURCES.md was not overwritten."}
        lines = [marker, "# HoloCore Source Map", "", "Generated metadata only; immutable source content remains in the shared HoloCore Home.", ""]
        for title, relative in entries:
            lines.extend([f"## {title}", "", f"- Archive Entry: `{relative}`", ""])
        content = "\n".join(lines)
        if target.exists() and target.read_text(encoding="utf-8") == content:
            return {"updated": False, "path": str(target), "entries": len(entries)}
        temporary = target.with_suffix(".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(target)
        return {"updated": True, "path": str(target), "entries": len(entries)}

    def ingest_source(self, source: str | Path, *, title: str | None = None) -> dict:
        ingestor = RawIngestor(self.router.config.state_dir.parent / "Sources", self.router.config.state_dir / "ingest-state.json")
        acquired = ingestor.ingest(source, title=title)
        items = list(acquired.get("items", [])) if isinstance(acquired.get("items"), list) else [acquired]
        integrated = [self._integrate_source_item(item) for item in items if not item.get("skipped")]
        self._cache.clear()
        source_map = self._refresh_source_map()
        atlas = self.ensure_atlas()
        item_statuses = [str(item.get("extraction_status", "unknown")) for item in items]
        status = "ok" if any(item_status in {"extracted", "no_text"} for item_status in item_statuses) else (item_statuses[0] if item_statuses else "ok")
        return {**acquired, "status": status, "integrated": integrated, "source_map": source_map, "atlas": atlas}

    def sync_inbox(self) -> dict:
        if not self.router.config.home:
            return {"synced": False, "reason": "shared Home is not configured", "items": []}
        inbox = self.router.config.state_dir.parent / "Inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        ingestor = RawIngestor(self.router.config.state_dir.parent / "Sources", self.router.config.state_dir / "ingest-state.json")
        acquired = ingestor.sync_inbox(inbox)
        integrated = [self._integrate_source_item(item) for item in acquired.get("items", []) if not item.get("skipped")]
        self._inbox_synced = True
        self._cache.clear()
        source_map = self._refresh_source_map()
        atlas = self.ensure_atlas()
        return {**acquired, "synced": True, "inbox": str(inbox), "integrated": integrated, "source_map": source_map, "atlas": atlas}
    def ingest_chat(self, messages: list[dict[str, str]], *, sector: str = "conversations", source: str = "chat", custom_instructions: str = "") -> dict:
        self.router.ensure_memory_scope()
        settings = self.router.config.llm or {}
        result = MemoryRefinementPipeline(self.router.memories, self.router.config.raw_chats_path, provider_from_config(settings)).refine(messages, world=self.router.world_id, sector=sector, source_ref=source, instructions=custom_instructions or str(settings.get("custom_instructions", "")))
        promotion = self._promote_extraction(result.extraction, source)
        self._cache.clear(); return {"raw_chat": str(result.audit_path), "shard_ids": [item.id for item in result.shards], "extracted": result.extraction.__dict__, "archive_entry": promotion}
