import io
import json
from types import SimpleNamespace

from holocore import mcp_server


def test_mcp_does_not_reply_to_notifications(monkeypatch, capsys):
    monkeypatch.setattr(
        mcp_server.sys,
        "stdin",
        io.StringIO(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
            + "\n"
            + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
            + "\n"
            + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            + "\n",
        ),
    )

    assert mcp_server.main() == 0

    responses = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [response["id"] for response in responses] == [1, 2]
    assert all(response["id"] is not None for response in responses)


def test_activity_and_handoff_tools_are_advertised():
    names = {tool["name"] for tool in mcp_server.TOOLS}
    assert {
        "holocore_activity_affected",
        "holocore_activity_handoff",
        "holocore_connections",
        "holocore_handoffs_list",
        "holocore_handoffs_create",
        "holocore_handoffs_claim",
        "holocore_handoffs_close",
    } <= names


def test_local_model_tools_are_advertised_with_write_inputs():
    tools = {tool["name"]: tool for tool in mcp_server.TOOLS}
    assert {
        "holocore_models_discover",
        "holocore_models_status",
        "holocore_models_configure",
        "holocore_models_clear",
    } <= set(tools)
    required = tools["holocore_models_configure"]["inputSchema"]["required"]
    assert required == ["provider", "model"]


def test_local_model_status_tool_uses_active_home(tmp_path):
    engine = SimpleNamespace(root=tmp_path, router=SimpleNamespace(config=SimpleNamespace(home=tmp_path, llm=None)))

    status = mcp_server.call(engine, "holocore_models_status", {})

    assert status["mode"] == "deterministic"
    assert status["fallback"] == "deterministic"


def test_new_integrations_advertise_plain_names_while_legacy_tools_remain_callable(tmp_path):
    names = {tool["name"] for tool in mcp_server.TOOLS}
    assert {
        "holocore_code_map_refresh", "holocore_code_map_groups", "holocore_memory_collect", "holocore_memory_groups",
        "holocore_memory_track", "holocore_memory_history",
    } <= names
    assert "holocore_atlas_constellations" not in names
    assert not any(name.startswith("holocore_atlas_") for name in names)
    assert not any(name.startswith("holocore_animus_") for name in names)

    atlas = SimpleNamespace(constellations=lambda min_size: [{"size": min_size}])
    engine = SimpleNamespace(root=tmp_path, router=SimpleNamespace(atlas=atlas))
    assert mcp_server.call(engine, "holocore_atlas_constellations", {"min_size": 3}) == [{"size": 3}]
