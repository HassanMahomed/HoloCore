"""Non-blocking AI-client hook entrypoint for activity and chat capture."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

from .capture import capture_hook
from .engine import HoloCoreEngine
from .config import Config
from .activity import ActivityLedger
from .auto_update import maybe_check
from . import __version__


def _world_root(cwd: Path) -> Path:
    """Resolve a hook cwd inside the registered World that owns it."""
    try:
        home = Config._selected_home()
        if home:
            for item in __import__("holocore.home", fromlist=["HomeManager"]).HomeManager(home).list_worlds().get("worlds", []):
                candidate = Path(str(item.get("root", ""))).resolve()
                if candidate and (cwd == candidate or candidate in cwd.parents):
                    return candidate
    except Exception:
        pass
    return cwd


def _diagnostic(root: Path, payload: Mapping[str, Any], result: Mapping[str, Any]) -> None:
    path = Config.load(root=root).state_dir / "capture-diagnostics.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"timestamp": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(), "event": payload.get("hook_event_name", "unknown"), "session_id": payload.get("session_id"), "turn_id": payload.get("turn_id"), "cwd": str(root), "transcript_path": payload.get("transcript_path"), "captured": result.get("captured", False), "reason": result.get("reason"), "messages": result.get("messages", 0), "committed": result.get("committed", False), "archive_entry": result.get("archive_entry")}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def _fallback_messages(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for key in ("prompt", "user_message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            messages.append({"role": "user", "content": value.strip()})
            break
    value = payload.get("last_assistant_message")
    if isinstance(value, str) and value.strip():
        messages.append({"role": "assistant", "content": value.strip()})
    return messages


def run(payload: Mapping[str, Any], client: str) -> dict[str, Any]:
    root = _world_root(Path(str(payload.get("cwd") or Path.cwd())).expanduser().resolve(strict=False))
    config = Config.load(root=root)
    project_id = config.world_id or root.name
    try:
        activity = ActivityLedger(config.animus_path).record_hook(
            payload, client=client, project_id=project_id
        )
    except Exception as exc:  # Activity capture must never block the client.
        activity = {"recorded": False, "reason": f"activity-error:{type(exc).__name__}"}
    if activity.get("recorded"):
        try:
            event = str(payload.get("hook_event_name") or payload.get("event") or "")
            maybe_check(
                config.home or config.state_dir.parent,
                current_version=__version__,
                session_ending=event.casefold().replace("_", "").replace("-", "") == "sessionend",
            )
        except Exception:
            # Updates are opportunistic. A missing network connection or an
            # updater problem must never delay or break the parent AI client.
            pass
    if client in {"cursor", "gemini", "opencode"}:
        # These clients currently contribute action history only. Their hooks
        # must not copy prompts, replies, or raw transcripts into Chat History.
        result = {"captured": False, "reason": "activity-only-client", "activity": activity}
        _diagnostic(root, payload, result)
        return result
    state = root / ".holocore" / "capture-state.json"
    report = capture_hook(payload, client, state)
    messages = list(report.get("messages", [])) or _fallback_messages(payload)
    if not messages:
        result = {"captured": False, "reason": report.get("reason", "no-messages")}
        if activity.get("recorded"):
            result["activity"] = activity
        _diagnostic(root, payload, result); return result
    engine = HoloCoreEngine(root)
    result = engine.ingest_chat(messages, source=f"{client}:{report.get('source') or 'hook'}")
    # Advance the transcript cursor immediately after successful ingestion so
    # an unrelated Atlas refresh failure cannot cause duplicate LLM calls.
    committed = report.commit()
    try:
        inbox = engine.sync_inbox()
        atlas = inbox.get("atlas") or engine.ensure_atlas()
    except Exception as exc:  # Capture must never break the parent AI client.
        inbox = {"synced": False, "warning": type(exc).__name__}
        atlas = {"refreshed": False, "warning": type(exc).__name__}
    result = {
        "captured": True,
        "messages": len(messages),
        "committed": committed,
        "archive_entry": result.get("archive_entry"),
        "inbox": inbox,
        "atlas": atlas,
        "activity": activity,
    }; _diagnostic(root, payload, result); return result


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--client", choices=("claude", "codex", "cursor", "gemini", "opencode"), required=True
    )
    args = parser.parse_args()
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        result = run(payload if isinstance(payload, dict) else {}, args.client)
    except Exception as exc:
        result = {"captured": False, "reason": f"hook-error:{type(exc).__name__}"}
    # Gemini and Cursor require JSON hook responses. Empty objects preserve
    # observational behavior; all useful details remain in the local ledger.
    if args.client in {"cursor", "gemini"}:
        sys.stdout.write("{}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
