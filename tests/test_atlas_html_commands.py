import json
import tomllib
from pathlib import Path

import pytest

from holocore.atlas_html import generate_atlas_html, generate_atlas_views, render_atlas_html
from holocore.commands import COMMANDS, get_command, render_all_commands, render_codex_skill


def _graph() -> dict:
    return {
        "directed": True,
        "graph": {"generator": "holocore-atlas"},
        "nodes": [
            {"id": "file:a.py", "label": "a.py", "kind": "file", "source_file": "a.py"},
            {"id": "function:a.py:run", "label": "run()", "kind": "function", "source_file": "a.py"},
        ],
        "links": [{"source": "file:a.py", "target": "function:a.py:run", "relation": "contains"}],
    }


def test_html_is_self_contained_searchable_and_embeds_graph() -> None:
    html = render_atlas_html(_graph())
    assert html.startswith("<!doctype html>")
    assert 'id="atlas-data"' in html
    assert 'id="search"' in html and 'id="kind"' in html and 'id="relation"' in html
    assert "function:a.py:run" in html
    assert "https://" not in html and "src=\"" not in html


def test_html_escapes_script_breakout_and_writes_next_to_atlas(tmp_path: Path) -> None:
    graph = _graph()
    graph["nodes"][0]["label"] = "</script><script>alert(1)</script>"
    source = tmp_path / "atlas.json"
    source.write_text(json.dumps(graph), encoding="utf-8")
    output = generate_atlas_html(source)
    text = output.read_text(encoding="utf-8")
    assert output == tmp_path / "atlas.html"
    assert "</script><script>alert" not in text
    assert "\\u003c/script\\u003e" in text


def test_viewer_bundle_renders_once_to_public_and_compatibility_paths(tmp_path: Path) -> None:
    source = tmp_path / "graph.json"
    source.write_text(json.dumps(_graph()), encoding="utf-8")
    outputs = [tmp_path / "atlas.html", tmp_path / "graph.html", tmp_path / ".holocore" / "atlas.html"]

    written = generate_atlas_views(source, outputs)

    assert written == outputs
    payloads = [path.read_text(encoding="utf-8") for path in outputs]
    assert len(set(payloads)) == 1
    assert 'name="holocore-atlas-viewer" content="3"' in payloads[0]
    assert '<option value="knowledge">Knowledge</option>' in payloads[0]
    assert "isKnowledge" in payloads[0]
    assert "deconflictLabels" in payloads[0]


def test_html_rejects_invalid_graph_and_mapping_requires_output() -> None:
    with pytest.raises(ValueError):
        render_atlas_html({"nodes": {}, "links": []})
    with pytest.raises(ValueError):
        generate_atlas_html(_graph())


def test_command_catalog_has_required_cross_platform_commands() -> None:
    names = {command.name for command in COMMANDS}
    assert names == {
        "init", "search", "remember", "recall", "animus-sync", "animus-checkpoint", "diary", "timeline", "consolidate", "animus-export",
        "atlas-refresh", "atlas-view", "atlas-explain", "atlas-path", "atlas-affected", "atlas-neighborhood", "atlas-constellations", "atlas-audit", "atlas-export",
        "archive-search", "archive-create", "status", "doctor", "setup", "connect",
        "home", "worlds", "global-graph", "obsidian-vault", "watch", "sync-all", "update", "install-check", "uninstall", "cleanup", "ingest", "inbox-sync", "paths", "open-archive",
            "animus-decks", "animus-signal", "animus-chronicle", "console",
    }
    assert get_command("holocore-atlas-view").invocation.startswith("holocore atlas-view")
    assert get_command("holocore-sync-all").write is True
    assert get_command("home").write is False


def test_renderers_emit_every_command_with_portable_paths_and_arguments() -> None:
    rendered = render_all_commands()
    assert set(rendered) == {"claude", "cursor", "gemini", "opencode", "codex"}
    assert len(rendered["claude"]) == len(COMMANDS) * 2
    assert all(len(files) == len(COMMANDS) for platform, files in rendered.items() if platform != "claude")
    for platform, files in rendered.items():
        assert all("\\" not in path for path in files)
        assert any("atlas-view" in path for path in files)
        if platform == "codex":
            assert all(path.endswith("/SKILL.md") for path in files)
            assert all("$ARGUMENTS" not in content for content in files.values())
        if platform == "gemini":
            assert all(path.endswith(".toml") for path in files)
            assert all("{{args}}" in content for content in files.values())
            assert all(tomllib.loads(content)["prompt"] for content in files.values())


def test_codex_skill_has_valid_identity_and_write_safety() -> None:
    skill = render_codex_skill("archive-create")
    assert "name: holocore-archive-create" in skill
    assert "confirm the requested scope" in skill
    assert "holocore archive-create" in skill
    assert "$ARGUMENTS" not in skill
