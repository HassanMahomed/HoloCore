from __future__ import annotations

import json

from holocore import auto_update


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return json.dumps({"tag_name": "v9.0.0"}).encode()


def test_auto_update_is_disabled_by_default_and_never_contacts_network(tmp_path, monkeypatch):
    def unexpected(*_args, **_kwargs):
        raise AssertionError("network should not be used while automatic updates are disabled")

    monkeypatch.setattr(auto_update.urllib.request, "urlopen", unexpected)
    result = auto_update.maybe_check(tmp_path, current_version="0.7.2")
    assert result["reason"] == "disabled"
    assert result["checked"] is False


def test_enabled_update_checks_once_and_queues_only_at_session_end(tmp_path, monkeypatch):
    auto_update.configure(tmp_path, enabled=True)
    calls = []
    monkeypatch.setattr(auto_update.urllib.request, "urlopen", lambda *_args, **_kwargs: _Response())
    monkeypatch.setattr(auto_update, "_launch_update", lambda home: calls.append(str(home)) or True)

    checked = auto_update.maybe_check(
        tmp_path, current_version="0.7.2", session_ending=True, force=True
    )
    assert checked["checked"] is True
    assert checked["latest_version"] == "9.0.0"
    assert checked["update_available"] is True
    assert checked["update_queued"] is True
    assert calls == [str(tmp_path)]

    not_due = auto_update.maybe_check(tmp_path, current_version="0.7.2")
    assert not_due["checked"] is False
    assert not_due["reason"] == "not-due"
