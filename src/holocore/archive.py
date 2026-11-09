"""HoloCore-native Archive operations for local Markdown vaults.

This module intentionally uses only the Python standard library.  It preserves
the useful Obsidian vault semantics without importing or invoking the vendored
application.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


SKIP_DIRS = frozenset(
    {".obsidian", ".git", ".trash", "_trash", ".claude", "_export", "templates", "node_modules", "raw"}
)
PROTECTED_WRITE_DIRS = SKIP_DIRS
REQUIRED_FRONTMATTER = ("type", "date", "tags", "ai-first")
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
TOKEN_RE = re.compile(r"[\w-]+", re.UNICODE)


class ArchiveError(ValueError):
    """Base exception for rejected Archive operations."""


class PathTraversalError(ArchiveError):
    """A requested path escaped the vault or entered a protected directory."""


class ArchiveValidationError(ArchiveError):
    """A note failed the AI-first validation contract."""

    def __init__(self, issues: Iterable[str]):
        self.issues = tuple(issues)
        super().__init__("; ".join(self.issues))


class ArchiveConflictError(ArchiveError):
    """A create or conditional update would overwrite unexpected content."""


def _split_frontmatter(text: str) -> tuple[list[str], str, bool]:
    clean = text.lstrip("\ufeff")
    lines = clean.splitlines()
    if not lines or lines[0].strip() != "---":
        return [], clean, False
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return lines[1:index], "\n".join(lines[index + 1 :]).lstrip("\n"), True
    return [], clean, False


def _scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return ""
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if value[0:1] in {'"', "'"} and value[-1:] == value[0]:
        return value[1:-1]
    if value[0:1] in {"[", "{"}:
        try:
            return json.loads(value.replace("'", '"'))
        except json.JSONDecodeError:
            if value.startswith("[") and value.endswith("]"):
                return [part.strip().strip("'\"") for part in value[1:-1].split(",") if part.strip()]
    return value


def _parse_frontmatter(lines: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    current: str | None = None
    for line in lines:
        item = re.match(r"^\s+-\s+(.*)$", line)
        if item and current:
            if not isinstance(result.get(current), list):
                result[current] = []
            result[current].append(_scalar(item.group(1)))
            continue
        field = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if field:
            current = field.group(1)
            result[current] = _scalar(field.group(2))
    return result


def _yaml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
    text = str(value)
    if not text or re.search(r"[:#\[\]{},]|^[-?!*&]", text):
        return json.dumps(text, ensure_ascii=False)
    return text


def _render(frontmatter: Mapping[str, Any], body: str) -> str:
    fields = "\n".join(f"{key}: {_yaml_value(value)}" for key, value in frontmatter.items())
    return f"---\n{fields}\n---\n\n{body.strip()}\n"


def _norm_link(link: str) -> str:
    target = link.split("|", 1)[0].split("#", 1)[0].strip().replace("\\", "/")
    return Path(target).stem.casefold()


def _ensure_ai_preamble(body: str) -> str:
    clean = body.strip()
    if "## For future Claude" in clean:
        return clean
    first = next((line.strip().lstrip("#").strip() for line in clean.splitlines() if line.strip()), "Archive entry")
    summary = first[:280]
    return f"## For future Claude\n{summary}\n\n{clean}"


_is_junction = getattr(os.path, "isjunction", lambda _path: False)


class Archive:
    """A bounded, local-only Markdown Archive rooted at ``vault``."""

    source_id = "archive"
    source_term = "Archive"

    def __init__(
        self,
        vault: str | os.PathLike[str],
        *,
        max_files: int = 10_000,
        max_file_bytes: int = 200_000,
        read_cap: int = 20_000,
    ) -> None:
        self.vault = Path(vault).expanduser().resolve()
        self.max_files = max(1, int(max_files))
        self.max_file_bytes = max(1, int(max_file_bytes))
        self.read_cap = max(1, int(read_cap))

    def init_vault(self, *, scoped: bool = False, container: bool = False) -> dict[str, Any]:
        """Initialize a minimal vault without replacing existing user files."""
        created: list[str] = []
        self.vault.mkdir(parents=True, exist_ok=True)
        folders = ("system",) if container else (("wiki", "system") if scoped else ("Inbox", "wiki", "system"))
        for folder in folders:
            path = self.vault / folder
            if not path.exists():
                path.mkdir()
                created.append(folder)
        index = self.vault / "system" / "index.md"
        if not index.exists():
            today = date.today().isoformat()
            index.write_text(
                _render(
                    {
                        "type": "index",
                        "date": today,
                        "tags": ["index", "archive"],
                        "ai-first": True,
                        "provenance": {"system": "holocore", "operation": "vault-init"},
                    },
                    "## For future Claude\nThis is the HoloCore Archive index. Link durable knowledge from here.\n",
                ),
                encoding="utf-8",
            )
            created.append("system/index.md")
        guide = self.vault / "README.md"
        if not scoped and not guide.exists():
            today = date.today().isoformat()
            guide.write_text(
                _render(
                    {
                        "type": "guide",
                        "date": today,
                        "tags": ["guide", "archive"],
                        "ai-first": True,
                        "provenance": {"system": "holocore", "operation": "vault-init"},
                    },
                    "## For future Claude\nThis visible folder is the HoloCore Archive and can be opened directly as an Obsidian vault.\n\n"
                    "# HoloCore Archive\n\n"
                    + ("- `Worlds/<project name>/wiki/` holds project knowledge.\n- `Shared/` holds cross-project knowledge.\n" if container else "- `Inbox/` holds notes waiting to be refined.\n- `wiki/` holds polished, verified Archive Entries.\n")
                    + "- `system/index.md` is the starting map.\n\n"
                    "Obsidian is optional. HoloCore and connected AI clients read these Markdown files directly.\n",
                ),
                encoding="utf-8",
            )
            created.append("README.md")
        return {"vault": str(self.vault), "created": created, "initialized": True}

    init = init_vault

    def _target(
        self,
        relative_path: str | os.PathLike[str],
        *,
        must_exist: bool = False,
        for_write: bool = False,
    ) -> Path:
        raw = Path(relative_path)
        if raw.is_absolute() or not raw.parts:
            raise PathTraversalError("path must be vault-relative")
        candidate = self.vault
        for part in raw.parts:
            candidate /= part
            if candidate.is_symlink():
                raise PathTraversalError("symlink paths are not allowed")
        target = (self.vault / raw).resolve()
        if target != self.vault and self.vault not in target.parents:
            raise PathTraversalError("path is outside the vault")
        relative = target.relative_to(self.vault)
        folded_parts = {part.casefold() for part in relative.parts}
        if folded_parts & SKIP_DIRS:
            raise PathTraversalError("path is in a protected directory")
        if for_write and folded_parts & PROTECTED_WRITE_DIRS:
            raise PathTraversalError("path is in a protected directory")
        if target.suffix.casefold() != ".md":
            raise ArchiveError("Archive notes must use the .md extension")
        if must_exist and (not target.is_file() or target.is_symlink()):
            raise FileNotFoundError(str(relative_path))
        return target

    def _notes(self) -> tuple[list[Path], bool]:
        if not self.vault.is_dir():
            return [], False
        notes: list[Path] = []
        for root, dirs, files in os.walk(self.vault, followlinks=False):
            # Windows junctions (e.g. the legacy Worlds alias) are not symlinks
            # but would still make every aliased note appear twice.
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not (Path(root) / d).is_symlink()
                       and not _is_junction(Path(root) / d)]
            for name in files:
                path = Path(root) / name
                if path.suffix.casefold() == ".md" and not path.is_symlink():
                    notes.append(path)
        notes.sort(key=lambda path: (-path.stat().st_mtime_ns, path.relative_to(self.vault).as_posix().casefold()))
        return notes[: self.max_files], len(notes) > self.max_files

    def list_notes(self, limit: int | None = None) -> list[dict[str, Any]]:
        """List notes newest first without reading their contents."""
        notes, _ = self._notes()
        return [{"path": path.relative_to(self.vault).as_posix(), "title": path.stem,
                 "modified": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()}
                for path in (notes if limit is None else notes[:limit])]

    def _read_text(self, path: Path, cap: int) -> str | None:
        try:
            if path.stat().st_size > cap:
                return None
            return path.read_text(encoding="utf-8-sig", errors="replace")
        except (OSError, UnicodeError):
            return None

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Return at most 20 ranked lexical matches from a bounded scan."""
        terms = [token.casefold() for token in TOKEN_RE.findall(query) if len(token) > 1]
        if not terms:
            return []
        limit = max(1, min(int(limit), 20))
        matches: list[tuple[float, str, dict[str, Any]]] = []
        notes, _ = self._notes()
        for path in notes:
            text = self._read_text(path, self.max_file_bytes)
            if not text:
                continue
            low, title = text.casefold(), path.stem.casefold()
            score = sum(low.count(term) + 5 * title.count(term) for term in terms)
            if not score:
                continue
            rel = path.relative_to(self.vault).as_posix()
            metadata = _parse_frontmatter(_split_frontmatter(text)[0])
            first = min((low.find(term) for term in terms if term in low), default=0)
            snippet = re.sub(r"\s+", " ", text[max(0, first - 80) : first + 240]).strip()
            matches.append(
                (
                    -float(score),
                    rel.casefold(),
                    {
                        "source_id": self.source_id,
                        "source_term": self.source_term,
                        "record_kind": "Archive Entry",
                        "record_id": rel,
                        "source_ref": rel,
                        "path": rel,
                        "title": path.stem,
                        "snippet": snippet,
                        "provenance": metadata.get("provenance", metadata.get("source")),
                        "ai_first": metadata.get("ai-first") is True,
                    },
                )
            )
        matches.sort(key=lambda item: (item[0], item[1]))
        return [item[2] for item in matches[:limit]]

    def read(self, relative_path: str | os.PathLike[str]) -> dict[str, Any]:
        target = self._target(relative_path, must_exist=True)
        try:
            with target.open("r", encoding="utf-8-sig", errors="replace") as handle:
                head = handle.read(max(self.read_cap, 16_384))
                truncated = len(head) > self.read_cap or bool(handle.read(1))
        except OSError as exc:
            raise ArchiveError("note is unreadable") from exc
        text = head[: self.read_cap]
        lines, _, _ = _split_frontmatter(head)
        rel = target.relative_to(self.vault).as_posix()
        metadata = _parse_frontmatter(lines)
        return {
            "source_id": self.source_id,
            "source_term": self.source_term,
            "record_kind": "Archive Entry",
            "record_id": rel,
            "source_ref": rel,
            "path": rel,
            "content": text[: self.read_cap],
            "truncated": truncated,
            "frontmatter": metadata,
            "provenance": metadata.get("provenance", metadata.get("source")),
        }

    def validate(self, note: str | os.PathLike[str]) -> dict[str, Any]:
        """Validate note text or a vault-relative note path against AI-first rules."""
        if isinstance(note, os.PathLike) or (isinstance(note, str) and "\n" not in note and note.endswith(".md")):
            target = self._target(note, must_exist=True)
            text = self._read_text(target, self.max_file_bytes)
            if text is None:
                raise ArchiveError("note is unreadable or exceeds the validation bound")
            path = str(note)
        else:
            text, path = str(note), None
        lines, _, had_frontmatter = _split_frontmatter(text)
        metadata = _parse_frontmatter(lines)
        issues: list[str] = []
        if not had_frontmatter:
            issues.append("missing frontmatter block")
        for key in REQUIRED_FRONTMATTER:
            if key not in metadata:
                issues.append(f"missing frontmatter key: {key}")
        if metadata.get("ai-first") is not True:
            issues.append("frontmatter ai-first must be true")
        note_type = str(metadata.get("type", ""))
        tags = metadata.get("tags", [])
        if note_type and (not isinstance(tags, list) or note_type not in [str(tag) for tag in tags]):
            issues.append("frontmatter tags must include the note type")
        try:
            date.fromisoformat(str(metadata.get("date", "")))
        except ValueError:
            issues.append("frontmatter date must use YYYY-MM-DD")
        if "## For future Claude" not in text:
            issues.append("missing '## For future Claude' preamble")
        return {"path": path, "ok": not issues, "issues": issues, "frontmatter": metadata}

    validate_note = validate

    def _atomic_write(self, target: Path, text: str) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def create(
        self,
        relative_path: str | os.PathLike[str],
        body: str,
        *,
        frontmatter: Mapping[str, Any] | None = None,
        provenance: Mapping[str, Any] | str | None = None,
    ) -> dict[str, Any]:
        target = self._target(relative_path, for_write=True)
        if target.exists():
            raise ArchiveConflictError(f"note already exists: {relative_path}")
        if frontmatter is None and body.lstrip("\ufeff").startswith("---"):
            text = body
        else:
            metadata = dict(frontmatter or {})
            note_type = str(metadata.setdefault("type", "note"))
            metadata.setdefault("date", date.today().isoformat())
            metadata.setdefault("tags", [note_type])
            metadata.setdefault("ai-first", True)
            metadata.setdefault("provenance", provenance or {"system": "holocore", "operation": "create"})
            text = _render(metadata, _ensure_ai_preamble(body))
        validation = self.validate(text)
        if not validation["ok"]:
            raise ArchiveValidationError(validation["issues"])
        self._atomic_write(target, text)
        rel = target.relative_to(self.vault).as_posix()
        return {"created": rel, "source_id": self.source_id, "source_ref": rel, "provenance": validation["frontmatter"].get("provenance")}

    create_note = create

    def update(
        self,
        relative_path: str | os.PathLike[str],
        *,
        body: str | None = None,
        append: str | None = None,
        set_fields: Mapping[str, Any] | None = None,
        expected_content: str | None = None,
    ) -> dict[str, Any]:
        target = self._target(relative_path, must_exist=True, for_write=True)
        existing = target.read_text(encoding="utf-8-sig")
        if expected_content is not None and existing != expected_content:
            raise ArchiveConflictError("note changed since it was read")
        lines, old_body, had_frontmatter = _split_frontmatter(existing)
        if not had_frontmatter:
            raise ArchiveValidationError(["missing frontmatter block"])
        metadata = _parse_frontmatter(lines)
        old_provenance = metadata.get("provenance", metadata.get("source"))
        metadata.update(dict(set_fields or {}))
        if old_provenance is not None:
            metadata["provenance" if "provenance" in metadata or "provenance" in _parse_frontmatter(lines) else "source"] = old_provenance
        metadata["updated"] = date.today().isoformat()
        new_body = old_body if body is None else body.strip()
        if append:
            new_body = new_body.rstrip() + "\n\n" + append.strip()
        if body is None and append is None and not set_fields:
            raise ArchiveError("nothing to update")
        text = _render(metadata, new_body)
        validation = self.validate(text)
        if not validation["ok"]:
            raise ArchiveValidationError(validation["issues"])
        self._atomic_write(target, text)
        rel = target.relative_to(self.vault).as_posix()
        return {"updated": rel, "source_id": self.source_id, "source_ref": rel, "provenance": old_provenance}

    update_note = update

    def _index(self, notes: list[Path]) -> dict[str, str]:
        index: dict[str, str] = {}
        for path in notes:
            rel = path.relative_to(self.vault).as_posix()
            index[path.stem.casefold()] = rel
            text = self._read_text(path, self.max_file_bytes) or ""
            metadata = _parse_frontmatter(_split_frontmatter(text)[0])
            aliases = metadata.get("aliases", [])
            if isinstance(aliases, str):
                aliases = [aliases]
            for alias in aliases:
                index[str(alias).casefold()] = rel
        return index

    def backlinks(self, target: str) -> dict[str, Any]:
        stem = _norm_link(target)
        if not stem:
            raise ArchiveError("target is required")
        notes, capped = self._notes()
        refs: list[str] = []
        for path in notes:
            text = self._read_text(path, self.max_file_bytes) or ""
            if path.stem.casefold() != stem and any(_norm_link(link) == stem for link in WIKILINK_RE.findall(text)):
                refs.append(path.relative_to(self.vault).as_posix())
        return {"target": stem, "count": len(refs), "backlinks": sorted(refs), "capped": capped}

    def health(self) -> dict[str, Any]:
        notes, capped = self._notes()
        index = self._index(notes)
        inbound: set[str] = set()
        outbound: set[str] = set()
        wanted: list[dict[str, str]] = []
        invalid: list[dict[str, Any]] = []
        missing_frontmatter: list[str] = []
        for path in notes:
            rel = path.relative_to(self.vault).as_posix()
            text = self._read_text(path, self.max_file_bytes) or ""
            if not _split_frontmatter(text)[2]:
                missing_frontmatter.append(rel)
            result = self.validate(text)
            if not result["ok"]:
                invalid.append({"path": rel, "issues": result["issues"]})
            links = WIKILINK_RE.findall(text)
            if links:
                outbound.add(path.stem.casefold())
            for link in links:
                norm = _norm_link(link)
                if norm in index:
                    inbound.add(Path(index[norm]).stem.casefold())
                elif norm:
                    wanted.append({"in": rel, "link": link})
        orphans = sorted(
            path.relative_to(self.vault).as_posix()
            for path in notes
            if path.stem.casefold() not in inbound and path.stem.casefold() not in outbound
        )
        return {
            "source_id": self.source_id,
            "vault": str(self.vault),
            "notes_scanned": len(notes),
            "capped": capped,
            "orphans": {"count": len(orphans), "sample": orphans[:10]},
            "wanted_notes": {"count": len(wanted), "sample": wanted[:10]},
            "missing_frontmatter": {"count": len(missing_frontmatter), "sample": missing_frontmatter[:10]},
            "invalid_ai_first": {"count": len(invalid), "sample": invalid[:10]},
            "healthy": not invalid and not wanted,
        }

    vault_health = health


def init_vault(vault: str | os.PathLike[str]) -> dict[str, Any]:
    return Archive(vault).init_vault()


initialize_vault = init_vault


def search(vault: str | os.PathLike[str], query: str, limit: int = 10, **bounds: Any) -> list[dict[str, Any]]:
    return Archive(vault, **bounds).search(query, limit)


def read_note(vault: str | os.PathLike[str], relative_path: str | os.PathLike[str], **bounds: Any) -> dict[str, Any]:
    return Archive(vault, **bounds).read(relative_path)


def create_note(
    vault: str | os.PathLike[str],
    relative_path: str | os.PathLike[str],
    body: str,
    **kwargs: Any,
) -> dict[str, Any]:
    return Archive(vault).create(relative_path, body, **kwargs)


def update_note(vault: str | os.PathLike[str], relative_path: str | os.PathLike[str], **kwargs: Any) -> dict[str, Any]:
    return Archive(vault).update(relative_path, **kwargs)


def validate_note(vault: str | os.PathLike[str], note: str | os.PathLike[str]) -> dict[str, Any]:
    return Archive(vault).validate(note)


def backlinks(vault: str | os.PathLike[str], target: str, **bounds: Any) -> dict[str, Any]:
    return Archive(vault, **bounds).backlinks(target)


def vault_health(vault: str | os.PathLike[str], **bounds: Any) -> dict[str, Any]:
    return Archive(vault, **bounds).health()


class ArchiveView:
    """The active World's view inside the single HoloCore Archive."""

    def __init__(self, world_vault: str | os.PathLike[str]):
        self.world = Archive(world_vault)
        self.vault = self.world.vault

    def init_vault(self) -> dict[str, Any]:
        report = {"world": self.world.init_vault(scoped=True), "initialized": True}
        report["vault"] = str(self.world.vault)
        report["created"] = report["world"].get("created", [])
        return report

    init = init_vault

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        hits = self.world.search(query, limit)
        for hit in hits:
            hit["archive_scope"] = "world"
        return hits[:limit]

    def _target_archive(self, path: str | os.PathLike[str]) -> tuple[Archive, str]:
        return self.world, Path(path).as_posix()

    def read(self, path):
        archive, relative = self._target_archive(path)
        return archive.read(relative)

    def create(self, path, body, **kwargs):
        archive, relative = self._target_archive(path)
        return archive.create(relative, body, **kwargs)

    def update(self, path, **kwargs):
        archive, relative = self._target_archive(path)
        return archive.update(relative, **kwargs)

    def health(self) -> dict[str, Any]:
        world = self.world.health()
        result = {"world": world, "healthy": world["healthy"]}
        result["vault"] = str(self.world.vault)
        return result
