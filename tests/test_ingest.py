from __future__ import annotations

import hashlib
import importlib
import io
import json
import os
import urllib.request
from pathlib import Path

import pytest

from holocore.engine import HoloCoreEngine
from holocore.ingest import RawIngestor


@pytest.fixture
def ingestor(tmp_path: Path) -> RawIngestor:
    return RawIngestor(tmp_path / "raw", tmp_path / "state" / "ingest.json")


def test_file_is_copied_content_addressed_and_deduplicated(
    tmp_path: Path, ingestor: RawIngestor
) -> None:
    source = tmp_path / "note.md"
    source.write_bytes(b"# Local\n\nHello")

    first = ingestor.ingest(source, title="My note")
    second = ingestor.ingest(source)

    expected_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    assert first["content_hash"] == expected_hash
    assert first["created"] is True
    assert first["deduplicated"] is False
    assert first["text"] == "# Local\n\nHello"
    assert first["title"] == "My note"
    assert Path(first["raw_path"]).read_bytes() == source.read_bytes()
    assert Path(first["raw_path"]).parts[-3] == "text"
    assert second["created"] is False
    assert second["deduplicated"] is True
    assert second["raw_path"] == first["raw_path"]
    assert source.exists()


def test_changed_same_path_creates_a_new_version(
    tmp_path: Path, ingestor: RawIngestor
) -> None:
    source = tmp_path / "changing.txt"
    source.write_text("v1", encoding="utf-8")
    first = ingestor.ingest(source)
    source.write_text("v2", encoding="utf-8")

    second = ingestor.ingest(source)
    state = json.loads(ingestor.state_path.read_text(encoding="utf-8"))

    assert second["created"] is True
    assert second["content_hash"] != first["content_hash"]
    assert Path(first["raw_path"]).read_text(encoding="utf-8") == "v1"
    assert Path(second["raw_path"]).read_text(encoding="utf-8") == "v2"
    assert len(state["records"]) == 2
    assert source.read_text(encoding="utf-8") == "v2"


def test_recursive_folder_ingests_supported_files_and_excludes_common_dirs(
    tmp_path: Path, ingestor: RawIngestor
) -> None:
    folder = tmp_path / "source"
    (folder / "nested").mkdir(parents=True)
    (folder / "nested" / "app.py").write_text("print('ok')", encoding="utf-8")
    (folder / "data.json").write_text('{"ok": true}', encoding="utf-8")
    (folder / ".git").mkdir()
    (folder / ".git" / "secret.txt").write_text("no", encoding="utf-8")

    report = ingestor.ingest(folder)
    again = ingestor.ingest(folder)

    assert report["counts"]["created"] == 2
    assert report["counts"]["skipped"] == 1
    assert {Path(item["source"]).name for item in report["items"]} == {"app.py", "data.json"}
    assert again["counts"]["created"] == 0
    assert again["counts"]["deduplicated"] == 2


class _Response(io.BytesIO):
    def __init__(self, body: bytes, headers: dict[str, str]) -> None:
        super().__init__(body)
        self.headers = headers
        self.status = 200


def test_html_url_uses_stdlib_timeout_extracts_visible_text_and_deduplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[urllib.request.Request, int]] = []
    body = (
        b"<html><head><title>Remote title</title><script>hidden()</script></head>"
        b"<body><h1>Hello</h1><p>Visible world</p></body></html>"
    )

    def fake_urlopen(request: urllib.request.Request, timeout: int) -> _Response:
        calls.append((request, timeout))
        return _Response(body, {"Content-Type": "text/html; charset=utf-8", "ETag": '"v1"'})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    ingestor = RawIngestor(tmp_path / "raw", tmp_path / "state.json")

    first = ingestor.ingest("https://example.test/page")
    second = ingestor.ingest("https://example.test/page")

    assert first["media_type"] == "text/html"
    assert first["title"] == "Remote title"
    assert "Hello" in first["text"] and "Visible world" in first["text"]
    assert "hidden" not in first["text"]
    assert second["deduplicated"] is True
    assert len(calls) == 2
    assert all(timeout == 15 for _, timeout in calls)
    assert calls[1][0].get_header("If-none-match") == '"v1"'


def test_pdf_without_pypdf_is_stored_with_clear_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "document.pdf"
    source.write_bytes(b"%PDF-1.4\nnot-a-real-pdf")
    real_import = importlib.import_module

    def fake_import(name: str, package: str | None = None):
        if name == "pypdf":
            raise ImportError("not installed")
        return real_import(name, package)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    ingestor = RawIngestor(tmp_path / "raw", tmp_path / "state.json")

    report = ingestor.ingest(source)

    assert Path(report["raw_path"]).read_bytes() == source.read_bytes()
    assert report["text"] == ""
    assert report["extraction_status"] == "stored_without_extraction"
    assert "install pypdf" in report["warning"]


def test_media_is_stored_and_requires_a_provider(tmp_path: Path) -> None:
    image = tmp_path / "pixel.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    ingestor = RawIngestor(tmp_path / "raw", tmp_path / "state.json")

    report = ingestor.ingest(image)

    assert report["media_type"] == "image/png"
    assert report["extraction_status"] == "provider_required"
    assert "configured provider" in report["warning"]
    assert Path(report["raw_path"]).exists()


def test_sync_inbox_is_recursive_visible_and_idempotent(tmp_path: Path) -> None:
    inbox = tmp_path / "Inbox"
    (inbox / "project").mkdir(parents=True)
    (inbox / "root.txt").write_text("root", encoding="utf-8")
    (inbox / "project" / "nested.md").write_text("nested", encoding="utf-8")
    (inbox / ".hidden.txt").write_text("hidden", encoding="utf-8")
    ingestor = RawIngestor(tmp_path / "raw", tmp_path / "state.json")

    first = ingestor.sync_inbox(inbox)
    second = ingestor.sync_inbox(inbox)

    assert first["operation"] == "sync_inbox"
    assert first["counts"]["created"] == 2
    assert first["counts"]["skipped"] == 1
    assert second["counts"]["created"] == 0
    assert second["counts"]["deduplicated"] == 2
    assert all(path.exists() for path in (inbox / "root.txt", inbox / "project" / "nested.md"))


def test_folder_does_not_follow_file_or_directory_symlinks(
    tmp_path: Path, ingestor: RawIngestor
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "safe.txt").write_text("safe", encoding="utf-8")
    try:
        (folder / "linked-file.txt").symlink_to(outside / "secret.txt")
        (folder / "linked-dir").symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    report = ingestor.ingest(folder)
    direct = ingestor.ingest(folder / "linked-file.txt")

    assert [Path(item["source"]).name for item in report["items"]] == ["safe.txt"]
    assert report["counts"]["skipped"] == 2
    assert "secret" not in "".join(item["text"] for item in report["items"])
    assert direct["extraction_status"] == "skipped"
    assert direct["raw_path"] is None


def test_max_size_is_enforced_for_files_and_streaming_urls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "large.txt"
    source.write_bytes(b"12345")
    ingestor = RawIngestor(tmp_path / "raw", tmp_path / "state.json", max_bytes=4)

    with pytest.raises(ValueError, match="max_bytes"):
        ingestor.ingest(source)

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda request, timeout: _Response(b"12345", {"Content-Type": "text/plain"}),
    )
    with pytest.raises(ValueError, match="max_bytes"):
        ingestor.ingest("https://example.test/large.txt")


def test_atomic_state_has_no_temporary_file_left_behind(
    tmp_path: Path, ingestor: RawIngestor
) -> None:
    source = tmp_path / "state.txt"
    source.write_text("state", encoding="utf-8")

    ingestor.ingest(source)

    state = json.loads(ingestor.state_path.read_text(encoding="utf-8"))
    assert state["schema_version"] == 1
    assert len(state["records"]) == 1
    assert not list(ingestor.state_path.parent.glob(f".{ingestor.state_path.name}.*.tmp"))


def test_engine_ingest_integrates_raw_memory_archive_and_graph(
    tmp_path: Path, isolated_holocore_home: Path
) -> None:
    world = tmp_path / "World"
    source = tmp_path / "decision.txt"
    source.write_text(
        "We decided that raw source ingestion must remain content-addressed and deduplicated.",
        encoding="utf-8",
    )
    engine = HoloCoreEngine(world)
    engine.setup(home=isolated_holocore_home, platforms=["generic"])

    first = engine.ingest_source(source)
    second = engine.ingest_source(source)

    assert first["integrated"][0]["memory_shards"]
    assert first["integrated"][0]["archive_entry"]["created"].startswith("wiki/sources/")
    assert Path(first["raw_path"]).is_file()
    assert Path(engine.router.config.atlas_graph_path).is_file()
    assert engine.router.config.atlas_graph_path.parent.name == "CodeMap"
    assert not (world / "holocore-out").exists()
    assert any(hit["source_file"].startswith(".knowledge/archive/") for hit in engine.router.atlas.search("decision"))
    assert second["deduplicated"] is True
    assert second["integrated"][0]["memory_shards"] == []
    assert second["integrated"][0]["archive_entry"]["deduplicated"] is True
    assert engine.router.animus.search(
        "content-addressed", world=engine.router.world_id, sector="sources"
    )


def test_search_automatically_syncs_visible_world_inbox_once(
    tmp_path: Path, isolated_holocore_home: Path
) -> None:
    world = tmp_path / "World"
    engine = HoloCoreEngine(world)
    setup = engine.setup(home=isolated_holocore_home, platforms=["generic"])
    inbox = Path(setup["paths"]["ingest_inbox"])
    (inbox / "brief.md").write_text(
        "# Release brief\n\nThe Atlas graph must refresh after source changes.", encoding="utf-8"
    )

    results = engine.search("release brief")

    assert any(result.source == "ARCHIVE" for result in results)
    assert list(Path(setup["paths"]["world_archive"]).glob("wiki/sources/*.md"))
