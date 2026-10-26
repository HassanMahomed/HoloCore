"""One-way promotion of an existing curated folder into the Second Brain World."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path


EXCLUDED_PARTS = {".git", ".obsidian", ".holocore", "holocore-out", "graphify-out", "node_modules"}


def sync_archive_source(source: str | Path, home: Path | None = None) -> dict[str, object]:
    source_path = Path(source).expanduser().resolve()
    if not source_path.is_dir():
        raise FileNotFoundError(f"Archive source directory does not exist: {source_path}")
    if home is None:
        from .home import HomeManager
        home = HomeManager().home
    from .folder_layout import HomeLayout
    target_root = (HomeLayout(home).project_knowledge("Second Brain") / "wiki").resolve()
    if target_root == source_path or source_path in target_root.parents:
        raise ValueError("Archive source cannot be inside the HoloCore Second Brain World")
    copied: list[str] = []
    skipped: list[str] = []
    conflicts: list[str] = []
    target_root.mkdir(parents=True, exist_ok=True)
    from .folder_layout import ensure_legacy_aliases
    ensure_legacy_aliases(home)
    for path in sorted(source_path.rglob("*")):
        if not path.is_file() or path.suffix.lower() != ".md":
            continue
        relative = path.relative_to(source_path)
        if any(part in EXCLUDED_PARTS for part in relative.parts):
            continue
        destination = target_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if _digest(path) == _digest(destination):
                skipped.append(str(relative))
            else:
                conflicts.append(str(relative))
            continue
        shutil.copy2(path, destination)
        copied.append(str(relative))
    return {"source": str(source_path), "destination": str(target_root), "copied": copied, "skipped": skipped, "conflicts": conflicts, "one_way": True}


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["sync_archive_source"]
