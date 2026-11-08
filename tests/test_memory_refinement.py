import json
from pathlib import Path

from holocore.memories import MemoryStore
from holocore.memory_pipeline import MemoryRefinementPipeline


class FakeLLM:
    def extract(self, messages, *, instructions=""):
        return {"summary": "Chose SQLite for local memory.", "facts": ["The app is local-first."], "decisions": ["Use SQLite."], "preferences": [], "entities": ["SQLite"], "provider": "fake"}


def test_chat_is_audited_and_distilled(tmp_path):
    animus = MemoryStore(tmp_path / "animus.db"); animus.create_world("demo"); animus.create_sector("demo", "conversations")
    result = MemoryRefinementPipeline(animus, tmp_path / "raw-chats", FakeLLM()).refine([{"role": "user", "content": "Use SQLite and stay local."}], world="demo", sector="conversations", source_ref="test")
    assert json.loads(result.audit_path.read_text(encoding="utf-8"))["messages"]
    assert animus.search("SQLite", world="demo", sector="conversations")
