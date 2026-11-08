from __future__ import annotations

from pathlib import Path

from holocore.desktop_service import DesktopService
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
    assert "unverified" in connection["state"]


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
    assert preview["restore_applied"] is False
    assert preview["database"] == str(service.config.memory_path.resolve())
    assert Path(backup["backup"]).is_file()
