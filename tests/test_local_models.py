from __future__ import annotations

import json
from pathlib import Path
from urllib.error import URLError

import pytest

from holocore.local_models import LocalModelManager, discover_ollama, normalize_loopback_url
from holocore.config import Config
from holocore.home import HomeManager


class _Response:
    def __init__(self, payload: object):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_discover_ollama_returns_deduplicated_sanitized_models(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "models": [
            {"name": "qwen3:latest", "size": 123, "digest": "abc", "details": {"family": "qwen3", "parameter_size": "8B", "quantization_level": "Q4"}},
            {"model": "qwen3:latest"},
            {"name": "gemma4:latest", "unknown": "ignored"},
        ]
    }
    monkeypatch.setattr("holocore.local_models.urllib.request.urlopen", lambda request, timeout: _Response(payload))

    result = discover_ollama(timeout=0.1)

    assert result["provider"] == "ollama"
    assert result["base_url"] == "http://127.0.0.1:11434"
    assert result["reachable"] is True
    assert [model["name"] for model in result["models"]] == ["gemma4:latest", "qwen3:latest"]
    assert result["models"][1]["family"] == "qwen3"
    assert result["diagnostic"] is None


def test_discover_ollama_reports_unavailable_without_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(*_args, **_kwargs):
        raise URLError("connection refused: secret-token-must-not-leak")

    monkeypatch.setattr("holocore.local_models.urllib.request.urlopen", unavailable)
    result = discover_ollama(timeout=0.1)

    assert result["reachable"] is False
    assert result["models"] == []
    assert result["diagnostic"] == "local runtime unavailable"


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:11434",
        "http://192.168.1.20:11434",
        "http://example.com:11434",
        "http://user:password@127.0.0.1:11434",
        "http://127.0.0.1:11434/path?token=secret",
    ],
)
def test_loopback_validation_rejects_unsafe_endpoints(url: str) -> None:
    with pytest.raises(ValueError):
        normalize_loopback_url(url)


def test_manager_configures_shared_home_atomically_and_clears(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = LocalModelManager(tmp_path)
    monkeypatch.setattr(
        manager,
        "discover",
        lambda base_url=None: {
            "candidates": [{"provider": "ollama", "base_url": "http://127.0.0.1:11434", "reachable": True, "models": [{"name": "qwen3:latest"}], "diagnostic": None}],
            "fallback": "deterministic",
        },
    )

    configured = manager.configure("ollama", "qwen3:latest")

    assert configured["configured"] is True
    stored = json.loads(manager.config_path.read_text(encoding="utf-8"))
    assert stored["base_url"] == "http://127.0.0.1:11434/v1"
    assert stored["model"] == "qwen3:latest"
    assert "api_key" not in stored

    cleared = manager.clear()
    assert cleared["cleared"] is True
    assert not manager.config_path.exists()
    assert cleared["status"]["mode"] == "deterministic"


def test_failed_configuration_preserves_existing_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = LocalModelManager(tmp_path)
    manager.config_path.parent.mkdir(parents=True)
    original = '{"version":1,"provider":"ollama","model":"old","base_url":"http://127.0.0.1:11434/v1"}'
    manager.config_path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(manager, "discover", lambda base_url=None: {"candidates": [{"provider": "ollama", "base_url": "http://127.0.0.1:11434", "reachable": True, "models": [{"name": "available"}]}], "fallback": "deterministic"})

    with pytest.raises(ValueError, match="not installed"):
        manager.configure("ollama", "missing")

    assert manager.config_path.read_text(encoding="utf-8") == original


def test_status_prefers_explicit_environment_without_exposing_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = LocalModelManager(tmp_path)
    monkeypatch.setenv("HOLOCORE_LLM_BASE_URL", "https://provider.example/v1")
    monkeypatch.setenv("HOLOCORE_LLM_MODEL", "remote-model")
    monkeypatch.setenv("HOLOCORE_LLM_API_KEY", "do-not-return")

    status = manager.status({"base_url": "https://legacy.example/v1", "model": "legacy"})

    assert status["mode"] == "configured-provider"
    assert status["source"] == "environment"
    assert status["model"] == "remote-model"
    assert "do-not-return" not in json.dumps(status)


def test_registered_world_loads_home_selection_and_preserves_legacy_instructions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    home = tmp_path / "home"
    config_home = tmp_path / "config-home"
    monkeypatch.setenv("HOLOCORE_CONFIG_HOME", str(config_home))
    manager = HomeManager(home, config_home=config_home)
    manager.register_world(project, import_archive=False)
    selection = LocalModelManager(home)
    selection.config_path.parent.mkdir(parents=True)
    selection.config_path.write_text(json.dumps({
        "version": 1,
        "provider": "ollama",
        "model": "qwen3:latest",
        "base_url": "http://127.0.0.1:11434/v1",
    }), encoding="utf-8")
    legacy_path = project / ".holocore" / "config.json"
    legacy_path.parent.mkdir()
    legacy_path.write_text(json.dumps({"llm": {"provider": "local", "custom_instructions": "Keep decisions"}}), encoding="utf-8")

    loaded = Config.load(root=project)

    assert loaded.llm["provider"] == "ollama"
    assert loaded.llm["model"] == "qwen3:latest"
    assert loaded.llm["custom_instructions"] == "Keep decisions"
    assert loaded.llm["_source"] == "home"
