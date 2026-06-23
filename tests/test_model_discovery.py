"""Tests for Model Discovery -- multi-provider enumeration and benchmarking"""
import pytest
import json
import time


class TestModelInfo:
    def test_defaults(self):
        from backend.model_discovery import ModelInfo
        mi = ModelInfo(id="gpt-4o")
        assert mi.id == "gpt-4o"
        assert mi.context_window == 128000
        assert mi.provider == "custom"

    def test_fields(self):
        from backend.model_discovery import ModelInfo
        mi = ModelInfo(
            id="test-model",
            provider="openai",
            context_window=100000,
            reasoning_support=True,
            vision_support=True,
            input_price=2.5,
            output_price=10.0,
            speed_tier="fast",
            recommended_for="code",
        )
        assert mi.reasoning_support is True
        assert mi.vision_support is True
        assert mi.input_price == 2.5


class TestModelDiscovery:
    def test_known_models_populated(self):
        from backend.model_discovery import ModelDiscovery, KNOWN_MODELS
        md = ModelDiscovery()
        assert len(KNOWN_MODELS) > 10
        assert "gpt-4o" in KNOWN_MODELS
        assert "claude-sonnet-4-20250514" in KNOWN_MODELS
        assert "deepseek-chat" in KNOWN_MODELS

    def test_make_model_with_known(self):
        from backend.model_discovery import ModelDiscovery
        md = ModelDiscovery()
        mi = md._make_model("gpt-4o", provider="openai")
        assert mi.id == "gpt-4o"
        assert mi.vision_support is True
        assert mi.speed_tier == "fast"

    def test_make_model_unknown(self):
        from backend.model_discovery import ModelDiscovery
        md = ModelDiscovery()
        mi = md._make_model("unknown-model-1234")
        assert mi.id == "unknown-model-1234"
        assert mi.context_window == 128000
        assert mi.provider == "custom"

    def test_make_model_overrides(self):
        from backend.model_discovery import ModelDiscovery
        md = ModelDiscovery()
        mi = md._make_model("gpt-4o", provider="azure", speed_tier="custom-speed")
        assert mi.provider == "azure"
        assert mi.speed_tier == "custom-speed"

    def test_recommend_code(self):
        from backend.model_discovery import ModelDiscovery
        md = ModelDiscovery()
        result = md.recommend("code")
        assert result["type"] == "code"
        assert result["recommended"] is not None
        assert len(result["alternatives"]) > 0

    def test_recommend_cheap(self):
        from backend.model_discovery import ModelDiscovery
        md = ModelDiscovery()
        result = md.recommend("cheap")
        assert result["type"] == "cheap"

    def test_recommend_unknown_type(self):
        from backend.model_discovery import ModelDiscovery
        md = ModelDiscovery()
        result = md.recommend("nonexistent")
        assert result["recommended"] is not None

    def test_cache_results_noop_when_empty(self):
        from backend.model_discovery import ModelDiscovery
        md = ModelDiscovery()
        removed = md.cache_results(ttl_seconds=3600)
        assert removed == 0

    def test_get_cache_age_empty(self):
        from backend.model_discovery import ModelDiscovery
        md = ModelDiscovery()
        ages = md.get_cache_age()
        assert isinstance(ages, dict)
        assert len(ages) == 0

    def test_get_context_limit(self):
        from backend.model_discovery import ModelDiscovery
        md = ModelDiscovery()
        result = md.get_context_limit("gpt-4o")
        assert "model_limit" in result
        assert "effective" in result
        assert result["model_limit"] == 128000

    def test_get_context_limit_custom(self):
        from backend.model_discovery import ModelDiscovery
        md = ModelDiscovery()
        result = md.get_context_limit("gpt-4o", user_max_tokens=50000)
        assert result["effective"] == 50000

    @pytest.mark.asyncio
    async def test_discover_openai_fallback(self):
        from backend.model_discovery import ModelDiscovery
        md = ModelDiscovery()
        models = await md.discover_openai_models("fake-key", "https://invalid.example.com/v1")
        assert len(models) > 0
        assert any(m.id == "gpt-4o" for m in models)

    @pytest.mark.asyncio
    async def test_discover_ollama_timeout(self):
        from backend.model_discovery import ModelDiscovery
        md = ModelDiscovery()
        models = await md.discover_ollama_models("http://127.0.0.1:19999")
        assert isinstance(models, list)

    @pytest.mark.asyncio
    async def test_discover_groq_fallback(self):
        from backend.model_discovery import ModelDiscovery
        md = ModelDiscovery()
        models = await md.discover_groq_models("fake-key")
        assert len(models) > 0
        assert any(m.provider == "groq" for m in models)

    @pytest.mark.asyncio
    async def test_discover_deepseek_fallback(self):
        from backend.model_discovery import ModelDiscovery
        md = ModelDiscovery()
        models = await md.discover_deepseek_models("fake-key")
        assert len(models) > 0
        assert any(m.provider == "deepseek" for m in models)

    @pytest.mark.asyncio
    async def test_discover_all(self):
        from backend.model_discovery import ModelDiscovery
        md = ModelDiscovery()
        models = await md.discover_all(api_key="fake")
        assert isinstance(models, list)

    @pytest.mark.asyncio
    async def test_test_model_bad_url(self):
        from backend.model_discovery import ModelDiscovery
        md = ModelDiscovery()
        result = await md.test_model("openai", "gpt-4o", "fake-key", "https://invalid.example.com")
        assert result.success is False or result.error != ""


class TestBenchmarkResult:
    def test_fields(self):
        from backend.model_discovery import BenchmarkResult
        br = BenchmarkResult(model_id="gpt-4o", provider="openai", latency_ms=500.0, tokens_per_sec=45.0)
        assert br.latency_ms == 500.0
        assert br.tokens_per_sec == 45.0
        assert br.success is True