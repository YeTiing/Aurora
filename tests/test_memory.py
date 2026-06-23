# Tests for Agent Memory System
import sys, json, time, pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from memory import (
    MemoryHub, MemoryEntry, MemoryType, MemoryImportance,
    WorkingContext, EpisodicMemory, SemanticMemory, Episode, SemanticFact,
    init_memory, get_memory,
)


class TestWorkingMemory:
    def test_create(self):
        wm = WorkingContext()
        assert wm.task_description == ""
        assert wm.recent_files == []

    def test_add_file(self):
        wm = WorkingContext()
        wm.add_file("src/main.py")
        wm.add_file("tests/test_main.py")
        assert "src/main.py" in wm.recent_files
        assert len(wm.recent_files) == 2

    def test_add_file_dedup(self):
        wm = WorkingContext()
        wm.add_file("src/main.py")
        wm.add_file("src/main.py")
        assert len(wm.recent_files) == 1

    def test_add_insight(self):
        wm = WorkingContext()
        wm.add_insight("Found a bug in auth.py")
        wm.add_insight("Found a bug in auth.py")  # dedup
        assert len(wm.key_insights) == 1

    def test_record_decision(self):
        wm = WorkingContext()
        wm.record_decision("Use Flask instead of FastAPI")
        assert len(wm.decision_log) == 1

    def test_to_context_string(self):
        wm = WorkingContext()
        wm.task_description = "Build login API"
        wm.active_goal = "Implement JWT auth"
        wm.add_insight("Auth middleware already exists")
        ctx = wm.to_context_string()
        assert "Build login API" in ctx
        assert "JWT auth" in ctx

    def test_clear(self):
        wm = WorkingContext()
        wm.add_file("test.py")
        wm.add_error("oops")
        wm.clear()
        assert wm.recent_files == []
        assert wm.recent_errors == []

    def test_file_limit(self):
        wm = WorkingContext()
        for i in range(25):
            wm.add_file(f"file_{i}.py")
        assert len(wm.recent_files) == 20  # capped


class TestEpisodicMemory:
    def test_record_and_search(self):
        em = EpisodicMemory()
        ep = Episode(
            episode_id="ep1", session_id="s1",
            task="Set up JWT authentication",
            outcome="success",
            key_learnings=["Use python-jose for JWT", "Store secrets in env"],
            tags=["auth", "jwt"],
        )
        em.record(ep)

        results = em.search(query="JWT")
        assert len(results) == 1
        assert results[0].episode_id == "ep1"

    def test_similar_tasks(self):
        em = EpisodicMemory()
        em.record(Episode(episode_id="e1", session_id="s1", task="add user login", outcome="success"))
        em.record(Episode(episode_id="e2", session_id="s2", task="install dependencies", outcome="success"))

        results = em.similar_tasks("user login page")
        assert any(e.episode_id == "e1" for e in results)

    def test_search_no_match(self):
        em = EpisodicMemory()
        results = em.search(query="nonexistent")
        assert len(results) == 0

    def test_get_learnings(self):
        em = EpisodicMemory()
        em.record(Episode(episode_id="e1", session_id="s1", task="add auth",
                          outcome="success", key_learnings=["Use JWT", "Hash passwords"]))
        em.record(Episode(episode_id="e2", session_id="s2", task="add DB",
                          outcome="failure", key_learnings=["Don't use raw SQL"]))
        learnings = em.get_learnings()
        assert "Use JWT" in learnings
        assert "Don't use raw SQL" not in learnings  # only success

    def test_max_episodes(self):
        em = EpisodicMemory(max_episodes=5)
        for i in range(10):
            em.record(Episode(episode_id=f"e{i}", session_id=f"s{i}", task=f"task {i}"))
        assert em.count == 5

    def test_serialize(self):
        em = EpisodicMemory()
        em.record(Episode(episode_id="e1", session_id="s1", task="test", outcome="success"))
        data = em.to_dict()
        em2 = EpisodicMemory()
        em2.load_from_dict(data)
        assert em2.count == 1


class TestSemanticMemory:
    def test_add_and_search(self):
        sm = SemanticMemory()
        sm.add_fact("Use snake_case for Python", category="convention", tags=["python", "naming"])
        sm.add_fact("Prefer async/await over callbacks", category="preference")

        results = sm.search(query="snake_case")
        assert len(results) == 1
        assert "snake_case" in results[0].content.lower()

    def test_category_filter(self):
        sm = SemanticMemory()
        sm.add_fact("Use TypeScript", category="preference")
        sm.add_fact("Always use try/except", category="rule")

        prefs = sm.get_by_category("preference")
        assert len(prefs) == 1
        assert prefs[0].content == "Use TypeScript"

    def test_reinforcement(self):
        sm = SemanticMemory()
        sm.add_fact("Use black formatter", category="convention")
        sm.add_fact("Use black formatter", category="convention")  # same content
        results = sm.search(query="black")
        assert len(results) == 1
        assert results[0].reinforcement == 2

    def test_min_confidence(self):
        sm = SemanticMemory()
        sm.add_fact("Use pytest", category="convention")
        # Reduce confidence artificially
        fact = list(sm._facts.values())[0]
        fact.confidence = 0.3
        results = sm.search(query="pytest", min_confidence=0.5)
        assert len(results) == 0

    def test_to_context_string(self):
        sm = SemanticMemory()
        sm.add_fact("Use black for formatting", category="convention")
        sm.add_fact("Prefer React over Vue", category="preference")
        ctx = sm.to_context_string()
        assert "black" in ctx.lower()

    def test_serialize(self):
        sm = SemanticMemory()
        sm.add_fact("Use TypeScript", category="preference")
        data = sm.to_dict()
        sm2 = SemanticMemory()
        sm2.load_from_dict(data)
        assert sm2.count == 1


class TestMemoryHub:
    @pytest.fixture
    def hub(self, tmp_path):
        return init_memory(str(tmp_path / "test_memory"))

    def test_record_episode_auto_learn(self, hub):
        ep = Episode(
            episode_id="ep1", session_id="s1",
            task="Set up logging",
            outcome="success",
            key_learnings=["Use structured logging with structlog"],
        )
        hub.record_episode(ep)
        results = hub.semantic.search(query="structured logging")
        assert len(results) == 1

    def test_global_search(self, hub):
        hub.semantic.add_fact("Use FastAPI Router", category="pattern")
        ep = Episode(episode_id="e1", session_id="s1", task="add API routes", outcome="success")
        hub.record_episode(ep)

        results = hub.search("API routes")
        assert "episodes" in results
        assert "facts" in results

    def test_get_context_for_llm(self, hub):
        hub.working.task_description = "Build REST API"
        hub.working.add_insight("Check existing routers in api/")
        hub.semantic.add_fact("Use Pydantic for validation", category="convention")

        ctx = hub.get_context_for_llm(task="Build REST API")
        assert "Build REST API" in ctx
        assert "Pydantic" in ctx

    def test_save_and_load(self, hub):
        hub.semantic.add_fact("Always use async", category="rule")
        hub.record_episode(Episode(episode_id="e1", session_id="s1", task="test", outcome="success"))
        hub.save()

        # Load into new hub
        hub2 = init_memory(str(hub.db_dir))
        assert hub2.semantic.count > 0
        assert hub2.episodic.count > 0

    def test_working_record(self, hub):
        hub.record_working_file("src/main.py")
        hub.record_working_error("ImportError: No module named 'foo'")
        hub.record_working_decision("Switch to uvicorn")
        hub.record_working_tool_use("shell_command")

        assert "src/main.py" in hub.working.recent_files
        assert len(hub.working.recent_errors) == 1
        assert len(hub.working.decision_log) == 1
        assert hub.working.tool_stats.get("shell_command") == 1

    def test_clear_working(self, hub):
        hub.record_working_file("test.py")
        hub.clear_working()
        assert hub.working.recent_files == []

    def test_stats(self, hub):
        hub.semantic.add_fact("Use black", category="convention")
        hub.record_episode(Episode(episode_id="e1", session_id="s1", task="test", outcome="success"))
        stats = hub.stats
        assert stats["facts"] > 0
        assert stats["episodes"] > 0

    def test_auto_classify_learning(self, hub):
        # Convention
        cat = hub._classify_learning("Always use double quotes for JS strings")
        assert cat in ("rule", "convention")

        # Bug fix
        cat = hub._classify_learning("Fixed NPE by adding null check")
        assert cat == "bug_fix"

        # Pattern
        cat = hub._classify_learning("Use repository pattern for DB access")
        assert cat == "pattern"