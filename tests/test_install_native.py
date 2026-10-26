from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from unittest.mock import patch

from holocore.install import bootstrap_project
from holocore.animus import Animus
from holocore.config import Config
from holocore.engine import HoloCoreEngine
from holocore.layout import integration_status, world_paths


def test_bootstrap_creates_native_files_for_all_clients(
    tmp_path: Path, isolated_holocore_home: Path
) -> None:
    root = tmp_path / "World with spaces"
    report = bootstrap_project(root, home=isolated_holocore_home)

    project_entries = {path.name for path in root.iterdir()}
    assert project_entries <= {".git"}
    assert not (root / "Archive").exists()
    world_storage = isolated_holocore_home / "Projects" / report.world_id
    config = json.loads((world_storage / "world.json").read_text(encoding="utf-8"))
    world_archive = isolated_holocore_home / "Knowledge" / "Projects" / report.world_id
    assert config == {
        "version": 5,
        "world": root.name,
        "world_id": report.world_id,
        "root": str(root.resolve()),
        "home": str(isolated_holocore_home),
        "archive": str(world_archive),
        "state_dir": str(world_storage / "Runtime"),
        "animus": str(isolated_holocore_home / "Memories" / "animus.db"),
        "raw_chats": str(isolated_holocore_home / "Memories" / "ChatHistory" / report.world_id),
        "atlas_graph": str(world_storage / "CodeMap" / "graph.json"),
        "llm": {"provider": "local"},
    }
    paths = world_paths(root)
    assert paths["home"] == str(isolated_holocore_home)
    assert paths["archive"] == str(isolated_holocore_home / "Knowledge")
    assert paths["world_archive"] == str(world_archive)
    assert "shared_archive" not in paths
    assert paths["memory_shards"] == str(isolated_holocore_home / "Memories" / "animus.db")
    assert paths["raw_chats"] == str(isolated_holocore_home / "Memories" / "ChatHistory" / report.world_id)
    connections = isolated_holocore_home / "Connections"
    assert json.loads((connections / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["holocore"]["cwd"] == str(isolated_holocore_home)
    codex = tomllib.loads((connections / ".codex/config.toml").read_text(encoding="utf-8"))
    assert codex["mcp_servers"]["holocore"]["args"] == ["-m", "holocore.mcp_server"]
    assert json.loads((connections / "opencode.json").read_text(encoding="utf-8"))["mcp"]["holocore"]["type"] == "local"
    client_root = Path(os.environ["HOLOCORE_CONFIG_HOME"]) / "clients"
    assert "holocore" in tomllib.loads((client_root / ".codex/config.toml").read_text(encoding="utf-8"))["mcp_servers"]
    assert "holocore" in json.loads((client_root / ".claude.json").read_text(encoding="utf-8"))["mcpServers"]
    assert "holocore" in json.loads((client_root / ".cursor/mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert "holocore" in json.loads((client_root / ".gemini/settings.json").read_text(encoding="utf-8"))["mcpServers"]
    assert "holocore" in json.loads((client_root / ".config/opencode/opencode.json").read_text(encoding="utf-8"))["mcp"]
    policy = (connections / "policy.md").read_text(encoding="utf-8")
    assert "Atlas first" in policy
    assert "Archive second" in policy
    assert "Animus third" in policy
    assert "Never route a HoloCore search back into itself" in policy
    assert "**Archive** = verified knowledge" in policy
    assert "**Constellation** = group of related mapped things" in policy
    assert "C:\\Users" not in policy
    assert HoloCoreEngine(root).status()["readiness"] == {"ready": True, "missing": []}

    codex_hooks = json.loads((connections / ".codex/hooks.json").read_text(encoding="utf-8"))
    claude_hooks = json.loads((connections / ".claude/settings.json").read_text(encoding="utf-8"))
    assert "holocore.capture_hook --client codex" in json.dumps(codex_hooks["hooks"]["Stop"])
    assert "PreToolUse" in codex_hooks["hooks"]
    assert "SubagentStart" in codex_hooks["hooks"]
    assert "holocore.capture_hook --client claude" in json.dumps(
        claude_hooks["hooks"]["SessionEnd"]
    )
    assert "PreToolUse" in claude_hooks["hooks"]
    assert "SubagentStart" in claude_hooks["hooks"]


def test_bootstrap_merges_without_replacing_user_instructions(tmp_path: Path) -> None:
    root = tmp_path / "world"
    root.mkdir()
    agents = root / "AGENTS.md"
    agents.write_text("user owned\n", encoding="utf-8")

    first = bootstrap_project(root, platforms=["codex", "opencode"])
    config_path = Path(first.home) / "Projects" / first.world_id / "world.json"
    config_before = config_path.read_bytes()
    second = bootstrap_project(root, platforms=["codex", "opencode"])

    merged = agents.read_text(encoding="utf-8")
    assert merged.startswith("user owned\n")
    assert merged == "user owned\n"
    assert config_path.read_bytes() == config_before
    assert agents not in first.updated
    assert agents not in second.updated


def test_git_is_initialized_only_when_requested(tmp_path: Path) -> None:
    root = tmp_path / "world"
    with patch("holocore.install.subprocess.run") as run:
        bootstrap_project(root, platforms=[], init_git=False)
        assert not any(call.args[0][:2] == ["git", "init"] for call in run.call_args_list)

        bootstrap_project(root, platforms=[], init_git=True)
        assert any(call.args[0][:2] == ["git", "init"] for call in run.call_args_list)


def test_bootstrap_merges_existing_mcp_and_repairs_codex_config(tmp_path: Path) -> None:
    root = tmp_path / "existing world"
    root.mkdir()
    (root / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "other"}}}), encoding="utf-8")
    (root / ".codex").mkdir()
    (root / ".codex/config.toml").write_text('model = "example"\n', encoding="utf-8")

    first = bootstrap_project(root, init_git=False, platforms=["claude", "codex"])
    second = bootstrap_project(root, init_git=False, platforms=["claude", "codex"])

    mcp = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))
    assert set(mcp["mcpServers"]) == {"other"}
    codex = tomllib.loads((root / ".codex/config.toml").read_text(encoding="utf-8"))
    assert codex["model"] == "example"
    assert "mcp_servers" not in codex
    connections = Path(first.home) / "Connections"
    central = tomllib.loads((connections / ".codex/config.toml").read_text(encoding="utf-8"))
    assert central["mcp_servers"]["holocore"]["cwd"] == str(first.home)
    assert root / ".mcp.json" not in first.updated
    status = integration_status(root)
    assert status["claude"]["connected"] is True
    assert status["codex"]["connected"] is True


def test_fresh_bootstrap_is_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "fresh"
    bootstrap_project(root, init_git=False, platforms=["claude", "codex"])
    second = bootstrap_project(root, init_git=False, platforms=["claude", "codex"])

    assert not second.created
    assert not second.updated


def test_bootstrap_copies_legacy_memory_shards_to_central_world_without_deleting_source(
    tmp_path: Path, isolated_holocore_home: Path
) -> None:
    root = tmp_path / "legacy-world"
    legacy_path = root / ".holocore" / "animus.db"
    legacy = Animus(legacy_path)
    legacy.create_world("legacy")
    legacy.create_sector("legacy", "project")
    legacy.ingest("A durable legacy decision", world="legacy", sector="project", source_ref="test")

    report = bootstrap_project(root, init_git=False, platforms=[], home=isolated_holocore_home)
    central = Animus(Config.load(root=root).animus_path)

    assert central.status()["memory_shards"] == 1
    assert legacy_path.is_file()
    assert any("Migrated 1 legacy Memory Shards" in warning for warning in report.warnings)


def test_capture_hooks_merge_preserves_user_json_and_is_idempotent(
    tmp_path: Path, isolated_holocore_home: Path
) -> None:
    root = tmp_path / "world"
    (root / ".codex").mkdir(parents=True)
    (root / ".claude").mkdir()
    codex_path = root / ".codex" / "hooks.json"
    claude_path = root / ".claude" / "settings.json"
    codex_path.write_text(
        json.dumps(
            {
                "userSetting": {"preserve": True},
                "hooks": {
                    "Stop": [{"hooks": [{"type": "command", "command": "user-stop"}]}],
                    "Other": [{"hooks": [{"type": "command", "command": "other"}]}],
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    claude_path.write_text(
        json.dumps(
            {
                "permissions": {"allow": ["Read"]},
                "hooks": {
                    "SessionEnd": [
                        {"hooks": [{"type": "command", "command": "user-session-end"}]}
                    ]
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    bootstrap_project(
        root,
        init_git=False,
        platforms=["codex", "claude"],
        home=isolated_holocore_home,
    )
    codex_first = codex_path.read_bytes()
    claude_first = claude_path.read_bytes()
    bootstrap_project(
        root,
        init_git=False,
        platforms=["codex", "claude"],
        home=isolated_holocore_home,
    )

    codex = json.loads(codex_path.read_text(encoding="utf-8"))
    claude = json.loads(claude_path.read_text(encoding="utf-8"))
    assert codex["userSetting"] == {"preserve": True}
    assert codex["hooks"]["Stop"][0]["hooks"][0]["command"] == "user-stop"
    assert codex["hooks"]["Other"][0]["hooks"][0]["command"] == "other"
    assert claude["permissions"] == {"allow": ["Read"]}
    assert claude["hooks"]["SessionEnd"][0]["hooks"][0]["command"] == "user-session-end"
    assert all("holocore.capture_hook" not in json.dumps(group) for group in codex["hooks"]["Stop"])
    assert all("holocore.capture_hook" not in json.dumps(group) for group in claude["hooks"]["SessionEnd"])
    connections = isolated_holocore_home / "Connections"
    central_codex = json.loads((connections / ".codex/hooks.json").read_text(encoding="utf-8"))
    central_claude = json.loads((connections / ".claude/settings.json").read_text(encoding="utf-8"))
    assert "holocore.capture_hook" in json.dumps(central_codex)
    assert "holocore.capture_hook" in json.dumps(central_claude)
    assert codex_path.read_bytes() == codex_first
    assert claude_path.read_bytes() == claude_first
