"""Read-only research and architecture briefs built from scoped HoloCore evidence."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .engine import HoloCoreEngine


WORKFLOW_KINDS = ("research", "architecture")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _memory_evidence(item: Any) -> dict[str, Any]:
    sources = [source.source_ref for source in item.provenance]
    return {
        "source": "Memories",
        "title": f"Memory {item.id[:8]}",
        "excerpt": item.content[:800],
        "location": sources[0] if sources else item.id,
        "provenance": [asdict(source) for source in item.provenance],
        "confidence": "remembered",
    }


def run_guided_workflow(
    root: str | Path,
    *,
    kind: str,
    question: str,
    sector: str | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """Build a bounded evidence brief without promoting or mutating knowledge."""
    kind = str(kind).strip().casefold()
    question = str(question).strip()
    if kind not in WORKFLOW_KINDS:
        raise ValueError(f"workflow must be one of: {', '.join(WORKFLOW_KINDS)}")
    if not question:
        raise ValueError("Enter a research question or architecture topic.")
    limit = max(1, min(int(limit), 10))
    engine = HoloCoreEngine(Path(root))
    engine.ensure_atlas()
    engine.router.ensure_memory_scope()
    atlas_hits = engine.router.atlas.search(question, limit=limit)
    archive_hits = engine.router.archive.search(question, limit=limit)
    memory_hits = engine.router.memories.search(question, world=engine.router.world_id, sector=sector, limit=limit)

    evidence: list[dict[str, Any]] = []
    for hit in atlas_hits:
        neighborhood = engine.router.atlas.neighborhood(str(hit.get("id")), depth=1)
        related = [
            str(item.get("label") or item.get("id"))
            for item in neighborhood.get("nodes", [])
            if str(item.get("id")) != str(hit.get("id"))
        ][:8]
        evidence.append({
            "source": "Code Map", "title": str(hit.get("label") or hit.get("id")),
            "excerpt": str(hit.get("kind") or "mapped item") + (f"; related to {', '.join(related)}" if related else ""),
            "location": str(hit.get("source_file") or ""), "confidence": "extracted",
            "related": related,
        })
    for hit in archive_hits:
        evidence.append({
            "source": "Knowledge", "title": str(hit.get("title") or hit.get("path")),
            "excerpt": str(hit.get("snippet") or "")[:800], "location": str(hit.get("path") or ""),
            "provenance": hit.get("provenance", {}), "confidence": "verified",
        })
    evidence.extend(_memory_evidence(item) for item in memory_hits)

    counts = {name: sum(1 for item in evidence if item["source"] == name) for name in ("Code Map", "Knowledge", "Memories")}
    gaps = [f"No matching {name.lower()} evidence was found." for name, count in counts.items() if count == 0]
    if kind == "architecture":
        next_steps = [
            "Inspect the cited source files and relationship neighborhoods.",
            "Record constraints and trade-offs before choosing a design.",
            "Promote only verified decisions into Knowledge after review.",
        ]
        heading = "Architecture brief"
    else:
        next_steps = [
            "Open the strongest cited sources and verify the claims.",
            "Resolve the listed evidence gaps or mark them as open questions.",
            "Promote the reviewed conclusion into Knowledge only when it is durable.",
        ]
        heading = "Research brief"
    generated_at = _now()
    lines = [
        f"# {heading}: {question}", "", f"Generated: {generated_at}", "",
        "## Evidence summary", "",
        f"- Code Map: {counts['Code Map']} matches", f"- Knowledge: {counts['Knowledge']} matches",
        f"- Memories: {counts['Memories']} matches", "", "## Evidence", "",
    ]
    for item in evidence:
        lines.extend([
            f"### {item['source']}: {item['title']}", "", item["excerpt"] or "No excerpt available.", "",
            f"- Location: `{item['location']}`", f"- Confidence: {item['confidence']}", "",
        ])
    lines.extend(["## Gaps", "", *([f"- {gap}" for gap in gaps] or ["- No source category is completely missing."]), "", "## Next steps", "", *[f"- {step}" for step in next_steps], ""])
    return {
        "kind": kind, "question": question, "world": engine.router.world_id, "sector": sector,
        "generated_at": generated_at, "read_only": True, "counts": counts, "evidence": evidence,
        "gaps": gaps, "next_steps": next_steps, "markdown": "\n".join(lines),
    }


__all__ = ["WORKFLOW_KINDS", "run_guided_workflow"]
