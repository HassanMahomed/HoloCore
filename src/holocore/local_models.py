"""Safe discovery and Home-scoped configuration for optional local models."""

from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit


DEFAULT_OLLAMA_ORIGIN = "http://127.0.0.1:11434"
DEFAULT_DISCOVERY_TIMEOUT = 2.0
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_loopback_url(value: str) -> str:
    """Return a credential-free HTTP loopback origin or reject the endpoint."""
    raw = str(value).strip().rstrip("/")
    parsed = urlsplit(raw)
    if parsed.scheme != "http":
        raise ValueError("local model endpoint must use http")
    if parsed.username or parsed.password:
        raise ValueError("local model endpoint must not contain credentials")
    if (parsed.hostname or "").casefold() not in _LOOPBACK_HOSTS:
        raise ValueError("local model endpoint must use loopback")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("local model endpoint must be an origin without path, query, or fragment")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("local model endpoint has an invalid port") from exc
    host = parsed.hostname or "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = f"{host}:{port}" if port is not None else host
    return urlunsplit(("http", netloc, "", "", ""))


def _origin_from_provider_url(value: str) -> str:
    parsed = urlsplit(str(value).strip())
    if parsed.path.rstrip("/") not in {"", "/v1"} or parsed.query or parsed.fragment:
        raise ValueError("configured local provider URL must end at the origin or /v1")
    origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    return normalize_loopback_url(origin)


def _safe_endpoint(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(str(value))
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = parsed.port
    except ValueError:
        port = None
    netloc = f"{host}:{port}" if port is not None else host
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def discover_ollama(base_url: str = DEFAULT_OLLAMA_ORIGIN, *, timeout: float = DEFAULT_DISCOVERY_TIMEOUT) -> dict[str, Any]:
    origin = normalize_loopback_url(base_url)
    result: dict[str, Any] = {
        "provider": "ollama",
        "base_url": origin,
        "reachable": False,
        "models": [],
        "diagnostic": None,
        "observed_at": _now(),
    }
    request = urllib.request.Request(origin + "/api/tags", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=float(timeout)) as response:
            payload = json.loads(response.read().decode("utf-8"))
        rows = payload.get("models") if isinstance(payload, Mapping) else None
        if not isinstance(rows, list):
            raise ValueError("invalid model listing")
        models: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            name = str(row.get("name") or row.get("model") or "").strip()
            if not name or name in models:
                continue
            detail = row.get("details") if isinstance(row.get("details"), Mapping) else {}
            item: dict[str, Any] = {"name": name}
            for key in ("size", "digest"):
                if row.get(key) is not None:
                    item[key] = row[key]
            for key in ("family", "parameter_size", "quantization_level"):
                if detail.get(key) is not None:
                    item[key] = detail[key]
            models[name] = item
        result.update(reachable=True, models=[models[key] for key in sorted(models, key=str.casefold)])
        if not models:
            result["diagnostic"] = "local runtime is available but has no installed models"
    except Exception:
        result["diagnostic"] = "local runtime unavailable"
    return result


class LocalModelManager:
    """Own one non-secret local model selection for a shared HoloCore Home."""

    def __init__(self, home: str | Path, *, timeout: float = DEFAULT_DISCOVERY_TIMEOUT):
        self.home = Path(home).expanduser().resolve()
        self.timeout = float(timeout)
        self.config_path = self.home / "Runtime" / "local-model.json"

    def discover(self, base_url: str | None = None) -> dict[str, Any]:
        return {
            "candidates": [discover_ollama(base_url or DEFAULT_OLLAMA_ORIGIN, timeout=self.timeout)],
            "fallback": "deterministic",
        }

    def _selection(self) -> tuple[dict[str, Any] | None, str | None]:
        if not self.config_path.exists():
            return None, None
        try:
            value = json.loads(self.config_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError
            provider = str(value.get("provider") or "").strip()
            model = str(value.get("model") or "").strip()
            base_url = str(value.get("base_url") or "").strip()
            if provider != "ollama" or not model:
                raise ValueError
            _origin_from_provider_url(base_url)
            return value, None
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None, "local model configuration is invalid"

    def provider_config(self) -> dict[str, Any] | None:
        selection, _ = self._selection()
        if not selection:
            return None
        return {
            "provider": "ollama",
            "base_url": str(selection["base_url"]),
            "model": str(selection["model"]),
            "_source": "home",
        }

    def configure(self, provider: str, model: str, *, base_url: str | None = None) -> dict[str, Any]:
        provider = str(provider).strip().casefold()
        model = str(model).strip()
        if provider != "ollama":
            raise ValueError("automatic local-model configuration supports provider 'ollama'")
        if not model:
            raise ValueError("model must be non-empty")
        origin = normalize_loopback_url(base_url or DEFAULT_OLLAMA_ORIGIN)
        discovery = self.discover(origin)
        candidate = discovery["candidates"][0]
        if not candidate.get("reachable"):
            raise ValueError("local runtime is unavailable")
        available = {str(item.get("name")) for item in candidate.get("models", [])}
        if model not in available:
            raise ValueError(f"model is not installed in the local runtime: {model}")
        selection = {
            "version": 1,
            "provider": provider,
            "model": model,
            "base_url": origin + "/v1",
            "updated_at": _now(),
        }
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.config_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(selection, indent=2), encoding="utf-8")
        temporary.replace(self.config_path)
        return {"configured": True, "path": str(self.config_path), "status": self.status()}

    def clear(self) -> dict[str, Any]:
        existed = self.config_path.exists()
        if existed:
            self.config_path.unlink()
        return {"cleared": existed, "path": str(self.config_path), "status": self.status()}

    def status(self, settings: Mapping[str, Any] | None = None) -> dict[str, Any]:
        values = dict(settings or {})
        env_base = os.getenv("HOLOCORE_LLM_BASE_URL")
        env_model = os.getenv("HOLOCORE_LLM_MODEL")
        diagnostic: str | None = None
        if env_base and env_model:
            base_url, model, provider, source = env_base, env_model, "openai-compatible", "environment"
        elif values.get("base_url") and values.get("model"):
            base_url = str(values["base_url"])
            model = str(values["model"])
            provider = str(values.get("provider") or "openai-compatible")
            source = str(values.get("_source") or "legacy")
        else:
            selection, diagnostic = self._selection()
            if not selection:
                return {
                    "mode": "deterministic", "source": "default", "provider": "deterministic",
                    "model": None, "base_url": None, "reachable": False, "model_available": False,
                    "fallback": "deterministic", "diagnostic": diagnostic,
                }
            base_url = str(selection["base_url"])
            model = str(selection["model"])
            provider = str(selection["provider"])
            source = "home"
        try:
            origin = _origin_from_provider_url(base_url)
        except ValueError:
            return {
                "mode": "configured-provider", "source": source, "provider": provider, "model": model,
                "base_url": _safe_endpoint(base_url), "reachable": None, "model_available": None,
                "fallback": "deterministic", "diagnostic": None,
            }
        candidate = self.discover(origin)["candidates"][0]
        names = {str(item.get("name")) for item in candidate.get("models", [])}
        available = model in names
        healthy = bool(candidate.get("reachable") and available)
        if candidate.get("reachable") and not available:
            diagnostic = "configured model is not installed"
        else:
            diagnostic = candidate.get("diagnostic")
        return {
            "mode": "configured-local" if healthy else "configured-unavailable",
            "source": source,
            "provider": provider,
            "model": model,
            "base_url": _safe_endpoint(base_url),
            "reachable": bool(candidate.get("reachable")),
            "model_available": available,
            "fallback": "deterministic",
            "diagnostic": diagnostic,
        }


__all__ = [
    "DEFAULT_DISCOVERY_TIMEOUT", "DEFAULT_OLLAMA_ORIGIN", "LocalModelManager",
    "discover_ollama", "normalize_loopback_url",
]
