from __future__ import annotations

import json
import os
import sqlite3
import sys
import zipfile
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from holocore import home_backup
from holocore.folder_layout import HomeLayout
from holocore.home import HomeManager
from holocore.home_backup import (BackupError, configure_schedule, create_home_backup, disable_schedule,
                                  preview_home_restore, prune_backups, restore_home_backup, run_scheduled_backup,
                                  schedule_status, scheduled_command)
from holocore.memories import MemoryStore


def _home(tmp_path: Path, name: str = "home") -> tuple[Path, str]:
    root = tmp_path / f"{name}-project"
    root.mkdir()
    manager = HomeManager(tmp_path / name)
    manager.register_world(root, import_archive=False)
    home = manager.home
    world = manager.list_worlds()["worlds"][0]["id"]
    layout = HomeLayout(home)
    (layout.project_knowledge(world) / "wiki").mkdir(parents=True, exist_ok=True)
    (layout.project_knowledge(world) / "wiki" / "decision.md").write_text("# Decision\nKeep backups", encoding="utf-8")
    (layout.chat_history / world).mkdir(parents=True, exist_ok=True)
    (layout.chat_history / world / "chat.json").write_text('{"messages": ["hello"]}', encoding="utf-8")
    sources = home / "Projects" / world / "Sources"
    sources.mkdir(parents=True, exist_ok=True)
    (sources / "spec.pdf").write_bytes(b"%PDF-1.4 fake")
    code_map = home / "Projects" / world / "CodeMap"
    code_map.mkdir(parents=True, exist_ok=True)
    (code_map / "graph.json").write_text("{}", encoding="utf-8")
    store = MemoryStore(layout.memories / "animus.db")
    store.create_world(world, "Original name")
    (layout.memories / "animus.db.old.bak").write_text("ignore me", encoding="utf-8")
    return home, world


def _world_name(home: Path, world: str) -> str:
    with closing(sqlite3.connect(HomeLayout(home).memories / "animus.db")) as db:
        return db.execute("SELECT display_name FROM worlds WHERE id=?", (world,)).fetchone()[0]


def test_backup_contains_everything_irreplaceable_and_nothing_rebuildable(tmp_path):
    home, world = _home(tmp_path)
    with closing(sqlite3.connect(HomeLayout(home).memories / "animus.db")) as writer:
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("UPDATE worlds SET display_name='Only in WAL' WHERE id=?", (world,))
        writer.commit()
        result = create_home_backup(home, tmp_path / "backups")
    assert set(result["categories"]) == {"registry", "memories", "knowledge", "chat_history", "projects"}
    with zipfile.ZipFile(result["backup"]) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("manifest.json"))
        assert "README.txt" in names and "home/worlds.json" in names
        assert f"home/Projects/{world}/Sources/spec.pdf" in names
        assert archive.getinfo(f"home/Projects/{world}/Sources/spec.pdf").compress_type == zipfile.ZIP_STORED
        assert not any("CodeMap" in name or name.endswith(".bak") for name in names)
        snapshot = tmp_path / "snapshot.db"
        snapshot.write_bytes(archive.read("home/Memories/animus.db"))
    with closing(sqlite3.connect(snapshot)) as db:
        assert db.execute("SELECT display_name FROM worlds").fetchone()[0] == "Only in WAL"
    assert manifest["database"] == "Memories/animus.db" and manifest["source_home"] == str(home)
    assert all(len(entry["sha256"]) == 64 for entry in manifest["files"])
    assert not list((tmp_path / "backups").glob(".*.partial"))
    preview = preview_home_restore(result["backup"], home)
    assert preview["valid"] and preview["issues"] == [] and preview["projects"] == [world]


def test_damaged_or_foreign_files_are_rejected(tmp_path):
    home, _world = _home(tmp_path)
    backup = Path(create_home_backup(home, tmp_path / "backups")["backup"])
    damaged = tmp_path / "damaged.zip"
    with zipfile.ZipFile(backup) as source, zipfile.ZipFile(damaged, "w") as target:
        for item in source.infolist():
            data = source.read(item)
            target.writestr(item, b"tampered" if item.filename.endswith("decision.md") else data)
    preview = preview_home_restore(damaged, home)
    assert not preview["valid"] and any("Damaged file" in issue for issue in preview["issues"])
    foreign = tmp_path / "foreign.zip"
    with zipfile.ZipFile(foreign, "w") as archive:
        archive.writestr("notes.txt", "hello")
    assert "not a HoloCore backup" in preview_home_restore(foreign, home)["issues"][0]
    (tmp_path / "plain.zip").write_text("not a zip", encoding="utf-8")
    assert not preview_home_restore(tmp_path / "plain.zip", home)["valid"]
    with pytest.raises(BackupError, match="cannot be restored"):
        restore_home_backup(damaged, home)


def test_unsafe_paths_in_a_manifest_are_rejected(tmp_path):
    home, _world = _home(tmp_path)
    backup = Path(create_home_backup(home, tmp_path / "backups")["backup"])
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(backup) as source, zipfile.ZipFile(evil, "w") as target:
        manifest = json.loads(source.read("manifest.json"))
        manifest["files"][0]["path"] = "../../outside.txt"
        for item in source.infolist():
            target.writestr(item, json.dumps(manifest) if item.filename == "manifest.json" else source.read(item))
    preview = preview_home_restore(evil, home)
    assert not preview["valid"] and "Unsafe path" in preview["issues"][0]


def test_restore_returns_the_home_to_the_backup_and_keeps_a_safety_copy(tmp_path):
    home, world = _home(tmp_path)
    note = HomeLayout(home).project_knowledge(world) / "wiki" / "decision.md"
    backup = create_home_backup(home, tmp_path / "backups")["backup"]
    note.write_text("# Decision\nChanged after the backup", encoding="utf-8")
    (note.parent / "later.md").write_text("# Later\nNew note", encoding="utf-8")
    with closing(sqlite3.connect(HomeLayout(home).memories / "animus.db")) as db:
        db.execute("UPDATE worlds SET display_name='Renamed later' WHERE id=?", (world,))
        db.commit()
    preview = preview_home_restore(backup, home)
    assert preview["valid"] and preview["restore_applied"] is False
    result = restore_home_backup(backup, home)
    assert note.read_text(encoding="utf-8") == "# Decision\nKeep backups"
    assert not (note.parent / "later.md").exists()
    assert _world_name(home, world) == "Original name"
    assert (home / "Projects" / world / "CodeMap" / "graph.json").is_file()  # Rebuildable data is left alone.
    assert Path(result["safety_backup"]).is_file() and "pre-restore" in result["safety_backup"]
    assert not any((home / ".holocore-restore").iterdir())
    if sys.platform == "win32":
        assert os.path.isjunction(home / "Knowledge" / "Worlds")  # Legacy alias recreated.
    restore_home_backup(result["safety_backup"], home)  # The safety backup undoes the restore.
    assert (note.parent / "later.md").is_file() and _world_name(home, world) == "Renamed later"


@pytest.mark.parametrize("fail_at", ["Knowledge", "Memories/ChatHistory", "database"])
def test_a_failed_restore_puts_everything_back(tmp_path, fail_at):
    home, world = _home(tmp_path)
    note = HomeLayout(home).project_knowledge(world) / "wiki" / "decision.md"
    backup = create_home_backup(home, tmp_path / "backups")["backup"]
    note.write_text("current", encoding="utf-8")
    with closing(sqlite3.connect(HomeLayout(home).memories / "animus.db")) as db:
        db.execute("UPDATE worlds SET display_name='Current' WHERE id=?", (world,))
        db.commit()

    def step(unit):
        if unit == fail_at:
            raise OSError("simulated interruption")
    with pytest.raises(BackupError, match="previous data was put back"):
        restore_home_backup(backup, home, _step=step)
    assert note.read_text(encoding="utf-8") == "current"
    assert _world_name(home, world) == "Current"
    assert (HomeLayout(home).chat_history / world / "chat.json").is_file()


def test_restore_while_a_connection_is_open(tmp_path):
    home, world = _home(tmp_path)
    backup = create_home_backup(home, tmp_path / "backups")["backup"]
    database = HomeLayout(home).memories / "animus.db"
    with closing(sqlite3.connect(database, timeout=30)) as open_connection:  # Like a running MCP server.
        open_connection.execute("UPDATE worlds SET display_name='Busy' WHERE id=?", (world,))
        open_connection.commit()
        restore_home_backup(backup, home)
        assert open_connection.execute("SELECT display_name FROM worlds").fetchone()[0] == "Original name"


def test_restore_into_a_home_at_another_path_updates_saved_paths(tmp_path):
    home, world = _home(tmp_path)
    backup = create_home_backup(home, tmp_path / "backups")["backup"]
    other = HomeManager(tmp_path / "moved").home
    other.mkdir()
    preview = preview_home_restore(backup, other)
    assert preview["valid"] and any("saved paths will be updated" in warning for warning in preview["warnings"])
    restore_home_backup(backup, other)
    registry = json.loads((other / "worlds.json").read_text(encoding="utf-8"))
    storage = registry["worlds"][0]["storage"]
    assert Path(storage) == other / "Projects" / world and str(home) not in json.dumps(registry)
    assert _world_name(other, world) == "Original name"


def test_newer_backup_formats_and_schemas_are_refused(tmp_path):
    home, _world = _home(tmp_path)
    backup = Path(create_home_backup(home, tmp_path / "backups")["backup"])
    future = tmp_path / "future.zip"
    with zipfile.ZipFile(backup) as source, zipfile.ZipFile(future, "w") as target:
        manifest = json.loads(source.read("manifest.json"))
        manifest.update(format_version=99, schema_version=99)
        for item in source.infolist():
            target.writestr(item, json.dumps(manifest) if item.filename == "manifest.json" else source.read(item))
    issues = preview_home_restore(future, home)["issues"]
    assert any("newer HoloCore" in issue for issue in issues) and any("schema v99" in issue for issue in issues)


def test_schedule_creates_a_windowed_task_and_records_runs(tmp_path, monkeypatch):
    home, _world = _home(tmp_path)
    calls = []

    def runner(arguments, **_kwargs):
        calls.append(arguments)
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    status = configure_schedule(home, folder=tmp_path / "scheduled", runner=runner)
    assert status["enabled"] and status["frequency"] == "weekly" and status["keep"] == 4
    created = calls[-1]
    assert created[:6] == ["schtasks", "/Create", "/TN", "HoloCore Backup", "/SC", "WEEKLY"]
    assert created[created.index("/D") + 1] == "SUN" and created[created.index("/ST") + 1] == "20:00"
    assert "--backup-scheduled" in created[created.index("/TR") + 1]
    configure_schedule(home, folder=tmp_path / "scheduled", frequency="daily", time="07:30", keep=2, runner=runner)
    assert "/D" not in calls[-1] and "DAILY" in calls[-1]
    for _ in range(3):
        assert run_scheduled_backup(home)["result"] == "succeeded"
    assert len(list((tmp_path / "scheduled").glob("*-scheduled.zip"))) == 2
    assert schedule_status(home)["last_run"]["bytes"] > 0
    assert disable_schedule(home, runner=runner)["enabled"] is False and calls[-1][1] == "/Delete"
    for bad in ({"frequency": "hourly"}, {"day": "XYZ"}, {"time": "25:00"}, {"keep": 0}):
        with pytest.raises(BackupError):
            configure_schedule(home, folder=tmp_path / "scheduled", runner=runner, **bad)
    failing = lambda arguments, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr="Access is denied.")
    with pytest.raises(BackupError, match="Access is denied"):
        configure_schedule(home, folder=tmp_path / "scheduled", runner=failing)


def test_a_failed_scheduled_run_is_recorded(tmp_path):
    home, _world = _home(tmp_path)
    outcome = run_scheduled_backup(home)
    assert outcome["result"] == "failed" and "No backup folder" in outcome["error"]
    assert schedule_status(home)["last_run"]["result"] == "failed"


def test_task_scheduler_entry_point_runs_a_backup(tmp_path):
    from holocore.setup_app import main as setup_main
    home, _world = _home(tmp_path)
    runner = lambda arguments, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr="")
    configure_schedule(home, folder=tmp_path / "scheduled", runner=runner)
    assert setup_main(["--backup-scheduled", "--home", str(home)]) == 0
    assert len(list((tmp_path / "scheduled").glob("*-scheduled.zip"))) == 1
    disable_schedule(home, runner=runner)
    (home / "Runtime" / "backups.json").write_text('{"folder": null}', encoding="utf-8")
    assert setup_main(["--backup-scheduled", "--home", str(home)]) == 1


def test_scheduled_command_prefers_the_windowed_setup(tmp_path, monkeypatch):
    engine = tmp_path / "holocore.exe"
    engine.write_bytes(b"")
    (tmp_path / "HoloCore Setup.exe").write_bytes(b"")
    monkeypatch.setattr(home_backup.sys, "executable", str(engine))
    assert scheduled_command(Path("C:/Home")).startswith(f'"{tmp_path / "HoloCore Setup.exe"}" --backup-scheduled')


def test_backups_in_the_same_second_never_overwrite_each_other(tmp_path, monkeypatch):
    home, _world = _home(tmp_path)
    fixed = home_backup._now()
    monkeypatch.setattr(home_backup, "_now", lambda: fixed)
    names = {create_home_backup(home, tmp_path / "backups")["backup"] for _ in range(3)}
    assert len(names) == 3 and len(list((tmp_path / "backups").glob("*.zip"))) == 3
    assert sorted(names) == sorted(names, key=lambda name: Path(name).stat().st_mtime_ns)


def test_prune_keeps_only_matching_labels(tmp_path):
    for stamp in ("20260101T000000Z", "20260102T000000Z", "20260103T000000Z"):
        (tmp_path / f"holocore-backup-{stamp}-scheduled.zip").write_bytes(b"")
    (tmp_path / "holocore-backup-20250101T000000Z-manual.zip").write_bytes(b"")
    removed = prune_backups(tmp_path, "scheduled", keep=1)
    assert len(removed) == 2 and (tmp_path / "holocore-backup-20260103T000000Z-scheduled.zip").exists()
    assert (tmp_path / "holocore-backup-20250101T000000Z-manual.zip").exists()


def test_cli_restore_needs_explicit_confirmation(tmp_path, monkeypatch, capsys):
    from holocore import cli
    home, world = _home(tmp_path)
    note = HomeLayout(home).project_knowledge(world) / "wiki" / "decision.md"
    backup = create_home_backup(home, tmp_path / "backups")["backup"]
    note.write_text("changed", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["holocore", "backup", "--home", str(home), "restore", backup])
    assert cli.main() == 0
    assert "Re-run with --yes" in capsys.readouterr().out and note.read_text(encoding="utf-8") == "changed"
    monkeypatch.setattr(sys, "argv", ["holocore", "backup", "--home", str(home), "restore", backup, "--yes"])
    assert cli.main() == 0
    assert note.read_text(encoding="utf-8") == "# Decision\nKeep backups"
