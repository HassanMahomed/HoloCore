"""Scoped project Memory collection from files, conversations, and Git history."""
from __future__ import annotations

import fnmatch
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .memories import MemoryStore


MODES = ("files", "conversations", "git")


@dataclass(frozen=True)
class CollectionOptions:
    mode: str = "files"
    world: str = ""
    sector: str = "project"
    root: Path = Path(".")
    source_ref: str = ""
    ignore: tuple[str, ...] = (".git", ".holocore", "__pycache__", "*.pyc")
    limit: int = 1000


@dataclass(frozen=True)
class CollectionReport:
    mode: str
    world: str
    sector: str
    examined: int
    stored: int
    deduplicated: int
    skipped: int
    checkpoint: str
    shards: tuple[str, ...] = field(default_factory=tuple)


class MemoryCollector:
    def __init__(self, memories: MemoryStore): self.memories = memories

    @staticmethod
    def _ignored(path: Path, root: Path, patterns: Iterable[str]) -> bool:
        rel = path.relative_to(root).as_posix()
        return any(fnmatch.fnmatch(rel, p) or any(fnmatch.fnmatch(part, p) for part in path.relative_to(root).parts) for p in patterns)

    def collect(self, options: CollectionOptions) -> CollectionReport:
        if options.mode not in MODES: raise ValueError(f"mode must be one of {', '.join(MODES)}")
        if not options.world: raise ValueError("project is required; memory collection is never global")
        self.memories.get_world(options.world)
        if options.sector: self.memories.get_sector(options.world, options.sector)
        source = options.source_ref or str(options.root.resolve())
        checkpoint = self.memories.get_checkpoint(world=options.world, sector=options.sector, mode=options.mode, source_ref=source)
        start = checkpoint.cursor if checkpoint else ""
        records: list[tuple[str, str, str]] = []
        if options.mode == "files": records = self._files(options, start)
        elif options.mode == "conversations": records = self._conversations(options, start)
        else: records = self._git(options, start)
        stored = dedup = skipped = 0; ids: list[str] = []; cursor = start
        for ref, text, token in records[:options.limit]:
            cursor = token
            if not text.strip() or "\x00" in text: skipped += 1; continue
            shard = self.memories.ingest(text, world=options.world, sector=options.sector, source_ref=ref,
                                       version_token=token, route_hint=f"mined:{options.mode}",
                                       transformations=(f"memory-collection:{options.mode}",),
                                       metadata={"collection_mode": options.mode, "source_root": str(options.root)})
            ids.append(shard.id)
            if shard.action == "deduplicated": dedup += 1
            elif shard.action in {"inserted", "updated"}: stored += 1
        self.memories.set_checkpoint(world=options.world, sector=options.sector, mode=options.mode, source_ref=source, cursor=cursor)
        return CollectionReport(options.mode, options.world, options.sector, len(records), stored, dedup, skipped, cursor, tuple(ids))

    def _files(self, o: CollectionOptions, start: str) -> list[tuple[str, str, str]]:
        result = []
        for path in sorted(p for p in o.root.rglob("*") if p.is_file()):
            if self._ignored(path, o.root, o.ignore): continue
            if path.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".pyc", ".dll", ".exe", ".bin"}: continue
            ref, token = str(path.resolve()), str(path.stat().st_mtime_ns)
            if token <= start: continue
            try: text = path.read_text(encoding="utf-8", errors="replace")
            except OSError: continue
            result.append((ref, text, token))
        return result

    def _conversations(self, o: CollectionOptions, start: str) -> list[tuple[str, str, str]]:
        result = []
        for ref, text, token in self._files(o, start):
            if Path(ref).suffix.lower() not in {".json", ".jsonl", ".txt", ".md"}: continue
            if Path(ref).suffix.lower() == ".json":
                try:
                    payload = json.loads(text); messages = payload.get("messages", payload) if isinstance(payload, dict) else payload
                    text = "\n".join(str(m.get("content", m)) for m in messages) if isinstance(messages, list) else str(payload)
                except (ValueError, TypeError): pass
            result.append((ref, text, token))
        return result

    def _git(self, o: CollectionOptions, start: str) -> list[tuple[str, str, str]]:
        try:
            raw = subprocess.check_output(["git", "-C", str(o.root), "log", "--format=%H%x1f%aI%x1f%s%x1e", f"-{o.limit}"], text=True, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.CalledProcessError): return []
        result = []
        for row in raw.split("\x1e"):
            parts = row.strip("\n").split("\x1f")
            if len(parts) == 3 and parts[0] > start: result.append((f"git:{parts[0]}", f"{parts[2]} ({parts[1]})", parts[0]))
        return result


    mine = collect


AnimusMiner = MemoryCollector
MiningOptions = CollectionOptions
MiningReport = CollectionReport

__all__ = [
    "MemoryCollector", "CollectionOptions", "CollectionReport", "MODES",
    "AnimusMiner", "MiningOptions", "MiningReport",
]
