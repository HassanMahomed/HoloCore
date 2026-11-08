"""Provider-neutral conversation import normalization for Memories ingestion."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        parts = value.get("parts") or value.get("content") or value.get("text")
        if isinstance(parts, list):
            return "\n".join(str(part) for part in parts if part is not None).strip()
        if parts is not None:
            return str(parts).strip()
    if isinstance(value, list):
        return "\n".join(_text(item) for item in value if _text(item)).strip()
    return ""


class ConversationImporter:
    """Normalize common ChatGPT, Slack, and generic exports into Memories messages."""

    def import_file(self, path: str | Path, *, provider: str = "auto") -> dict[str, Any]:
        source = Path(path).expanduser().resolve()
        raw = source.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = None
        provider = provider.casefold()
        messages = self._chatgpt(payload) if provider in {"auto", "chatgpt"} and payload is not None and self._looks_chatgpt(payload) else None
        if not messages and provider in {"auto", "slack"} and payload is not None:
            messages = self._slack(payload)
        if not messages:
            messages = self._generic(payload, raw.decode("utf-8", errors="replace"))
        return {"provider": provider if provider != "auto" else self._detected(payload), "source": str(source), "content_hash": digest, "messages": messages, "message_count": len(messages)}

    @staticmethod
    def _looks_chatgpt(payload: Any) -> bool:
        return isinstance(payload, list) and any(isinstance(item, dict) and ("mapping" in item or "conversation_id" in item) for item in payload)

    @staticmethod
    def _detected(payload: Any) -> str:
        if ConversationImporter._looks_chatgpt(payload):
            return "chatgpt"
        if isinstance(payload, list) and payload and isinstance(payload[0], dict) and ("user" in payload[0] or "user_name" in payload[0]) and "text" in payload[0]:
            return "slack"
        return "generic"

    @staticmethod
    def _chatgpt(payload: list[Any]) -> list[dict[str, Any]]:
        result = []
        for conversation in payload:
            mapping = conversation.get("mapping", {}) if isinstance(conversation, dict) else {}
            for item in mapping.values() if isinstance(mapping, dict) else []:
                message = item.get("message") if isinstance(item, dict) else None
                if not isinstance(message, dict):
                    continue
                content = _text(message.get("content"))
                if not content:
                    continue
                result.append({"role": str(message.get("author", {}).get("role", "unknown")), "content": content, "created_at": message.get("create_time")})
        return result

    @staticmethod
    def _slack(payload: Any) -> list[dict[str, Any]]:
        rows = payload if isinstance(payload, list) else payload.get("messages", []) if isinstance(payload, dict) else []
        return [{"role": str(row.get("user_name") or row.get("user") or "unknown"), "content": str(row.get("text", "")).strip(), "created_at": row.get("ts")} for row in rows if isinstance(row, dict) and str(row.get("text", "")).strip()]

    @staticmethod
    def _generic(payload: Any, raw: str) -> list[dict[str, Any]]:
        if isinstance(payload, dict) and isinstance(payload.get("messages"), list):
            return [{"role": str(item.get("role", "unknown")), "content": _text(item.get("content")), "created_at": item.get("created_at")} for item in payload["messages"] if isinstance(item, dict) and _text(item.get("content"))]
        return [{"role": "unknown", "content": raw.strip()}] if raw.strip() else []


__all__ = ["ConversationImporter"]
