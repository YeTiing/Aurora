"""Tests for dual-file memory system."""
import sys, tempfile, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
os.chdir(str(Path(__file__).parent.parent))

from dual_memory import (
    MemoryStore, Curator, HonchoDialectic,
    ClosedLoopMemory, get_closed_loop,
    SkillManager, MemoryNudge, FTSSessions,
    MAX_AGENT_MEMORY_CHARS, MAX_USER_PROFILE_CHARS,
)


class TestMemoryStore:
    def test_create_and_add(self, tmp_path):
        store = MemoryStore(
            name="TEST",
            file_path=tmp_path / "test.md",
            max_chars=500,
        ).load()
        assert len(store.entries) == 1  # Default entry

        ok, msg = store.add("User prefers concise answers", source="agent")
        assert ok
        assert len(store.entries) == 2
        assert store.char_count > 0
        assert store.usage_pct > 0

    def test_replace_entry(self, tmp_path):
        store = MemoryStore("TEST", tmp_path / "test.md", 500).load()
        initial = len(store.entries)
        ok, msg = store.replace(0, "Updated entry", source="curator")
        assert ok
        assert "Updated entry" == store.entries[0].text

    def test_remove_entry(self, tmp_path):
        store = MemoryStore("TEST", tmp_path / "test.md", 500).load()
        initial = len(store.entries)
        ok, msg = store.remove(0)
        assert ok
        assert len(store.entries) == initial - 1

    def test_overflow_rejection(self, tmp_path):
        store = MemoryStore("TEST", tmp_path / "test.md", 20).load()
        ok, msg = store.add("This is way too long to fit in 20 characters")
        assert not ok
        assert "overflow" in msg.lower() or "cannot" in msg.lower()

    def test_system_prompt_format(self, tmp_path):
        store = MemoryStore("AGENT_MEMORY", tmp_path / "agent.md", 500).load()
        prompt = store.to_system_prompt()
        assert "AGENT_MEMORY" in prompt or "MEMORY" in prompt
        assert "§" in prompt
        assert "chars" in prompt.lower() or "%" in prompt

    def test_list_entries(self, tmp_path):
        store = MemoryStore("TEST", tmp_path / "test.md", 500).load()
        entries = store.list_entries()
        assert isinstance(entries, list)
        assert "text" in entries[0]
        assert "index" in entries[0]


class TestMemoryCurator:
    def test_deduplicate(self, tmp_path):
        store = MemoryStore("TEST", tmp_path / "test.md", 1000).load()
        store.add("The user prefers Python for all backend work", source="agent")
        store.add("User prefers Python for backend work", source="agent")  # near duplicate
        store.add("Node.js is used for frontend", source="agent")

        curator = Curator(store, MemoryStore("USER", tmp_path / "user.md", 500).load(), SkillManager(tmp_path / "skills"))
        result = curator.light()

        assert "agent" in result
        assert result["agent"]["removed"] >= 1  # Should deduplicate


class TestHonchoDialectic:
    def test_record_facts(self, tmp_path):
        h = HonchoDialectic()
        h.record("我觉得以后我们还是都用中文比较好英文太麻烦了", "")
        assert len(h._facts) >= 1

    def test_turn_counting(self, tmp_path):
        h = HonchoDialectic(ctx_cadence=2, dial_cadence=3)
        for i in range(5):
            h.record(f"this is turn number {i} with enough text to exceed minimum ok", "")
        assert h._turns == 5

    def test_peer_card_context(self, tmp_path):
        h = HonchoDialectic()
        h.peer.traits.append("prefers Chinese")
        h.peer.preferences.append("concise answers")
        ctx = h.peer.context()
        assert "prefers Chinese" in ctx
        assert "concise answers" in ctx

    def test_dialectic_depth_scaling(self, tmp_path):
        h = HonchoDialectic(dial_depth=2)
        assert h.depth_for(100) == 1
        assert h.depth_for(300) == 2
        assert h.depth_for(600) == 3

    def test_cold_warm_prompts(self, tmp_path):
        h = HonchoDialectic()
        h.record("I think we should use Python for the backend because it is fast enough for our use case", "")
        cold = h.cold_prompt()
        assert "Build user model" in cold or "user model" in cold.lower()
        warm = h.warm_prompt()
        assert "Update user model" in warm or "user model" in warm.lower()

class TestClosedLoopMemory:
    def test_singleton(self, tmp_path):
        manager = ClosedLoopMemory(tmp_path)
        assert manager.agent_memory is not None
        assert manager.user_profile is not None
        assert manager.curator is not None

    def test_system_prompt_injection(self, tmp_path):
        manager = ClosedLoopMemory(tmp_path)
        prompt = manager.system_prompt()
        assert "MEMORY" in prompt
        assert "USER PROFILE" in prompt

    def test_process_turn(self, tmp_path):
        manager = ClosedLoopMemory(tmp_path)
        r = manager.process_turn("用 Python 写后端，简洁点", "好的，我会用 Python 简洁实现")
        assert isinstance(r, dict)
        manager = ClosedLoopMemory(tmp_path)
        result = manager.process_turn(
            "用 Python 写后端，简洁点", "好的，我会用 Python 简洁实现"
        )
        assert isinstance(result, dict)

    def test_stats(self, tmp_path):
        manager = ClosedLoopMemory(tmp_path)
        stats = manager.stats()
        assert "agent_memory" in stats
        assert "user_profile" in stats
        assert "curator" in stats
