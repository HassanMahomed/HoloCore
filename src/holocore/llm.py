"""Optional JSON LLM providers for HoloCore memory refinement."""

from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable


@runtime_checkable
class MemoryProvider(Protocol):
    """Provider contract. One invocation must return the complete extraction."""

    def extract(self, messages: Sequence[Mapping[str, str]], *, instructions: str = "") -> Mapping[str, Any]: ...


def _text(messages: Sequence[Mapping[str, str]]) -> str:
    return "\n".join(str(message.get("content", "")).strip() for message in messages if message.get("content"))


class LocalMemoryProvider:
    """Keyless, deterministic extraction used when no remote provider is configured."""

    name = "deterministic"

    def extract(self, messages: Sequence[Mapping[str, str]], *, instructions: str = "") -> Mapping[str, Any]:
        del instructions
        text = _text(messages)
        sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", text) if part.strip()]
        decisions = [item for item in sentences if re.search(r"\b(decid(?:e|ed)|will|chose|agreed)\b", item, re.I)]
        preferences = [item for item in sentences if re.search(r"\b(prefer|like|want|avoid)\b", item, re.I)]
        entities: list[str] = []
        stop = {
            "A", "An", "After", "All", "Also", "And", "Any", "Area", "Automatic", "Before", "Changing", "Choose",
            "During", "Each", "Every", "First", "For", "From", "I", "If", "In", "It", "Its",
            "Home", "Homes", "ID", "Install", "Installs", "Later", "List", "Local", "No", "None", "Not", "On", "One", "Only", "Open", "Or", "Other", "Project", "Read", "Reinstall", "Report", "Restart", "Run", "Search", "Second", "See", "Setup", "Simple", "Stores",
            "The", "Then", "These", "This", "Those", "To", "Update", "Use", "When", "Where", "With",
            "What", "Without", "Yes", "You", "Your",
        }
        for item in re.findall(r"\b(?:[A-Z][\w-]*)(?:\s+[A-Z][\w-]*)*\b", text):
            item = re.sub(r"\s+", " ", item).strip(" .:-")
            words = item.split()
            if not item or words[0] in stop:
                continue
            if len(words) > 1 and words[-1] in {"From", "See", "Setup", "Stop", "Use"}:
                continue
            if len(words) == 1:
                token = words[0]
                distinctive = token.isupper() or any(char.isupper() for char in token[1:]) or len(re.findall(rf"\b{re.escape(token)}\b", text)) >= 2
                if not distinctive:
                    continue
            if item not in entities:
                entities.append(item)
        return {
            "summary": " ".join(sentences[:2]),
            "facts": sentences,
            "decisions": decisions,
            "preferences": preferences,
            "entities": entities,
        }


@dataclass(frozen=True)
class OpenAICompatibleProvider:
    """OpenAI chat-completions compatible JSON provider using only stdlib HTTP."""

    base_url: str
    model: str
    api_key_env: str = "OPENAI_API_KEY"
    api_key_header: str = "Authorization"
    api_key_prefix: str = "Bearer "
    headers: Mapping[str, str] = field(default_factory=dict)
    timeout: float = 30.0
    name: str = "openai-compatible"

    def extract(self, messages: Sequence[Mapping[str, str]], *, instructions: str = "") -> Mapping[str, Any]:
        schema = (
            "Return one JSON object with exactly these keys: summary (string), facts (array of strings), "
            "decisions (array of strings), preferences (array of strings), entities (array of strings). "
            "Extract only information supported by the chat."
        )
        if instructions.strip():
            schema += " Custom instructions: " + instructions.strip()
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": schema}, *[dict(item) for item in messages]],
            "response_format": {"type": "json_object"},
            "temperature": 0,
        }
        headers = {"Content-Type": "application/json", **dict(self.headers)}
        key = os.getenv(self.api_key_env)
        if key:
            headers[self.api_key_header] = self.api_key_prefix + key
        url = self.base_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"
        request = urllib.request.Request(url, json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
        if isinstance(content, Mapping):
            return content
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(content).strip(), flags=re.I)
        result = json.loads(cleaned)
        if not isinstance(result, Mapping):
            raise ValueError("provider response must be a JSON object")
        return result


class FallbackMemoryProvider:
    """Attempt one configured provider call, then preserve the deterministic baseline."""

    def __init__(self, primary: MemoryProvider, fallback: MemoryProvider | None = None):
        self.primary = primary
        self.fallback = fallback or LocalMemoryProvider()
        self.last_used = str(getattr(primary, "name", "configured"))
        self.last_error: str | None = None

    @staticmethod
    def _valid(value: object) -> bool:
        if not isinstance(value, Mapping) or not isinstance(value.get("summary", ""), str):
            return False
        return all(isinstance(value.get(key, []), (list, tuple)) for key in ("facts", "decisions", "preferences", "entities"))

    def extract(self, messages: Sequence[Mapping[str, str]], *, instructions: str = "") -> Mapping[str, Any]:
        try:
            value = self.primary.extract(messages, instructions=instructions)
            if not self._valid(value):
                raise ValueError("configured provider returned invalid structured output")
            self.last_used = str(getattr(self.primary, "name", "configured"))
            self.last_error = None
            return value
        except Exception:
            self.last_used = str(getattr(self.fallback, "name", "deterministic"))
            self.last_error = "configured provider unavailable or invalid"
            return self.fallback.extract(messages, instructions=instructions)


def provider_from_config(config: Mapping[str, Any] | None = None) -> MemoryProvider:
    """Build a remote provider when base URL and model are set, otherwise local."""

    values = dict(config or {})
    base_url = os.getenv("HOLOCORE_LLM_BASE_URL") or values.get("base_url")
    model = os.getenv("HOLOCORE_LLM_MODEL") or values.get("model")
    if not base_url or not model:
        return LocalMemoryProvider()
    primary = OpenAICompatibleProvider(
        base_url=str(base_url), model=str(model),
        api_key_env=str(values.get("api_key_env", "HOLOCORE_LLM_API_KEY")),
        api_key_header=str(values.get("api_key_header", "Authorization")),
        api_key_prefix=str(values.get("api_key_prefix", "Bearer ")),
        headers=dict(values.get("headers", {})), timeout=float(values.get("timeout", 30)),
        name=str(values.get("provider") or "openai-compatible"),
    )
    return FallbackMemoryProvider(primary)


__all__ = ["FallbackMemoryProvider", "LocalMemoryProvider", "MemoryProvider", "OpenAICompatibleProvider", "provider_from_config"]
