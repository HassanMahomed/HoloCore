from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest

from holocore.activity import ActivityLedger
from holocore.archive import Archive
from holocore.desktop_service import DesktopService, activity_label, default_project, is_noise, note_label
from holocore.folder_layout import HomeLayout
from holocore.home import HomeManager
from holocore.memories import MemoryStore


def test_desktop_does_not_initialize_unconfigured_project(tmp_path, monkeypatch):
    monkeypatch.setenv("HOLOCORE_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr("holocore.desktop_service.inspect_client_connections", lambda: {})
    root = tmp_path / "project"
    root.mkdir()
    service = DesktopService(root)
    result = service.overview()
    assert not result["memory_ready"]
    assert result["storage"]["exists"] is False
    assert service.search_knowledge("anything") == []
    assert list(root.iterdir()) == []
    assert not (tmp_path / "config").exists()


def test_desktop_reports_configuration_without_claiming_runtime_health(tmp_path, monkeypatch):
    monkeypatch.setenv("HOLOCORE_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr("holocore.desktop_service.inspect_client_connections", lambda: {
        "codex": {"connected": True, "state": "connected", "detail": "Entry found", "config_path": "config.toml"}
    })
    connection = DesktopService(tmp_path).connections()[0]
    assert connection["configured"] is True
    assert connection["runtime_verified"] is False
    assert connection["last_capture"] is None
    assert connection["configuration"] == "Configured"


def test_explicit_home_search_and_backup_are_scoped(tmp_path, monkeypatch):
    monkeypatch.setenv("HOLOCORE_CONFIG_HOME", str(tmp_path / "pointer"))
    monkeypatch.setattr("holocore.desktop_service.inspect_client_connections", lambda: {})
    root = tmp_path / "project"
    root.mkdir()
    manager = HomeManager(tmp_path / "chosen-home")
    manager.register_world(root, import_archive=False)
    service = DesktopService(root, manager.home)
    service.config.vault.mkdir(parents=True, exist_ok=True)
    (service.config.vault / "decision.md").write_text("# Storage decision\nUse bounded backups", encoding="utf-8")
    assert service.search_knowledge("bounded")[0]["path"] == "decision.md"
    assert service.projects()[0]["root"] == str(root.resolve())
    MemoryStore(service.config.memory_path).create_world(service.config.world_id)
    backup = service.backup(tmp_path / "backups")
    preview = service.restore_preview(backup["backup"])
    assert preview["valid"] and preview["restore_applied"] is False
    assert preview["destination"] == str(manager.home.resolve())
    assert backup["categories"]["memories"]["files"] == 1 and "knowledge" in backup["categories"]
    assert Path(backup["backup"]).is_file()


def _home_service(tmp_path, monkeypatch):
    monkeypatch.setenv("HOLOCORE_CONFIG_HOME", str(tmp_path / "pointer"))
    monkeypatch.setattr("holocore.desktop_service.inspect_client_connections", lambda: {})
    root = tmp_path / "project"
    root.mkdir()
    manager = HomeManager(tmp_path / "home")
    manager.register_world(root, import_archive=False)
    return DesktopService(root, manager.home)


def test_note_and_activity_labels_prefer_readable_text(tmp_path):
    memory = tmp_path / "2026-11-08-abc123.md"
    memory.write_text("---\ntype: note\n---\n# Conversation memory\n\n## Summary\n- Chose SQLite backups\n", encoding="utf-8")
    assert note_label(memory, memory.stem) == "Chose SQLite backups"
    heading = tmp_path / "plain.md"
    heading.write_text("# Storage decision\nBody", encoding="utf-8")
    assert note_label(heading, "plain") == "Storage decision"
    assert note_label(tmp_path / "missing.md", "fallback") == "fallback"
    assert activity_label({"title": "Conversation: x.jsonl", "content": "Fixed the bug\n\nTranscript:\nuser: hi"}) == "Fixed the bug"
    assert activity_label({"title": "Conversation: x.jsonl", "content": ""}) == "Conversation: x.jsonl"


def test_knowledge_sources_cover_project_second_brain_and_home(tmp_path, monkeypatch):
    service = _home_service(tmp_path, monkeypatch)
    layout = HomeLayout(service.config.home)
    (service.config.vault / "wiki").mkdir(parents=True, exist_ok=True)
    (service.config.vault / "wiki" / "a.md").write_text("# Project note\nbackup plan", encoding="utf-8")
    brain = layout.project_knowledge("Second Brain") / "wiki"
    brain.mkdir(parents=True)
    (brain / "b.md").write_text("# Brain note\nbackup ideas", encoding="utf-8")
    assert "Project note" in [row["label"] for row in service.notes("project")]
    assert "Brain note" not in [row["label"] for row in service.notes("project")]
    assert [row["label"] for row in service.notes("second_brain")] == ["Brain note"]
    assert {row["label"] for row in service.notes("home")} >= {"Project note", "Brain note"}
    assert service.search_knowledge("backup", source="second_brain")[0]["label"] == "Brain note"
    assert "backup ideas" in service.read_note("wiki/b.md", "second_brain")["content"]


def test_memories_timeline_and_summary_are_read_only_and_scoped(tmp_path, monkeypatch):
    service = _home_service(tmp_path, monkeypatch)
    assert service.memories() == [] and service.timeline() == [] and service.memory_kinds() == []
    assert not service.config.memory_path.exists()
    store = MemoryStore(service.config.memory_path)
    store.create_world(service.world)
    store.create_world("other")
    with closing(sqlite3.connect(service.config.memory_path)) as db:
        for shard, world, kind, text in (("1", service.world, "fact", "Use 100% bounded_backups"),
                                         ("2", service.world, "summary", "Weekly summary"),
                                         ("3", "other", "fact", "Other project bounded")):
            db.execute("INSERT INTO shards VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (shard, world, "conversations", text, shard, "", "[]", "{}",
                        json.dumps({"memory_kind": kind}), f"2026-11-0{shard}T00:00:00+00:00", "x"))
        db.execute("INSERT INTO diary(id, world_id, sector_id, occurred_at, title, content, kind, metadata, created_at) "
                   "VALUES('d', ?, '', '2026-11-08T00:00:00+00:00', 'Conversation: a.jsonl', ?, 'conversation', '{}', 'x')",
                   (service.world, "Did a thing\n\nTranscript:\n..."))
        db.commit()
    before = service.config.memory_path.read_bytes()
    assert service.memory_kinds() == ["fact", "summary"]
    assert [row["id"] for row in service.memories("bounded")] == ["1"]
    assert [row["id"] for row in service.memories("100%")] == ["1"]
    assert service.memories("1_0") == []  # LIKE wildcards are escaped.
    assert [row["id"] for row in service.memories(kind="summary")] == ["2"]
    assert service.timeline()[0]["label"] == "Did a thing"
    summary = service.home_summary()
    assert summary["memories"] == 3 and summary["project_memories"] == 2
    assert summary["projects"][0]["memories"] == 2
    assert service.config.memory_path.read_bytes() == before


def test_code_map_summary(tmp_path, monkeypatch):
    service = _home_service(tmp_path, monkeypatch)
    assert service.code_map()["exists"] is False
    service.config.atlas_graph.parent.mkdir(parents=True, exist_ok=True)
    service.config.atlas_graph.write_text(json.dumps({"nodes": [
        {"id": "file:a.py", "kind": "file", "label": "a.py", "source_file": "a.py"},
        {"id": "fn:a", "kind": "function", "label": "a()", "source_file": "a.py"},
        {"id": "fn:b", "kind": "function", "label": "b()", "source_file": "a.py"}],
        "links": [{"source": "file:a.py", "target": "fn:a"}, {"source": "file:a.py", "target": "fn:b"}]}), encoding="utf-8")
    result = service.code_map()
    assert (result["signals"], result["links"], result["files"]) == (3, 2, 1)
    assert result["kinds"][0] == ("function", 2)
    assert result["most_connected"][0]["label"] == "a.py" and result["most_connected"][0]["links"] == 2


def test_noise_filter_hides_fragments_but_keeps_real_memories(tmp_path, monkeypatch):
    for text in ("MB", "holocore desktop", "```bash", "[Image: original 2169x1399, displayed at 2000x1290]",
                 "<filesystem><root>C:\\x</root></filesystem>", "   "):
        assert is_noise(text), text
    for text in ("Each test takes a couple of seconds.", "Chose SQLite snapshot backups for v1.1"):
        assert not is_noise(text), text
    service = _home_service(tmp_path, monkeypatch)
    store = MemoryStore(service.config.memory_path)
    store.create_world(service.world)
    with closing(sqlite3.connect(service.config.memory_path)) as db:
        for shard, text in (("1", "MB"), ("2", "Chose SQLite snapshot backups for v1.1"), ("3", "```bash")):
            db.execute("INSERT INTO shards VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (shard, service.world, "conversations", text, shard, "", "[]", "{}",
                        json.dumps({"memory_kind": "fact"}), f"2026-11-0{shard}T00:00:00+00:00", "x"))
        for diary, text in (("a", "[Image: original 10x10]"), ("b", "Fixed the capture bug for Codex")):
            db.execute("INSERT INTO diary(id, world_id, sector_id, occurred_at, title, content, kind, metadata, created_at) "
                       "VALUES(?, ?, '', ?, 'Conversation: a.jsonl', ?, 'conversation', '{}', 'x')",
                       (diary, service.world, f"2026-11-08T0{ord(diary) - 96}:00:00+00:00", text))
        db.commit()
    assert len(service.memories()) == 3
    assert [row["id"] for row in service.memories(hide_noise=True)] == ["2"]
    assert [row["label"] for row in service.timeline(hide_noise=True)] == ["Fixed the capture bug for Codex"]


def test_code_map_most_connected_lists_only_code(tmp_path, monkeypatch):
    service = _home_service(tmp_path, monkeypatch)
    service.config.atlas_graph.parent.mkdir(parents=True, exist_ok=True)
    nodes = [{"id": "note", "kind": "archive_entry", "label": "Conversation memory"},
             {"id": "fn", "kind": "function", "label": "run()", "source_file": "a.py"}]
    links = [{"source": "note", "target": f"fact{i}"} for i in range(5)] + [{"source": "fn", "target": "note"}]
    service.config.atlas_graph.write_text(json.dumps({"nodes": nodes, "links": links}), encoding="utf-8")
    result = service.code_map()
    assert [row["label"] for row in result["most_connected"]] == ["run()"]
    assert result["code_signals"] == 1


def test_start_menu_launch_opens_most_recent_or_containing_project(tmp_path, monkeypatch):
    monkeypatch.setenv("HOLOCORE_CONFIG_HOME", str(tmp_path / "pointer"))
    manager = HomeManager(tmp_path / "home")
    older, newer = tmp_path / "older", tmp_path / "newer"
    for root in (older, newer):
        root.mkdir()
        manager.register_world(root, import_archive=False)
    ids = {Path(row["root"]).resolve(): row["id"] for row in manager.list_worlds()["worlds"]}
    ledger = ActivityLedger(HomeLayout(manager.home).memories / "animus.db")
    ledger.record_hook({"hook_event_name": "Stop", "session_id": "1"}, client="codex", project_id=ids[older.resolve()])
    ledger.record_hook({"hook_event_name": "Stop", "session_id": "2"}, client="codex", project_id=ids[newer.resolve()])
    assert default_project(manager.home, cwd=tmp_path) == newer.resolve()
    (older / "src").mkdir()
    assert default_project(manager.home, cwd=older / "src") == older.resolve()


def test_preview_wording_never_implies_a_restore_happened():
    from holocore.desktop_app import describe_preview
    valid = {"valid": True, "issues": [], "warnings": ["Memories recorded after this backup was made will be replaced."],
             "created_at": "2026-11-08T10:00:00+00:00", "holocore_version": "1.0.1", "source_home": "C:/Home",
             "categories": {"knowledge": {"label": "Knowledge notes", "files": 3, "bytes": 2048}},
             "projects": ["HoloCore"], "not_included": ["Code maps (rebuilt automatically)"]}
    text = describe_preview(valid)
    assert "Every file's checksum matches." in text and "Knowledge notes: 3 files" in text
    assert "will be replaced" in text and text.endswith("Nothing has been restored.")
    broken = describe_preview({"valid": False, "issues": ["This file is not a HoloCore backup"], "warnings": []})
    assert "can't be used" in broken and "not a HoloCore backup" in broken


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junctions")
def test_archive_does_not_double_count_junction_aliases(tmp_path):
    import _winapi
    (tmp_path / "Projects" / "p").mkdir(parents=True)
    (tmp_path / "Projects" / "p" / "note.md").write_text("# Note\nalias", encoding="utf-8")
    _winapi.CreateJunction(str(tmp_path / "Projects"), str(tmp_path / "Worlds"))
    assert [row["path"] for row in Archive(tmp_path).list_notes()] == ["Projects/p/note.md"]
    assert len(Archive(tmp_path).search("alias")) == 1


def test_control_centre_renders_service_data():
    tk = pytest.importorskip("tkinter")
    from holocore.desktop_app import DesktopControlCentre
    try:
        window = tk.Tk()
    except tk.TclError:
        pytest.skip("No display available")
    window.withdraw()
    app = DesktopControlCentre(window, Path.cwd(), autoload=False)
    try:
        app.show_dashboard({
            "projects": [], "database": "memories.db", "storage": {"exists": False},
            "connections": [{"name": "claude", "configuration": "Configured", "configured": True,
                             "last_capture": None, "detail": "Entry found", "config_path": "x"}],
            "timeline": [{"occurred_at": "2026-11-08T10:00:00+00:00", "label": "Fixed \U0001F600 the bug"}],
            "summary": {"projects": [{"id": app.service.world, "name": "Demo", "root": "C:/demo", "available": True,
                                      "notes": 4, "memories": 9, "last_activity": None}],
                        "knowledge_notes": 12, "second_brain_notes": 3, "memories": 40, "project_memories": 9}})
        assert app.home_cards["knowledge"][0].get() == "12"
        assert app.home_cards["memories"][1].get() == f"9 in {app.service.world}"
        assert app.home_cards["connections"][1].get() == "No capture recorded for this project"
        assert len(app.activity_table.get_children()) == 1 and len(app.project_table.get_children()) == 1
        app.show_memories(["fact"], [{"id": "m1", "kind": "fact", "created_at": "2026-11-08T10:00:00+00:00",
                                      "content": "Remember this", "sector_id": "conversations"}])
        assert app.memory_kind.cget("values")[1] == "fact"
        window.update()  # Tk delivers the row-selection event on the next loop pass.
        assert "Remember this" in app.memory_preview.get("1.0", "end")
        app.show_code_map({"exists": False})
        assert app.code_cards["signals"][0].get() == "—"
        app.show_schedule({"enabled": False, "folder": None, "frequency": "weekly", "day": "SUN", "time": "20:00",
                           "keep": 4, "last_run": None})
        assert app.schedule_label.cget("text").startswith("Off") and app.storage_cards["last_backup"][0].get() == "Never"
        assert str(app.schedule_off_button.cget("state")) == "disabled"
        app.show_schedule({"enabled": True, "folder": "D:/Backups", "frequency": "weekly", "day": "SUN", "time": "20:00",
                           "keep": 4, "last_run": {"result": "failed", "finished_at": "2026-11-08T10:00:00+00:00",
                                                   "error": "Disk full"}})
        assert "every Sunday at 20:00" in app.schedule_label.cget("text")
        assert app.storage_cards["last_backup"][0].get() == "Failed" and "Disk full" in app.storage_cards["last_backup"][1].get()
        assert str(app.schedule_off_button.cget("state")) == "normal"
        for page in ("home", "projects", "connections", "storage"):
            app.show_page(page)
        assert app.title_var.get() == "Storage & Backups"
    finally:
        app.close()
