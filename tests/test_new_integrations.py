"""Tests for new integrated modules: MagicDocs, IM Adapter, Session Search, Worktree, Context Collapse."""
import sys, os, tempfile, shutil, asyncio
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
os.chdir(str(Path(__file__).parent.parent))


# ========== MagicDocs ==========

class TestMagicDocsDetection:
    def test_detects_header(self):
        from backend.magic_docs import MagicDocsManager
        md = MagicDocsManager()
        content = "# MAGIC DOC: Project Architecture"
        result = md.detect(content)
        assert result is not None
        assert result["title"] == "Project Architecture"

    def test_detects_with_instructions(self):
        from backend.magic_docs import MagicDocsManager
        md = MagicDocsManager()
        content = "# MAGIC DOC: API Docs\n\n_Document all API endpoints_\n\n# Section 1"
        result = md.detect(content)
        assert result is not None
        assert result["title"] == "API Docs"
        assert "API endpoints" in result.get("instructions", "")

    def test_ignores_non_magic_docs(self):
        from backend.magic_docs import MagicDocsManager
        md = MagicDocsManager()
        content = "# Regular README\n\nJust a normal file."
        result = md.detect(content)
        assert result is None

    def test_detects_case_insensitive(self):
        from backend.magic_docs import MagicDocsManager
        md = MagicDocsManager()
        content = "# magic doc: lower case"
        result = md.detect(content)
        assert result is not None
        assert result["title"] == "lower case"


class TestMagicDocsRegistration:
    def setup_method(self):
        from backend.magic_docs import magic_docs_manager
        self.mgr = magic_docs_manager
        self._before = self.mgr.tracked_count

    def test_register_new_doc(self):
        content = "# MAGIC DOC: Test Doc\n\nSome content"
        self.mgr.register("/tmp/test_magic.md", content)
        assert self.mgr.tracked_count >= 1

    def test_register_updates_existing(self):
        content = "# MAGIC DOC: Test Doc 2\n\nInitial"
        self.mgr.register("/tmp/test_magic2.md", content)
        content2 = "# MAGIC DOC: Test Doc 2\n\nUpdated content"
        self.mgr.register("/tmp/test_magic2.md", content2)
        docs = self.mgr.list_all()
        matching = [d for d in docs if d["path"] == "/tmp/test_magic2.md"]
        assert len(matching) == 1

    def test_register_ignores_non_magic(self):
        before = self.mgr.tracked_count
        self.mgr.register("/tmp/regular.md", "Just a regular file")
        assert self.mgr.tracked_count == before

    def test_should_update_false_initially(self):
        content = "# MAGIC DOC: Fresh\n\ncontent"
        self.mgr.register("/tmp/fresh.md", content)
        assert not self.mgr.should_update("/tmp/fresh.md")

    def test_should_update_after_cooldown(self):
        import time
        content = "# MAGIC DOC: Old\n\ncontent"
        self.mgr.register("/tmp/old.md", content)
        doc = self.mgr._docs.get("/tmp/old.md")
        if doc:
            doc.last_updated = time.time() - 120  # 2 min ago
        assert self.mgr.should_update("/tmp/old.md")

    def test_list_all(self):
        content = "# MAGIC DOC: Lister\n\ncontent"
        self.mgr.register("/tmp/lister.md", content)
        docs = self.mgr.list_all()
        assert any(d["path"] == "/tmp/lister.md" for d in docs)


# ========== IM Adapter ==========

class TestAdapterBridge:
    def setup_method(self):
        from backend.im_adapter import AdapterBridge
        self.bridge = AdapterBridge(max_sessions=10)

    def test_create_session(self):
        s = self.bridge.create_session("chat_123", "telegram")
        assert s.adapter_type == "telegram"
        assert s.chat_id == "chat_123"
        assert s.session_id.startswith("telegram_")

    def test_get_session(self):
        self.bridge.create_session("chat_456", "wechat")
        s = self.bridge.get_session("chat_456", "wechat")
        assert s is not None
        assert s.chat_id == "chat_456"

    def test_get_session_by_id(self):
        s1 = self.bridge.create_session("chat_789", "telegram")
        s2 = self.bridge.get_session_by_id(s1.session_id)
        assert s2 is not None
        assert s2.session_id == s1.session_id

    def test_stats(self):
        self.bridge.create_session("a", "telegram")
        self.bridge.create_session("b", "wechat")
        st = self.bridge.stats()
        assert st["active_sessions"] == 2
        assert "telegram" in st["by_adapter"]
        assert "wechat" in st["by_adapter"]


# ========== Session Search ==========

class TestSessionSearch:
    def setup_method(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp(prefix="aurora_ss_")
        from backend.session_search import SessionSearch
        self.search = SessionSearch(
            db_path=os.path.join(self.tmpdir, "test_search.db")
        )

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_index_and_search(self):
        self.search.index_message("s1", "m1", "user", "Hello, fix the login bug")
        self.search.index_message("s1", "m2", "assistant", "I found a SQL injection in auth.py")
        hits = self.search.search("login")
        assert len(hits) > 0

    def test_search_by_session(self):
        self.search.index_message("s1", "m1", "user", "Task one")
        self.search.index_message("s2", "m2", "user", "Another thing")
        hits = self.search.search("task", session_id="s1")
        assert len(hits) == 1
        assert hits[0].session_id == "s1"

    def test_delete_session(self):
        self.search.index_message("s99", "m99", "user", "to be deleted")
        self.search.delete_session("s99")
        hits = self.search.search("deleted")
        assert len(hits) == 0

    def test_stats(self):
        self.search.index_message("s1", "m1", "user", "msg")
        st = self.search.stats()
        assert st["total_messages"] >= 1


# ========== Worktree ==========

class TestWorktreeManager:
    def test_init(self):
        from backend.worktree import WorktreeManager
        wm = WorktreeManager()
        assert wm.list_all() == []

    def test_create_in_non_git_fails(self):
        import tempfile
        from backend.worktree import WorktreeManager
        wm = WorktreeManager()
        with tempfile.TemporaryDirectory() as td:
            with pytest.raises((ValueError, RuntimeError)):
                asyncio.get_event_loop().run_until_complete(
                    wm.create("test_session", td)
                )

    def test_get_workspace_unknown(self):
        from backend.worktree import WorktreeManager
        wm = WorktreeManager()
        assert wm.get_workspace("no_such_session") is None


# ========== Context Collapse ==========

class TestContextCollapser:
    def setup_method(self):
        from backend.context.collapse import ContextCollapser, CollapseConfig
        self.c = ContextCollapser(CollapseConfig(
            max_messages=10, max_tool_results=5,
            summary_turn_threshold=3, auto_compact=True,
        ))

    def test_should_collapse_false_under_limit(self):
        msgs = [{"role": "user", "content": "hi"}]
        assert not self.c.should_collapse(msgs)

    def test_should_collapse_true_over_limit(self):
        msgs = [{"role": "tool", "content": "result"}] * 10
        assert self.c.should_collapse(msgs)

    def test_collapse_short_conversation_noop(self):
        msgs = [{"role": "user", "content": "hi"}]
        result, summary = self.c.collapse(msgs, keep_last=10)
        assert len(result) == 1
        assert summary == ""

    def test_collapse_long_conversation(self):
        msgs = []
        for i in range(20):
            msgs.append({"role": "user", "content": f"message {i}"})
            msgs.append({"role": "assistant", "content": f"reply {i}"})
        result, summary = self.c.collapse(msgs, keep_last=10)
        assert len(result) <= 11  # summary + last 10

    def test_estimate_savings(self):
        msgs = [{"role": "tool", "content": "x" * 100}] * 20
        est = self.c.estimate_savings(msgs)
        assert est["can_collapse"] is True
        assert est["estimated_token_savings"] > 0

    def test_collapse_count_increments(self):
        msgs = [{"role": "tool", "content": "x"}] * 15
        self.c.collapse(msgs, keep_last=5)
        assert self.c.collapse_count >= 1
