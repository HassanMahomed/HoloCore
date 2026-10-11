from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from holocore.commands import COMMANDS
from holocore.engine import HoloCoreEngine
from holocore.mcp_server import PROMPTS, TOOLS
from holocore.parity import assert_parity, compatibility_matrix


def test_generated_interfaces_and_mcp_prompts_have_no_drift() -> None:
    report = assert_parity()
    assert report["ok"]
    expected = {command.name for command in COMMANDS}
    for names in report["matrix"]["generated"].values():
        assert set(names) == expected
    assert set(report["matrix"]["mcp"]["prompts"]) == expected


def test_compatibility_matrix_is_json_serializable_and_tools_are_namespaced() -> None:
    matrix = compatibility_matrix()
    json.dumps(matrix)
    assert matrix["mcp"]["tools"]
    assert all(name.startswith("holocore_") for name in matrix["mcp"]["tools"])
    assert all(tool["inputSchema"]["type"] == "object" for tool in TOOLS)
    assert {item["name"] for item in PROMPTS} == set(matrix["cli"])


@pytest.fixture
def keyless_release_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict[str, str]]:
    world = tmp_path / "keyless-release-world"
    home = tmp_path / "keyless-home"
    config_home = tmp_path / "config"
    config_home.mkdir()
    env = os.environ.copy()
    for key in ("HOLOCORE_LLM_BASE_URL", "HOLOCORE_LLM_MODEL", "HOLOCORE_LLM_API_KEY", "OPENAI_API_KEY"):
        env.pop(key, None)
        monkeypatch.delenv(key, raising=False)
    env.update({"PYTHONPATH": str(Path(__file__).parents[1] / "src"), "HOLOCORE_CONFIG_HOME": str(config_home)})
    return world, {**env, "HOLOCORE_HOME": str(home)}


def _run_cli(world: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "holocore.cli", "--root", str(world), "--json", *args],
        env=env, text=True, capture_output=True, check=False,
    )


def test_keyless_release_acceptance_fixture(keyless_release_env: tuple[Path, dict[str, str]]) -> None:
    world, env = keyless_release_env
    setup = _run_cli(world, env, "init", "--no-git", "--home", env["HOLOCORE_HOME"])
    assert setup.returncode == 0, setup.stderr
    assert json.loads(setup.stdout)["home"] == env["HOLOCORE_HOME"]

    source = world / "release_note.txt"
    source.write_text("The release decision is to keep keyless local extraction.", encoding="utf-8")
    ingest = _run_cli(world, env, "ingest", str(source))
    assert ingest.returncode == 0, ingest.stderr
    assert json.loads(ingest.stdout)["status"] in {"ok", "provider_required"}

    status = _run_cli(world, env, "status")
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["readiness"]["ready"] is True

    connections = _run_cli(world, env, "connections")
    assert connections.returncode == 0, connections.stderr
    assert set(json.loads(connections.stdout)) == {"claude", "codex", "cursor", "gemini", "opencode"}

    activity = _run_cli(world, env, "activity", "status")
    assert activity.returncode == 0, activity.stderr
    assert json.loads(activity.stdout)["ready"] is True

    mcp = subprocess.Popen(
        [sys.executable, "-m", "holocore.mcp_server"], cwd=world, env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert mcp.stdin and mcp.stdout
        mcp.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n")
        mcp.stdin.flush()
        response = json.loads(mcp.stdout.readline())
        assert response["result"]["tools"]
    finally:
        mcp.terminate()
        mcp.wait(timeout=5)


def test_complete_three_engine_keyless_release_path(keyless_release_env: tuple[Path, dict[str, str]]) -> None:
    """Exercise the merged Archive, Atlas, and Animus workflow in one runtime."""
    world, env = keyless_release_env
    engine = HoloCoreEngine(world)
    setup = engine.setup(git=False, home=Path(env["HOLOCORE_HOME"]))
    assert setup["ready"] is True

    source = world / "module.py"
    source.write_text("class ReleaseFeature:\n    def run(self):\n        return 'ok'\n", encoding="utf-8")
    note = world / "decision.md"
    note.write_text("The release decision is to keep the workflow local and searchable.", encoding="utf-8")

    ingested = engine.ingest_source(note)
    assert ingested["status"] in {"ok", "provider_required"}
    refreshed = engine.refresh()
    assert refreshed["nodes"] > 0
    atlas = engine.router.atlas
    assert atlas.constellations(min_size=1)
    assert atlas.audit()["source_coverage"]["signals"] >= 1
    assert atlas.explain("ReleaseFeature")["matches"]

    engine.remember("The release decision is to keep the workflow local.", sector="project", source="test")
    engine.router.animus.record_diary("Release verification completed.", world=engine.router.world_id, title="Release", sector="project")
    assert engine.router.animus.timeline(world=engine.router.world_id, sector="project")
    assert engine.router.animus.consolidate(world=engine.router.world_id, sector="project")["examined"] >= 1
    assert engine.router.animus.search("release decision", world=engine.router.world_id)

    engine.refresh()
    unified = engine.search("ReleaseFeature release decision")
    assert any(item.source == "ARCHIVE" for item in unified)
    assert any(item.source == "ATLAS" for item in unified)
    assert any(item.source == "ANIMUS" for item in unified)
