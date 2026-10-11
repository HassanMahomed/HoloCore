from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .animus import Animus
from .archive import ArchiveView
from .atlas import Atlas
from .config import Config
from .models import Result
from .animus_retrieval import AnimusRetriever, OpenAICompatibleEmbedder


class RouteLoopError(RuntimeError):
    """Raised when a unified search attempts to route back into itself."""


@dataclass(frozen=True)
class RoutePlan:
    """A checked, immutable execution plan for one unified search."""

    query: str
    world: str
    folders_ready: bool
    missing_paths: tuple[str, ...]
    atlas_state: str
    atlas_fresh: bool
    use_atlas: bool
    use_archive: bool
    use_animus: bool
    phases: tuple[str, ...]


_ROUTE_STACK: ContextVar[tuple[tuple[str, str], ...]] = ContextVar("holocore_route_stack", default=())


class Router:
    """Check-first router; selected subsystems execute once without recursion."""

    HISTORY = ("previous", "prior", "earlier", "conversation", "history", "remember", "last time", "what did we decide", "what did we try", "past attempt", "lesson learned", "decision", "decided")
    ARCHIVE = ("decision", "decided", "document", "documented", "guidance", "policy", "rule", "spec", "requirement", "architecture", "brief", "why")
    MAX_ATLAS_RESULTS = 3
    MAX_ARCHIVE_RESULTS = 3
    MAX_ARCHIVE_SNIPPET_CHARS = 600
    MAX_ANIMUS_RESULTS = 2
    MAX_ANIMUS_SNIPPET_CHARS = 600

    def __init__(self, root: Path, config: Config | None = None):
        self.root = root.resolve()
        self.config = config or Config.load(root=self.root)
        self.archive = ArchiveView(self.config.vault)
        self.atlas = Atlas(
            self.root,
            output=self.config.atlas_graph_path,
            runtime_output=self.config.atlas_path,
            knowledge_roots={"archive": self.config.vault},
        )
        self.animus = Animus(self.config.animus_path)
        embedding = (self.config.llm or {}).get("embedding") if self.config.llm else None
        self.retriever = AnimusRetriever(self.animus, OpenAICompatibleEmbedder(str(embedding["base_url"]), str(embedding["model"])) if isinstance(embedding, dict) and embedding.get("base_url") and embedding.get("model") else None)
        self.world_id = self.config.world_id or self.root.name
        self.last_plan: RoutePlan | None = None

    def ensure_memory_scope(self) -> None:
        """Create the active Animus scope only immediately before a memory write/read."""
        self.animus.create_world(self.world_id, self.root.name, {"root": str(self.root)})
        for sector in ("general", "project", "conversations", "sources"):
            self.animus.create_sector(self.world_id, sector)

    def plan(self, query: str, world: str | None = None) -> RoutePlan:
        """Check required folders and Atlas freshness before building a read route."""
        scope = world or self.world_id
        required_paths = (self.config.state_dir, self.config.vault)
        missing_paths = tuple(str(path) for path in required_paths if not path.is_dir())
        atlas_status = self.atlas.freshness()
        q = query.casefold()
        use_animus = any(term in q for term in self.HISTORY)
        plan = RoutePlan(
            query=query,
            world=scope,
            folders_ready=not missing_paths,
            missing_paths=missing_paths,
            atlas_state=str(atlas_status.get("state", "unknown")),
            atlas_fresh=bool(atlas_status.get("fresh")),
            use_atlas=bool(atlas_status.get("fresh")),
            use_archive=any(term in q for term in self.ARCHIVE) or use_animus or not bool(atlas_status.get("fresh")),
            use_animus=use_animus,
            phases=("check", "atlas", "archive", "animus", "sources"),
        )
        self.last_plan = plan
        return plan

    @staticmethod
    def _expanded_query(query: str, hits: list[dict[str, Any]], fields: tuple[str, ...]) -> str:
        """Add bounded graph/note context while preserving one downstream call."""
        parts = [query]
        for hit in hits[:5]:
            for field in fields:
                value = str(hit.get(field, "")).strip()
                if value:
                    parts.append(value)
        return " ".join(dict.fromkeys(parts))

    def search(self, query: str, world: str | None = None) -> list[Result]:
        scope = world or self.world_id
        route_key = (query.casefold(), scope.casefold())
        stack = _ROUTE_STACK.get()
        if route_key in stack:
            raise RouteLoopError(f"recursive HoloCore route rejected for query: {query}")
        token = _ROUTE_STACK.set((*stack, route_key))
        try:
            plan = self.plan(query, scope)
            results: list[Result] = []
            if not plan.folders_ready:
                results.append(Result("CHECK", "Required folders missing", "Run holocore init before relying on project knowledge.", "; ".join(plan.missing_paths)))
            atlas_hits: list[dict[str, Any]] = []
            atlas_limit = 1 if plan.use_animus else self.MAX_ATLAS_RESULTS
            if plan.use_atlas:
                atlas_hits = self.atlas.search(query)[: atlas_limit]
                for hit in atlas_hits:
                    results.append(Result("ATLAS", hit.get("label", hit["id"]), hit.get("kind", "signal"), hit.get("source_file", "")))
            else:
                results.append(Result("CHECK", f"Atlas {plan.atlas_state}", "Run atlas-refresh before relying on structural context.", str(self.atlas.output)))

            archive_query = self._expanded_query(query, atlas_hits, ("label", "qualified_name", "source_file"))
            archive_hits: list[dict[str, Any]] = []
            if plan.use_archive:
                archive_limit = 1 if plan.use_animus else self.MAX_ARCHIVE_RESULTS
                archive_hits = self.archive.search(archive_query)[: archive_limit]
                for hit in archive_hits:
                    snippet_limit = 180 if plan.use_animus else self.MAX_ARCHIVE_SNIPPET_CHARS
                    snippet = str(hit.get("snippet", ""))[: snippet_limit]
                    results.append(Result("ARCHIVE", hit["title"], snippet, hit["path"]))

            if plan.use_animus:
                self.ensure_memory_scope()
                memory_query = self._expanded_query(archive_query, archive_hits, ("title", "path"))
                if self.retriever.embedder is None:
                    hits = self.animus.search(memory_query, world=scope)[:1]
                    for hit in hits:
                        source = hit.provenance[0].source_ref if hit.provenance else ""
                        results.append(Result("ANIMUS", f"Memory Shard {hit.id}", str(hit.content)[:180], source))
                else:
                    for hit in self.retriever.search(memory_query, world=scope)[:1]:
                        source = hit.shard.provenance[0].source_ref if hit.shard.provenance else ""
                        results.append(Result("ANIMUS", f"Memory Shard {hit.shard.id}", str(hit.content)[:180], source))
            return results
        finally:
            _ROUTE_STACK.reset(token)


__all__ = ["RouteLoopError", "RoutePlan", "Router"]
