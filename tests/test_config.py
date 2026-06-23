# Tests for Config system
import sys, json, tempfile, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
os.chdir(str(Path(__file__).parent.parent))

from config import Config


class TestConfig:
    def test_loads_aurora_json(self):
        cfg = Config(".")
        all_cfg = cfg.all()
        assert "llm" in all_cfg
        assert "agent" in all_cfg

    def test_dot_notation_get(self):
        cfg = Config(".")
        assert cfg.get("llm.model") == "gpt-4o"
        assert cfg.get("agent.max_turn_iter") == 30

    def test_properties(self):
        cfg = Config(".")
        assert cfg.llm_model == "gpt-4o"
        assert cfg.max_turn_iter == 30
        assert cfg.token_budget == 24000
        assert cfg.llm_api_key == ""

    def test_get_with_default(self):
        cfg = Config(".")
        assert cfg.get("nonexistent.key", 42) == 42
        assert cfg.get("nonexistent") is None

    def test_deep_merge(self):
        cfg = Config(".")
        base = {"a": {"b": 1, "c": 2}}
        override = {"a": {"b": 10}}
        result = cfg._deep_merge(base.copy(), override)
        assert result["a"]["b"] == 10  # Overridden
        assert result["a"]["c"] == 2   # Preserved