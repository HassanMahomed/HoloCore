"""Safe, local-first acquisition of raw files and URLs.

This module deliberately stops at acquisition and text extraction.  It does not
write to HoloCore's Knowledge, Code Map, or Memories.
"""

from __future__ import annotations

import hashlib
import importlib
import io
import json
import mimetypes
import os
import re
import tempfile
import threading
import zipfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


_SCHEMA_VERSION = 1
_URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_COMMON_EXCLUDES = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "graphify-out", "holocore-out",
    "node_modules",
    "venv",
}
_MEDIA_BY_SUFFIX = {
    ".c": "text/x-c",
    ".cc": "text/x-c++",
    ".cpp": "text/x-c++",
    ".cs": "text/x-csharp",
    ".css": "text/css",
    ".csv": "text/csv",
    ".go": "text/x-go",
    ".h": "text/x-c",
    ".hpp": "text/x-c++",
    ".htm": "text/html",
    ".html": "text/html",
    ".java": "text/x-java-source",
    ".js": "text/javascript",
    ".json": "application/json",
    ".jsx": "text/jsx",
    ".kt": "text/x-kotlin",
    ".log": "text/plain",
    ".lua": "text/x-lua",
    ".markdown": "text/markdown",
    ".md": "text/markdown",
    ".mjs": "text/javascript",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".epub": "application/epub+zip",
    ".rtf": "application/rtf",
    ".php": "text/x-php",
    ".ps1": "text/x-powershell",
    ".py": "text/x-python",
    ".rb": "text/x-ruby",
    ".rs": "text/x-rust",
    ".rst": "text/x-rst",
    ".sh": "text/x-shellscript",
    ".sql": "text/x-sql",
    ".toml": "application/toml",
    ".ts": "text/typescript",
    ".tsx": "text/tsx",
    ".txt": "text/plain",
    ".xml": "application/xml",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
}
_TEXT_APPLICATION_TYPES = {
    "application/json",
    "application/ld+json",
    "application/toml",
    "application/xml",
    "application/x-httpd-php",
    "application/x-javascript",
    "application/x-ndjson",
    "application/yaml",
}
_HTML_TYPES = {"application/xhtml+xml", "text/html"}
_OFFICE_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "word",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "slides",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "sheet",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_state() -> dict[str, Any]:
    return {"schema_version": _SCHEMA_VERSION, "records": {}, "sources": {}}


def _clean_content_type(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.split(";", 1)[0].strip().lower()
    return cleaned or None


def _media_type(name: str, supplied: str | None = None) -> str:
    declared = _clean_content_type(supplied)
    if declared and declared != "application/octet-stream":
        return declared
    suffix = Path(urllib.parse.unquote(name)).suffix.lower()
    if suffix in _MEDIA_BY_SUFFIX:
        return _MEDIA_BY_SUFFIX[suffix]
    guessed, _ = mimetypes.guess_type(name)
    return guessed or declared or "application/octet-stream"


def _storage_group(media_type: str) -> str:
    if media_type in _HTML_TYPES:
        return "html"
    if media_type == "application/pdf":
        return "pdf"
    if media_type in _OFFICE_TYPES or media_type in {"application/epub+zip", "application/rtf"}:
        return "documents"
    if media_type.startswith("image/"):
        return "images"
    if media_type.startswith("audio/"):
        return "audio"
    if media_type.startswith("video/"):
        return "video"
    if media_type.startswith("text/") or media_type in _TEXT_APPLICATION_TYPES:
        return "text"
    return "binary"


def _safe_suffix(name: str) -> str:
    suffix = Path(urllib.parse.unquote(name)).suffix.lower()
    if re.fullmatch(r"\.[a-z0-9][a-z0-9._+-]{0,14}", suffix):
        return suffix
    return ".bin"


def _fallback_title(source: str, name: str) -> str:
    if source.lower().startswith(("http://", "https://")):
        parsed = urllib.parse.urlsplit(source)
        leaf = Path(urllib.parse.unquote(parsed.path)).name
        return Path(leaf).stem if leaf else parsed.netloc
    stem = Path(name).stem
    return stem or Path(name).name


class _VisibleHTMLParser(HTMLParser):
    _HIDDEN = {"head", "noscript", "script", "style", "svg", "template"}
    _BLOCKS = {
        "article", "aside", "blockquote", "br", "div", "footer", "h1", "h2",
        "h3", "h4", "h5", "h6", "header", "li", "main", "nav", "p", "pre",
        "section", "table", "td", "th", "tr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._hidden_depth = 0
        self._title_depth = 0
        self.visible: list[str] = []
        self.title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        tag = tag.lower()
        if tag == "title":
            self._title_depth += 1
        if tag in self._HIDDEN:
            self._hidden_depth += 1
        elif tag in self._BLOCKS and self._hidden_depth == 0:
            self.visible.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title" and self._title_depth:
            self._title_depth -= 1
        if tag in self._HIDDEN and self._hidden_depth:
            self._hidden_depth -= 1
        elif tag in self._BLOCKS and self._hidden_depth == 0:
            self.visible.append("\n")

    def handle_data(self, data: str) -> None:
        if self._title_depth:
            self.title_parts.append(data)
        if self._hidden_depth == 0:
            self.visible.append(data)

    @staticmethod
    def _normalize(parts: list[str]) -> str:
        value = "".join(parts).replace("\r\n", "\n").replace("\r", "\n")
        value = re.sub(r"[^\S\n]+", " ", value)
        value = re.sub(r" *\n+ *", "\n", value)
        return value.strip()

    @property
    def text(self) -> str:
        return self._normalize(self.visible)

    @property
    def title(self) -> str:
        return " ".join("".join(self.title_parts).split())


class RawIngestor:
    """Acquire immutable raw sources and cache extraction results by SHA-256."""

    url_timeout = 15

    def __init__(
        self, raw_root: Path, state_path: Path, max_bytes: int = 20_000_000
    ) -> None:
        if isinstance(max_bytes, bool) or int(max_bytes) <= 0:
            raise ValueError("max_bytes must be a positive integer")
        self.raw_root = Path(raw_root).expanduser().resolve(strict=False)
        self.state_path = Path(state_path).expanduser().resolve(strict=False)
        self.max_bytes = int(max_bytes)
        self._lock = threading.RLock()

    def ingest(self, source: str | Path, *, title: str | None = None) -> dict[str, Any]:
        """Ingest one local file, directory tree, or HTTP(S) URL."""
        if isinstance(source, Path):
            return self._ingest_local(source, title=title)
        raw_source = os.fspath(source)
        if not raw_source.strip():
            raise ValueError("source must not be empty")
        if _URL_RE.match(raw_source):
            scheme = urllib.parse.urlsplit(raw_source).scheme.lower()
            if scheme not in {"http", "https"}:
                raise ValueError(f"Unsupported URL scheme: {scheme}")
            return self._ingest_url(raw_source, title=title)
        return self._ingest_local(Path(raw_source), title=title)

    def sync_inbox(self, inbox: Path) -> dict[str, Any]:
        """Recursively ingest visible files from a per-World inbox."""
        candidate = Path(inbox).expanduser()
        if candidate.is_symlink():
            return self._skipped_report(str(candidate.absolute()), candidate.name, "Symlink inboxes are not followed.")
        root = candidate.resolve(strict=True)
        if not root.is_dir():
            raise NotADirectoryError(str(root))
        return self._ingest_directory(root, title=root.name, operation="sync_inbox")

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return _empty_state()
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Cannot read ingestion state: {self.state_path}") from exc
        if not isinstance(value, dict):
            raise RuntimeError(f"Invalid ingestion state: {self.state_path}")
        records = value.get("records")
        sources = value.get("sources")
        if not isinstance(records, dict) or not isinstance(sources, dict):
            raise RuntimeError(f"Invalid ingestion state: {self.state_path}")
        return {
            "schema_version": _SCHEMA_VERSION,
            "records": records,
            "sources": sources,
        }

    def _save_state(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_name = ""
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.state_path.parent,
                prefix=f".{self.state_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
                json.dump(state, temporary, ensure_ascii=False, indent=2, sort_keys=True)
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, self.state_path)
        finally:
            if temporary_name:
                try:
                    Path(temporary_name).unlink(missing_ok=True)
                except OSError:
                    pass

    def _ingest_local(self, candidate: Path, *, title: str | None) -> dict[str, Any]:
        candidate = candidate.expanduser()
        if candidate.is_symlink():
            return self._skipped_report(
                str(candidate.absolute()), candidate.name, "Symlink sources are not followed."
            )
        path = candidate.resolve(strict=True)
        if path.is_dir():
            return self._ingest_directory(path, title=title or path.name, operation="ingest")
        if not path.is_file():
            raise ValueError(f"Source is not a regular file: {path}")
        return self._ingest_file(path, title=title)

    def _read_local(self, path: Path) -> bytes:
        size = path.stat().st_size
        if size > self.max_bytes:
            raise ValueError(f"Source exceeds max_bytes ({self.max_bytes}): {path}")
        with path.open("rb") as handle:
            data = handle.read(self.max_bytes + 1)
        if len(data) > self.max_bytes:
            raise ValueError(f"Source exceeds max_bytes ({self.max_bytes}): {path}")
        return data

    def _ingest_file(self, path: Path, *, title: str | None) -> dict[str, Any]:
        if path.is_symlink():
            return self._skipped_report(str(path), path.name, "Symlink sources are not followed.")
        stat = path.stat()
        data = self._read_local(path)
        source = str(path)
        key = f"file:{os.path.normcase(source)}"
        metadata = {
            "kind": "file",
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
        return self._ingest_bytes(
            data,
            source=source,
            source_key=key,
            name=path.name,
            title=title,
            declared_media_type=None,
            source_metadata=metadata,
        )

    @staticmethod
    def _header(headers: Any, name: str) -> str | None:
        if headers is None:
            return None
        try:
            value = headers.get(name)
        except (AttributeError, TypeError):
            return None
        return str(value) if value is not None else None

    def _cached_url_report(
        self, state: dict[str, Any], source: str, source_key: str, title: str | None
    ) -> dict[str, Any] | None:
        source_state = state["sources"].get(source_key)
        if not isinstance(source_state, dict):
            return None
        digest = source_state.get("content_hash")
        record = state["records"].get(digest)
        if not isinstance(digest, str) or not isinstance(record, dict):
            return None
        raw_path = self._record_path(record)
        if raw_path is None or not raw_path.is_file() or raw_path.is_symlink():
            return None
        name = Path(urllib.parse.unquote(urllib.parse.urlsplit(source).path)).name
        return self._report_from_record(
            source=source,
            digest=digest,
            record=record,
            title=title,
            fallback=_fallback_title(source, name),
            created=False,
            deduplicated=True,
        )

    def _ingest_url(self, source: str, *, title: str | None) -> dict[str, Any]:
        source_key = f"url:{source}"
        with self._lock:
            state = self._load_state()
            prior = state["sources"].get(source_key)
            validators: dict[str, str] = {}
            if isinstance(prior, dict):
                if prior.get("etag"):
                    validators["If-None-Match"] = str(prior["etag"])
                if prior.get("last_modified"):
                    validators["If-Modified-Since"] = str(prior["last_modified"])

        for attempt in range(2):
            request = urllib.request.Request(
                source,
                headers=validators if attempt == 0 else {},
            )
            try:
                response = urllib.request.urlopen(request, timeout=self.url_timeout)
            except urllib.error.HTTPError as exc:
                if exc.code == 304:
                    with self._lock:
                        cached = self._cached_url_report(
                            self._load_state(), source, source_key, title
                        )
                    if cached is not None:
                        return cached
                    continue
                raise

            with response:
                status = getattr(response, "status", None)
                if status is None:
                    try:
                        status = response.getcode()
                    except AttributeError:
                        status = None
                if status == 304:
                    with self._lock:
                        cached = self._cached_url_report(
                            self._load_state(), source, source_key, title
                        )
                    if cached is not None:
                        return cached
                    continue
                headers = getattr(response, "headers", None)
                content_length = self._header(headers, "Content-Length")
                if content_length:
                    try:
                        if int(content_length) > self.max_bytes:
                            raise ValueError(
                                f"Source exceeds max_bytes ({self.max_bytes}): {source}"
                            )
                    except ValueError as exc:
                        if "exceeds max_bytes" in str(exc):
                            raise
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = response.read(min(65_536, self.max_bytes - total + 1))
                    if not chunk:
                        break
                    if not isinstance(chunk, (bytes, bytearray)):
                        raise TypeError("HTTP response returned non-byte content")
                    total += len(chunk)
                    if total > self.max_bytes:
                        raise ValueError(
                            f"Source exceeds max_bytes ({self.max_bytes}): {source}"
                        )
                    chunks.append(bytes(chunk))
                data = b"".join(chunks)
                content_type = self._header(headers, "Content-Type")
                metadata = {
                    "kind": "url",
                    "etag": self._header(headers, "ETag"),
                    "last_modified": self._header(headers, "Last-Modified"),
                }
            name = Path(urllib.parse.unquote(urllib.parse.urlsplit(source).path)).name or "download"
            return self._ingest_bytes(
                data,
                source=source,
                source_key=source_key,
                name=name,
                title=title,
                declared_media_type=content_type,
                source_metadata=metadata,
            )
        raise RuntimeError(f"URL returned 304 without a usable cached record: {source}")

    def _record_path(self, record: dict[str, Any]) -> Path | None:
        relative = record.get("raw_path")
        if not isinstance(relative, str) or not relative:
            return None
        candidate = (self.raw_root / Path(relative)).resolve(strict=False)
        try:
            candidate.relative_to(self.raw_root)
        except ValueError:
            return None
        return candidate

    def _raw_path(self, digest: str, media_type: str, name: str) -> Path:
        return self.raw_root / _storage_group(media_type) / digest[:2] / f"{digest}{_safe_suffix(name)}"

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(65_536):
                digest.update(chunk)
        return digest.hexdigest()

    def _store_raw(self, path: Path, data: bytes, digest: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        resolved_parent = path.parent.resolve(strict=True)
        try:
            resolved_parent.relative_to(self.raw_root)
        except ValueError as exc:
            raise RuntimeError("Raw storage path escapes raw_root") from exc
        if path.is_symlink():
            raise RuntimeError(f"Refusing symlink at raw storage path: {path}")
        try:
            with path.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            if path.is_symlink() or not path.is_file() or self._hash_file(path) != digest:
                raise RuntimeError(f"Content-addressed raw path is not immutable: {path}")

    def _extract(self, data: bytes, media_type: str) -> dict[str, str | None]:
        if media_type == "application/pdf":
            return self._extract_pdf(data)
        if media_type in _OFFICE_TYPES:
            return self._extract_office(data, _OFFICE_TYPES[media_type])
        if media_type == "application/epub+zip":
            return self._extract_epub(data)
        if media_type == "application/rtf":
            return self._extract_rtf(data)
        if media_type in _HTML_TYPES:
            try:
                decoded = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                return {
                    "text": "",
                    "status": "extraction_failed",
                    "warning": "HTML is not valid UTF-8; raw file stored without extracted text.",
                    "embedded_title": None,
                }
            parser = _VisibleHTMLParser()
            try:
                parser.feed(decoded)
                parser.close()
            except Exception as exc:
                return {
                    "text": "",
                    "status": "extraction_failed",
                    "warning": f"HTML text extraction failed ({type(exc).__name__}); raw file stored.",
                    "embedded_title": None,
                }
            return {
                "text": parser.text,
                "status": "extracted",
                "warning": None,
                "embedded_title": parser.title or None,
            }
        if media_type.startswith("text/") or media_type in _TEXT_APPLICATION_TYPES:
            try:
                decoded = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                return {
                    "text": "",
                    "status": "extraction_failed",
                    "warning": "Text source is not valid UTF-8; raw file stored without extracted text.",
                    "embedded_title": None,
                }
            return {
                "text": decoded,
                "status": "extracted",
                "warning": None,
                "embedded_title": None,
            }
        kind = media_type.split("/", 1)[0]
        if kind in {"image", "audio", "video"}:
            return {
                "text": "",
                "status": "provider_required",
                "warning": (
                    f"{kind.capitalize()} extraction requires a capable configured provider; "
                    "raw file stored only."
                ),
                "embedded_title": None,
            }
        return {
            "text": "",
            "status": "unsupported",
            "warning": "No local text extractor is available for this media type; raw file stored only.",
            "embedded_title": None,
        }

    @staticmethod
    def _xml_text(payload: bytes) -> str:
        root = ElementTree.fromstring(payload)
        return " ".join(part.strip() for part in root.itertext() if part and part.strip())

    @classmethod
    def _extract_office(cls, data: bytes, kind: str) -> dict[str, str | None]:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = archive.namelist()
                if kind == "word":
                    selected = ["word/document.xml"]
                elif kind == "slides":
                    selected = sorted(name for name in names if name.startswith("ppt/slides/slide") and name.endswith(".xml"))
                else:
                    selected = [name for name in names if name.startswith("xl/worksheets/") and name.endswith(".xml")]
                text = "\n\n".join(cls._xml_text(archive.read(name)) for name in selected if name in names)
            return {"text": text, "status": "extracted" if text else "no_text", "warning": None if text else "Office file contained no extractable text.", "embedded_title": None}
        except Exception as exc:
            return {"text": "", "status": "extraction_failed", "warning": f"Office extraction failed ({type(exc).__name__}); raw file stored.", "embedded_title": None}

    @classmethod
    def _extract_epub(cls, data: bytes) -> dict[str, str | None]:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = sorted(name for name in archive.namelist() if name.lower().endswith((".xhtml", ".html", ".htm")))
                parts = []
                for name in names:
                    parser = _VisibleHTMLParser()
                    parser.feed(archive.read(name).decode("utf-8", errors="replace"))
                    parts.append(parser.text)
            text = "\n\n".join(part for part in parts if part)
            return {"text": text, "status": "extracted" if text else "no_text", "warning": None if text else "EPUB contained no extractable text.", "embedded_title": None}
        except Exception as exc:
            return {"text": "", "status": "extraction_failed", "warning": f"EPUB extraction failed ({type(exc).__name__}); raw file stored.", "embedded_title": None}

    @staticmethod
    def _extract_rtf(data: bytes) -> dict[str, str | None]:
        try:
            text = data.decode("utf-8", errors="replace")
            text = re.sub(r"\\'[0-9a-fA-F]{2}", "", text)
            text = re.sub(r"\\[a-z]+-?\d* ?", "", text)
            text = re.sub(r"[{}]", "", text).strip()
            return {"text": text, "status": "extracted" if text else "no_text", "warning": None if text else "RTF contained no extractable text.", "embedded_title": None}
        except Exception as exc:
            return {"text": "", "status": "extraction_failed", "warning": f"RTF extraction failed ({type(exc).__name__}); raw file stored.", "embedded_title": None}

    @staticmethod
    def _extract_pdf(data: bytes) -> dict[str, str | None]:
        try:
            pypdf = importlib.import_module("pypdf")
        except ImportError:
            return {
                "text": "",
                "status": "stored_without_extraction",
                "warning": "PDF text extraction unavailable; install pypdf to enable it. Raw PDF stored.",
                "embedded_title": None,
            }
        try:
            reader = pypdf.PdfReader(io.BytesIO(data))
            pages = [page.extract_text() or "" for page in reader.pages]
            text = "\n\n".join(part.strip() for part in pages if part.strip())
            metadata = getattr(reader, "metadata", None)
            embedded_title = getattr(metadata, "title", None) if metadata else None
            warning = None if text else "PDF contained no extractable text; raw PDF stored."
            return {
                "text": text,
                "status": "extracted" if text else "no_text",
                "warning": warning,
                "embedded_title": str(embedded_title).strip() if embedded_title else None,
            }
        except Exception as exc:
            return {
                "text": "",
                "status": "extraction_failed",
                "warning": f"PDF text extraction failed ({type(exc).__name__}); raw PDF stored.",
                "embedded_title": None,
            }

    def _ingest_bytes(
        self,
        data: bytes,
        *,
        source: str,
        source_key: str,
        name: str,
        title: str | None,
        declared_media_type: str | None,
        source_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        digest = hashlib.sha256(data).hexdigest()
        media_type = _media_type(name, declared_media_type)
        fallback = _fallback_title(source, name)
        with self._lock:
            state = self._load_state()
            record = state["records"].get(digest)
            if isinstance(record, dict):
                raw_path = self._record_path(record)
                if raw_path is not None and raw_path.is_file() and not raw_path.is_symlink():
                    state["sources"][source_key] = {
                        **source_metadata,
                        "content_hash": digest,
                        "updated_at": _utc_now(),
                    }
                    self._save_state(state)
                    return self._report_from_record(
                        source=source,
                        digest=digest,
                        record=record,
                        title=title,
                        fallback=fallback,
                        created=False,
                        deduplicated=True,
                    )

            raw_path = self._raw_path(digest, media_type, name)
            self._store_raw(raw_path, data, digest)
            extracted = self._extract(data, media_type)
            record = {
                "raw_path": raw_path.relative_to(self.raw_root).as_posix(),
                "media_type": media_type,
                "text": extracted["text"],
                "extraction_status": extracted["status"],
                "warning": extracted["warning"],
                "embedded_title": extracted["embedded_title"],
                "created_at": _utc_now(),
            }
            state["records"][digest] = record
            state["sources"][source_key] = {
                **source_metadata,
                "content_hash": digest,
                "updated_at": _utc_now(),
            }
            self._save_state(state)
            return self._report_from_record(
                source=source,
                digest=digest,
                record=record,
                title=title,
                fallback=fallback,
                created=True,
                deduplicated=False,
            )

    def _report_from_record(
        self,
        *,
        source: str,
        digest: str,
        record: dict[str, Any],
        title: str | None,
        fallback: str,
        created: bool,
        deduplicated: bool,
    ) -> dict[str, Any]:
        raw_path = self._record_path(record)
        warning = record.get("warning")
        result = {
            "source": source,
            "raw_path": str(raw_path) if raw_path is not None else None,
            "content_hash": digest,
            "media_type": str(record.get("media_type", "application/octet-stream")),
            "text": str(record.get("text", "")),
            "title": title or record.get("embedded_title") or fallback,
            "created": created,
            "deduplicated": deduplicated,
            "warning": str(warning) if warning else None,
            "extraction_status": str(record.get("extraction_status", "unknown")),
        }
        result["counts"] = self._single_counts(result)
        return result

    @staticmethod
    def _single_counts(report: dict[str, Any]) -> dict[str, int]:
        return {
            "discovered": 1,
            "total": 1,
            "processed": 1,
            "created": int(bool(report.get("created"))),
            "deduplicated": int(bool(report.get("deduplicated"))),
            "skipped": 0,
            "failed": 0,
            "warnings": int(bool(report.get("warning"))),
        }

    @staticmethod
    def _skipped_report(source: str, name: str, warning: str) -> dict[str, Any]:
        return {
            "source": source,
            "raw_path": None,
            "content_hash": None,
            "media_type": "application/octet-stream",
            "text": "",
            "title": Path(name).stem or name,
            "created": False,
            "deduplicated": False,
            "warning": warning,
            "extraction_status": "skipped",
            "counts": {
                "discovered": 1,
                "total": 0,
                "processed": 0,
                "created": 0,
                "deduplicated": 0,
                "skipped": 1,
                "failed": 0,
                "warnings": 1,
            },
        }

    def _is_internal_path(self, path: Path) -> bool:
        resolved = path.resolve(strict=False)
        if resolved == self.state_path:
            return True
        try:
            resolved.relative_to(self.raw_root)
            return True
        except ValueError:
            return False

    def _walk_files(
        self, root: Path
    ) -> tuple[list[Path], list[dict[str, str]], list[dict[str, str]]]:
        files: list[Path] = []
        skipped: list[dict[str, str]] = []
        errors: list[dict[str, str]] = []

        def on_error(exc: OSError) -> None:
            errors.append({"source": str(exc.filename or root), "error": str(exc)})

        for current, directories, names in os.walk(
            root, topdown=True, onerror=on_error, followlinks=False
        ):
            current_path = Path(current)
            kept_directories: list[str] = []
            for directory in sorted(directories):
                path = current_path / directory
                reason = ""
                if path.is_symlink():
                    reason = "symlink-directory"
                elif directory.startswith(".") or directory in _COMMON_EXCLUDES:
                    reason = "excluded-directory"
                elif self._is_internal_path(path):
                    reason = "ingestor-output"
                if reason:
                    skipped.append({"source": str(path), "reason": reason})
                else:
                    kept_directories.append(directory)
            directories[:] = kept_directories

            for name in sorted(names):
                path = current_path / name
                reason = ""
                if path.is_symlink():
                    reason = "symlink-file"
                elif name.startswith(".") or name in _COMMON_EXCLUDES:
                    reason = "excluded-file"
                elif self._is_internal_path(path):
                    reason = "ingestor-output"
                if reason:
                    skipped.append({"source": str(path), "reason": reason})
                else:
                    files.append(path)
        return files, skipped, errors

    def _ingest_directory(self, root: Path, *, title: str, operation: str) -> dict[str, Any]:
        files, skipped_items, errors = self._walk_files(root)
        items: list[dict[str, Any]] = []
        for path in files:
            try:
                items.append(self._ingest_file(path, title=None))
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                errors.append({"source": str(path), "error": f"{type(exc).__name__}: {exc}"})

        counts = {
            "discovered": len(files) + len(skipped_items),
            "total": len(items),
            "processed": len(items),
            "created": sum(int(item["created"]) for item in items),
            "deduplicated": sum(int(item["deduplicated"]) for item in items),
            "skipped": len(skipped_items),
            "failed": len(errors),
            "warnings": sum(int(bool(item.get("warning"))) for item in items),
        }
        warning = f"{len(errors)} source(s) failed; see errors." if errors else None
        result: dict[str, Any] = {
            "source": str(root),
            "raw_path": None,
            "content_hash": None,
            "media_type": "inode/directory",
            "text": "",
            "title": title,
            "created": counts["created"] > 0,
            "deduplicated": counts["created"] == 0 and counts["deduplicated"] > 0,
            "warning": warning,
            "extraction_status": "aggregate",
            "operation": operation,
            "items": items,
            "skipped_items": skipped_items,
            "errors": errors,
            "counts": counts,
            "created_count": counts["created"],
            "deduplicated_count": counts["deduplicated"],
            "skipped_count": counts["skipped"],
            "failed_count": counts["failed"],
            "warning_count": counts["warnings"],
        }
        return result


__all__ = ["RawIngestor"]
