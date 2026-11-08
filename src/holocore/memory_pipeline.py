"""HoloCore-native raw-chat auditing and distilled memory refinement."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .memories import MemoryShard, MemoryStore
from .llm import LocalMemoryProvider, MemoryProvider


_KINDS = ("facts", "decisions", "preferences", "entities")


@dataclass(frozen=True)
class MemoryExtraction:
    summary: str
    facts: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()
    preferences: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MemoryExtraction":
        def items(key: str) -> tuple[str, ...]:
            raw = value.get(key, ())
            if isinstance(raw, str):
                raw = [raw]
            if not isinstance(raw, Sequence):
                raise ValueError(f"{key} must be an array of strings")
            result: list[str] = []
            for item in raw:
                text = str(item).strip()
                if text and text not in result:
                    result.append(text)
            return tuple(result)
        return cls(str(value.get("summary", "")).strip(), *(items(key) for key in _KINDS))


@dataclass(frozen=True)
class RefinementResult:
    extraction: MemoryExtraction
    shards: tuple[MemoryShard, ...]
    audit_path: Path
    provider: str = "deterministic"
    fallback_reason: str | None = None


class MemoryRefinementPipeline:
    def __init__(self, memories: MemoryStore, audit_dir: str | Path, provider: MemoryProvider | None = None):
        self.memories = memories
        self.audit_dir = Path(audit_dir)
        self.provider = provider or LocalMemoryProvider()

    @staticmethod
    def _messages(chat: str | Sequence[Mapping[str, Any]]) -> tuple[dict[str, str], ...]:
        if isinstance(chat, str):
            return ({"role": "user", "content": chat},)
        messages = tuple({"role": str(item.get("role", "user")), "content": str(item.get("content", ""))} for item in chat)
        if not messages or not any(item["content"].strip() for item in messages):
            raise ValueError("chat must contain at least one non-empty message")
        return messages

    def _audit(self, messages: Sequence[Mapping[str, str]], source_ref: str) -> Path:
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        canonical = json.dumps(list(messages), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256((source_ref + "\0" + canonical).encode("utf-8")).hexdigest()[:20]
        path = self.audit_dir / f"{digest}.json"
        if not path.exists():
            record = {"source_ref": source_ref, "recorded_at": datetime.now(timezone.utc).isoformat(), "messages": list(messages)}
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)
        return path

    def refine(self, chat: str | Sequence[Mapping[str, Any]], *, world: str, source_ref: str,
               sector: str | None = None, instructions: str = "", privacy: Mapping[str, Any] | None = None) -> RefinementResult:
        messages = self._messages(chat)
        source_ref = str(source_ref).strip()
        if not source_ref:
            raise ValueError("source_ref must be non-empty")
        audit_path = self._audit(messages, source_ref)
        extraction = MemoryExtraction.from_mapping(self.provider.extract(messages, instructions=instructions))
        provider_name = str(getattr(self.provider, "last_used", getattr(self.provider, "name", type(self.provider).__name__.casefold())))
        fallback_reason = getattr(self.provider, "last_error", None)
        records: list[tuple[str, str]] = []
        if extraction.summary:
            records.append(("summary", extraction.summary))
        for kind in _KINDS:
            records.extend((kind[:-1] if kind != "entities" else "entity", item) for item in getattr(extraction, kind))
        shards = tuple(self.memories.ingest(
            content, world=world, sector=sector,
            source_ref=f"{source_ref}#distilled/{kind}/{index}",
            chunk_index=0, route_hint="distilled-memory",
            transformations=("memory-refinement",), privacy=privacy,
            metadata={"memory_kind": kind, "raw_audit": str(audit_path), "provider": provider_name, "fallback_reason": fallback_reason},
            source_metadata={"raw_source_ref": source_ref},
        ) for index, (kind, content) in enumerate(records))
        transcript = "\n\n".join(
            f"{item['role']}: {item['content']}" for item in messages if item["content"].strip()
        )
        diary_content = extraction.summary or transcript
        if extraction.summary and transcript:
            diary_content = f"{extraction.summary}\n\nTranscript:\n{transcript}"
        self.memories.record_diary(
            diary_content,
            world=world,
            sector=sector,
            title=f"Conversation: {Path(source_ref).name or 'chat'}",
            kind="conversation",
            metadata={"message_count": len(messages), "raw_audit": str(audit_path), "provider": provider_name, "fallback_reason": fallback_reason},
            source_ref=source_ref,
        )
        return RefinementResult(extraction, shards, audit_path, provider_name, fallback_reason)


MemoryPipeline = MemoryRefinementPipeline

__all__ = ["MemoryExtraction", "MemoryPipeline", "MemoryRefinementPipeline", "RefinementResult"]
