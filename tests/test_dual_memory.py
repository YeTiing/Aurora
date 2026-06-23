"""Tests for dual-file memory system."""
import sys, tempfile, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
os.chdir(str(Path(__file__).parent.parent))

from dual_memory import (
    MemoryStore, MemoryCurator, UserTraitExtractor,
    DualMemoryManager, get_dual_memory,
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

        curator = MemoryCurator(store, MemoryStore("USER", tmp_path / "user.md", 500).load())
        result = curator.run_lightweight()

        assert "agent" in result
        assert result["agent"]["removed_count"] >= 1  # Should deduplicate


class TestUserTraitExtractor:
    def test_extract_language_pref(self, tmp_path):
        profile = MemoryStore("USER", tmp_path / "user.md", 500).load()
        extractor = UserTraitExtractor()
        found = extractor.extract("以后我们都说中文吧", profile)
        assert len(found) > 0

    def test_extract_style_pref(self, tmp_path):
        profile = MemoryStore("USER", tmp_path / "user.md", 500).load()
        extractor = UserTraitExtractor()
        found = extractor.extract("回答简洁一点别啰嗦", profile)
        assert len(found) > 0

    def test_no_duplicates(self, tmp_path):
        profile = MemoryStore("USER", tmp_path / "user.md", 500).load()
        extractor = UserTraitExtractor()
        extractor.extract("用中文", profile)
        count1 = len(profile.entries)
        extractor.extract("用中文说", profile)
        count2 = len(profile.entries)
        assert count1 == count2  # No duplicate added


class TestDualMemoryManager:
    def test_singleton(self, tmp_path):
        manager = DualMemoryManager(tmp_path)
        assert manager.agent_memory is not None
        assert manager.user_profile is not None
        assert manager.curator is not None

    def test_system_prompt_injection(self, tmp_path):
        manager = DualMemoryManager(tmp_path)
        prompt = manager.get_system_prompt_injection()
        assert "MEMORY" in prompt
        assert "USER PROFILE" in prompt

    def test_process_conversation(self, tmp_path):
        manager = DualMemoryManager(tmp_path)
        result = manager.process_conversation_turn(
            "用 Python 写后端，简洁点", "好的，我会用 Python 简洁实现"
        )
        assert isinstance(result, dict)

    def test_stats(self, tmp_path):
        manager = DualMemoryManager(tmp_path)
        stats = manager.stats()
        assert "agent_memory" in stats
        assert "user_profile" in stats
        assert "curator" in stats
