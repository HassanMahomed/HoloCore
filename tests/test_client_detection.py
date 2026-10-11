from __future__ import annotations

import json

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


def test_connection_inspection_classifies_all_supported_clients(tmp_path, monkeypatch):
    root = tmp_path / "clients"
    monkeypatch.setenv("HOLOCORE_CLIENT_CONFIG_ROOT", str(root))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr(
        install,
        "detect_installed_clients",
        lambda: {
            name: {"installed": name != "opencode", "evidence": [f"{name}.exe"] if name != "opencode" else []}
            for name in ("claude", "codex", "cursor", "gemini", "opencode")
        },
    )
    (root / ".claude.json").parent.mkdir(parents=True, exist_ok=True)
    (root / ".claude.json").write_text(json.dumps({"mcpServers": {"holocore": {"command": "holocore"}}}), encoding="utf-8")
    (root / ".codex").mkdir()
    (root / ".codex" / "config.toml").write_text('[mcp_servers.holocore]\ncommand = "holocore"\n', encoding="utf-8")
    (root / ".cursor").mkdir()
    (root / ".cursor" / "mcp.json").write_text("{invalid", encoding="utf-8")
    (root / ".gemini").mkdir()
    (root / ".gemini" / "settings.json").write_text(json.dumps({"mcpServers": {"other": {}}}), encoding="utf-8")

    status = install.inspect_client_connections()

    assert status["claude"]["state"] == "connected"
    assert status["codex"]["state"] == "connected"
    assert status["cursor"]["state"] == "invalid-config"
    assert status["gemini"]["state"] == "needs-connection"
    assert status["opencode"]["state"] == "not-installed"
    assert all("config_path" in value for value in status.values())
