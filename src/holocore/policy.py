"""Portable, client-neutral HoloCore knowledge workflow policy."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Iterable, Mapping


@dataclass(frozen=True)
class PrivacyPolicy:
    """Explicit local/provider policy for content sent to optional LLMs."""

    allow_remote: bool = False
    consent: bool = False
    redact_patterns: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_config(cls, config: Mapping[str, object] | None = None) -> "PrivacyPolicy":
        values = dict(config or {})
        return cls(
            allow_remote=bool(values.get("allow_remote", False)),
            consent=bool(values.get("consent", False)),
            redact_patterns=tuple(str(item) for item in values.get("redact_patterns", ()) if str(item)),
        )

    @property
    def remote_allowed(self) -> bool:
        return self.allow_remote and self.consent

    def redact(self, text: str) -> str:
        result = text
        for pattern in self.redact_patterns:
            result = re.sub(pattern, "[REDACTED]", result, flags=re.I)
        return result

    def redact_messages(self, messages: Iterable[Mapping[str, str]]) -> list[dict[str, str]]:
        return [{"role": str(item.get("role", "user")), "content": self.redact(str(item.get("content", "")))} for item in messages]


def render_policy(client: str = "AI client") -> str:
    """Return the policy installed for every supported AI client."""
    return f"""# HoloCore Knowledge Policy

This project uses HoloCore through `{client}`. HoloCore is local-first and uses one
check-first, non-recursive route for each request.

## Start of a project session

1. Read the project instruction file once.
2. Run `holocore status` before broad orientation.
3. Check Code Map freshness before using structural results. HoloCore refreshes a
   missing or stale Code Map once before search; do not trigger a duplicate refresh.
4. Do not repeat the full bootstrap sequence during the same session.

## Retrieval order

1. **Code Map first:** use the project graph to identify relevant files, symbols, and
   relationships.
2. **Knowledge second:** search the corresponding index and linked durable notes for
   verified rules, decisions, and guidance.
3. **Memories third, only when relevant:** recall prior work, errors, attempts,
   decisions, or conversations from the current Project and group.
4. **Exact sources last:** open only the project files identified by the preceding
   checks.

Build one route plan and execute it once. Never route a HoloCore search back into itself.
Each selected subsystem may run at most once per request.

## Source coordination

- HoloCore is the single routing layer for project context.
- The central Second Brain is a curated Knowledge source, not a second automatic
  conversation-capture engine. Read its indexed, linked notes when the Knowledge
  route selects them; do not mine the whole vault.
- Legacy Graphify outputs and MemPalace remain compatibility or migration sources.
  Query them only when HoloCore explicitly identifies a gap or the user asks for
  historical compatibility. Do not fan out to Graphify, MemPalace, Code Map,
  Knowledge, and Memories independently for the same request.
- Treat generated files and databases as non-source artifacts: `holocore-out`,
  `graphify-out`, `.holocore`, `.git`, MCP logs, caches, and imported copies must
  not be ingested as new knowledge.
- Never write the same conversation or fact to both MemPalace and Memories.
  HoloCore Memories is the default destination for new episodic history.

## Knowledge ownership

- Knowledge owns verified, durable, reusable notes.
- Code Map owns rebuildable structural facts from project files.
- Memories owns episodic history and refined conversation memory.
- Project files remain the source of truth for exact implementation details.
- Do not copy the same raw content into every store.

## Simple distinction

- **Knowledge** = verified durable notes.
- **Code Map** = structural map.
- **Memories** = remembered history.
- **Project** = one registered code or document project.
- **Memory group** = focused area inside a project.
- **Memory** = one retained remembered record.
- **Knowledge note** = polished durable note.
- **Mapped item** = one file, symbol, or relationship.
- **Code Map group** = related mapped items.

## Writing and certainty

- Setup is explicit authorization for local conversation capture, deduplicated
  memory promotion, and one stale Code Map refresh. Other writes remain explicit.
- Update an existing Knowledge note before creating a duplicate.
- Automatically promote only durable-looking summaries, facts, and decisions,
  retaining transcript provenance so they can be reviewed.
- Record uncertain claims as open questions, never as settled facts.
- Refresh the Code Map after meaningful source changes.
- Store meaningful project history in Memories; do not mine all Knowledge notes.
- Promote verified durable knowledge into central Knowledge only once, updating
  an existing note before creating a new one.
- If a route result points to another HoloCore command, do not execute it
  automatically unless the user explicitly requested that write or refresh.
"""


__all__ = ["PrivacyPolicy", "render_policy"]
