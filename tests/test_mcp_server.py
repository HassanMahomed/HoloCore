import io
import json

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
