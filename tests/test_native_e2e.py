from __future__ import annotations

import json
from pathlib import Path

from holocore.engine import HoloCoreEngine
from holocore.mcp_server import PROMPTS, TOOLS, call, get_prompt


def test_new_application_combines_wiki_graph_memory_git_and_ai_links(
    tmp_path: Path, isolated_holocore_home: Path
) -> None:
    world = tmp_path / "Demo World"
    engine = HoloCoreEngine(world)

    installed = engine.initialize(git=True)
    assert installed["git"] in {"created", "existing"}
    assert installed["home"] == str(isolated_holocore_home)
    assert installed["paths"]["archive"] == str(isolated_holocore_home / "Knowledge")
    assert installed["paths"]["world_archive"].startswith(
        str(isolated_holocore_home / "Knowledge" / "Projects")
    )
    assert {path.name for path in world.iterdir()} == {".git"}
    connections = isolated_holocore_home / "Connections"
    for relative in ("policy.md", ".cursor/mcp.json", "opencode.json"):
        assert (connections / relative).exists()

    engine.router.archive.init()
    engine.router.archive.create("wiki/decision.md", "# Native decision\n\n## For future Claude\nHoloCore owns one engine and no legacy runtime dependency.")
    archive_hits = engine.router.archive.search("legacy runtime")
    assert archive_hits and archive_hits[0]["source_id"] == "archive"

    source = world / "app.py"
    source.write_text("from pathlib import Path\n\ndef build_world():\n    return Path('.')\n", encoding="utf-8")
    graph = engine.refresh()
    assert graph["nodes"] > 0
    assert engine.router.atlas.search("build_world")

    first = engine.remember("Fixed the native installation workflow", "project", str(source))
    second = engine.remember("Fixed the native installation workflow", "project", str(source))
    assert first["created"] is True
    assert second["deduplicated"] is True
    assert engine.router.animus.search(
        "installation", world=engine.router.world_id, sector="project"
    )

    names = {tool["name"] for tool in TOOLS}
    assert {"holocore_search", "holocore_archive_search", "holocore_atlas_search", "holocore_remember", "holocore_recall"} <= names
    assert call(engine, "holocore_status", {})["world"] == str(world.resolve())
    assert "search" in {prompt["name"] for prompt in PROMPTS}
    assert "holocore search" in get_prompt("search", {"arguments": "architecture"})["messages"][0]["content"]["text"]

    mcp = json.loads((connections / ".mcp.json").read_text(encoding="utf-8"))
    assert mcp["mcpServers"]["holocore"]["args"] == ["-m", "holocore.mcp_server"]

    # Production package contains only src/holocore; old source trees are not runtime imports.
    source_text = "\n".join(path.read_text(encoding="utf-8") for path in (Path(__file__).parents[1] / "src" / "holocore").glob("*.py"))
    assert "from graphify" not in source_text
    assert "import graphify" not in source_text
    assert "from mempalace" not in source_text
    assert "import mempalace" not in source_text
