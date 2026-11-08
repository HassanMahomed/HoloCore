"""Provider-neutral scoped retrieval for project Memories.

Semantic providers are optional. The deterministic lexical scorer remains the
baseline and is also used when an embedding provider is unavailable.
"""
from __future__ import annotations

import math
import json
import os
import re
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .memories import MemoryShard, MemoryStore


@dataclass(frozen=True)
class RetrievalResult:
    shard: MemoryShard
    score: float
    method: str
    matched_terms: tuple[str, ...]

    @property
    def content(self) -> str: return self.shard.content


class OpenAICompatibleEmbedder:
    """Optional OpenAI-compatible embeddings endpoint; local retrieval remains the fallback."""
    def __init__(self, base_url: str, model: str, *, api_key_env: str = "HOLOCORE_EMBEDDING_API_KEY", timeout: float = 30.0):
        self.base_url, self.model, self.api_key_env, self.timeout = base_url, model, api_key_env, timeout

    def __call__(self, text: str) -> Sequence[float]:
        headers = {"Content-Type": "application/json"}
        key = os.getenv(self.api_key_env)
        if key: headers["Authorization"] = f"Bearer {key}"
        url = self.base_url.rstrip("/")
        if not url.endswith("/embeddings"): url += "/embeddings"
        request = urllib.request.Request(url, json.dumps({"model": self.model, "input": text}).encode(), headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            data = json.loads(response.read().decode())
        return data["data"][0]["embedding"]


def _tokens(value: str) -> list[str]:
    return re.findall(r"[\w-]+", value.casefold(), re.UNICODE)


class MemoryRetriever:
    def __init__(self, memories: MemoryStore, embedder: Callable[[str], Sequence[float]] | None = None):
        self.memories, self.embedder = memories, embedder

    @staticmethod
    def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
        if len(a) != len(b) or not a or not any(a) or not any(b): return 0.0
        return sum(x * y for x, y in zip(a, b)) / (math.sqrt(sum(x*x for x in a)) * math.sqrt(sum(y*y for y in b)))

    def search(self, query: str, *, world: str, sector: str | None = None, limit: int = 10,
               metadata: Mapping[str, Any] | None = None, route_hint: str | None = None,
               min_score: float = 0.0) -> list[RetrievalResult]:
        query_terms = set(_tokens(query)); metadata = metadata or {}
        candidates = self.memories.search(query, world=world, sector=sector, limit=max(limit * 5, 50))
        semantic_scores: dict[str, float] = {}
        # MemoryStore.search is intentionally lexical; this second pass permits a
        # provider to rescue synonym/semantic matches while retaining scope.
        if self.embedder:
            try:
                qv = self.embedder(query)
                candidates = self.memories.shards(world=world, sector=sector)
                # Include the complete explicit scope for semantic synonyms;
                # the lexical pre-pass alone cannot discover those records.
                scored = [(self._cosine(qv, self.embedder(item.content)), item) for item in self.memories.shards(world=world, sector=sector)]
                semantic_scores = {item.id: score for score, item in scored}
                candidates = [item for score, item in sorted(scored, key=lambda x: (-x[0], x[1].id)) if score > 0]
            except (OSError, RuntimeError, ValueError, TypeError):
                self.embedder = None
        results: list[RetrievalResult] = []
        for item in candidates:
            if route_hint and item.route_hint != route_hint: continue
            if any(item.metadata.get(key) != value for key, value in metadata.items()): continue
            terms = tuple(sorted(query_terms.intersection(_tokens(item.content))))
            lexical = sum(item.content.casefold().count(term) for term in query_terms)
            score = semantic_scores.get(item.id, float(item.score or lexical))
            method = "semantic" if self.embedder else "lexical"
            if score >= min_score and score > 0: results.append(RetrievalResult(item, score, method, terms))
        results.sort(key=lambda result: (-result.score, result.shard.id))
        return results[:limit]

    retrieve = search


AnimusRetriever = MemoryRetriever
SemanticRetriever = MemoryRetriever

__all__ = ["MemoryRetriever", "AnimusRetriever", "OpenAICompatibleEmbedder", "RetrievalResult", "SemanticRetriever"]
