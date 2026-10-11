from __future__ import annotations

from holocore import install


def test_client_detection_uses_executable_or_config_evidence(tmp_path, monkeypatch):
    claude_config = tmp_path / "claude"
    claude_config.mkdir()
    monkeypatch.setattr(
        install,
        "CLIENT_PROBES",
        {
            "claude": ("claude", claude_config),
            "codex": ("codex", tmp_path / "missing-codex"),
            "cursor": ("cursor", tmp_path / "missing-cursor"),
        },
    )
    monkeypatch.setattr(install.shutil, "which", lambda name: "C:/Tools/codex.exe" if name == "codex" else None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))

    detected = install.detect_installed_clients()
    assert detected["claude"]["installed"] is True
    assert detected["codex"]["installed"] is True
    assert detected["cursor"]["installed"] is False
    assert str(claude_config) in detected["claude"]["evidence"]
    assert "C:\\Tools\\codex.exe" in detected["codex"]["evidence"]
