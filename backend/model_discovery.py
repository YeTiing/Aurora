# Model Discovery -- multi-provider model enumeration, benchmarking, recommendation
from __future__ import annotations
import httpx, asyncio, time, json
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ModelInfo:
    id: str
    provider: str = "custom"
    owned_by: str = ""
    context_window: int = 128000
    reasoning_support: bool = False
    vision_support: bool = False
    function_calling: bool = True
    streaming: bool = True
    input_price: float = 0.0
    output_price: float = 0.0
    speed_tier: str = "medium"
    recommended_for: str = "general"
    max_tokens: int = 128000
    available: bool = True
    last_checked: float = 0.0
    benchmark_latency_ms: float = 0.0
    benchmark_tokens_per_sec: float = 0.0


KNOWN_MODELS: dict[str, dict] = {
    "gpt-5.5": {"context_window": 200000, "provider": "openai", "input_price": 5.00, "output_price": 15.00, "speed_tier": "fast", "recommended_for": "code", "reasoning_support": True, "vision_support": True},
    "gpt-5.4": {"context_window": 200000, "provider": "openai", "input_price": 5.00, "output_price": 15.00, "speed_tier": "fast", "recommended_for": "code", "reasoning_support": True, "vision_support": True},
    "gpt-5.3-codex": {"context_window": 200000, "provider": "openai", "input_price": 5.00, "output_price": 15.00, "speed_tier": "fast", "recommended_for": "code", "reasoning_support": True, "vision_support": True},
    "gpt-5.2": {"context_window": 200000, "provider": "openai", "input_price": 5.00, "output_price": 15.00, "speed_tier": "fast", "recommended_for": "code", "reasoning_support": True, "vision_support": True},
    "gpt-4o": {"context_window": 128000, "provider": "openai", "input_price": 2.50, "output_price": 10.00, "speed_tier": "fast", "recommended_for": "multimodal", "reasoning_support": False, "vision_support": True},
    "gpt-4o-mini": {"context_window": 128000, "provider": "openai", "input_price": 0.15, "output_price": 0.60, "speed_tier": "fastest", "recommended_for": "cheap", "reasoning_support": False, "vision_support": True},
    "gpt-4-turbo": {"context_window": 128000, "provider": "openai", "input_price": 10.00, "output_price": 30.00, "speed_tier": "medium", "recommended_for": "general", "reasoning_support": False, "vision_support": True},
    "gpt-4": {"context_window": 8192, "provider": "openai", "input_price": 30.00, "output_price": 60.00, "speed_tier": "slow", "recommended_for": "general", "reasoning_support": False, "vision_support": False},
    "gpt-3.5-turbo": {"context_window": 16384, "provider": "openai", "input_price": 0.50, "output_price": 1.50, "speed_tier": "fastest", "recommended_for": "cheap", "reasoning_support": False, "vision_support": False},
    "claude-sonnet-4-20250514": {"context_window": 200000, "provider": "claude", "input_price": 3.00, "output_price": 15.00, "speed_tier": "fast", "recommended_for": "code", "reasoning_support": True, "vision_support": True},
    "claude-3-5-sonnet": {"context_window": 200000, "provider": "claude", "input_price": 3.00, "output_price": 15.00, "speed_tier": "fast", "recommended_for": "code", "reasoning_support": False, "vision_support": True},
    "claude-3-opus": {"context_window": 200000, "provider": "claude", "input_price": 15.00, "output_price": 75.00, "speed_tier": "slow", "recommended_for": "analysis", "reasoning_support": False, "vision_support": True},
    "claude-3-haiku": {"context_window": 200000, "provider": "claude", "input_price": 0.25, "output_price": 1.25, "speed_tier": "fastest", "recommended_for": "cheap", "reasoning_support": False, "vision_support": True},
    "deepseek-chat": {"context_window": 65536, "provider": "deepseek", "input_price": 0.14, "output_price": 0.28, "speed_tier": "fast", "recommended_for": "cheap", "reasoning_support": False, "vision_support": False},
    "deepseek-reasoner": {"context_window": 65536, "provider": "deepseek", "input_price": 0.55, "output_price": 2.19, "speed_tier": "slow", "recommended_for": "analysis", "reasoning_support": True, "vision_support": False},
    "llama-3.3-70b": {"context_window": 128000, "provider": "groq", "input_price": 0.59, "output_price": 0.79, "speed_tier": "fast", "recommended_for": "general", "reasoning_support": False, "vision_support": False},
    "mixtral-8x7b": {"context_window": 32768, "provider": "groq", "input_price": 0.24, "output_price": 0.24, "speed_tier": "fastest", "recommended_for": "cheap", "reasoning_support": False, "vision_support": False},
    "gemma2-9b-it": {"context_window": 8192, "provider": "groq", "input_price": 0.20, "output_price": 0.20, "speed_tier": "fastest", "recommended_for": "cheap", "reasoning_support": False, "vision_support": False},
    "qwen-max": {"context_window": 32768, "provider": "openai", "input_price": 2.00, "output_price": 6.00, "speed_tier": "medium", "recommended_for": "general", "reasoning_support": False, "vision_support": False},
    "qwen-plus": {"context_window": 131072, "provider": "openai", "input_price": 0.80, "output_price": 2.00, "speed_tier": "medium", "recommended_for": "general", "reasoning_support": False, "vision_support": False},
    "glm-4": {"context_window": 128000, "provider": "openai", "input_price": 1.50, "output_price": 5.00, "speed_tier": "medium", "recommended_for": "general", "reasoning_support": False, "vision_support": False},
    "yi-large": {"context_window": 32768, "provider": "openai", "input_price": 1.50, "output_price": 4.00, "speed_tier": "medium", "recommended_for": "general", "reasoning_support": False, "vision_support": False},
    "moonshot-v1-128k": {"context_window": 128000, "provider": "openai", "input_price": 1.20, "output_price": 5.00, "speed_tier": "medium", "recommended_for": "general", "reasoning_support": False, "vision_support": False},
}


@dataclass
class BenchmarkResult:
    model_id: str
    provider: str
    latency_ms: float
    tokens_per_sec: float
    input_tokens: int = 0
    output_tokens: int = 0
    error: str = ""
    success: bool = True


class ModelDiscovery:
    def __init__(self):
        self._cache: dict[str, list[ModelInfo]] = {}
        self._cache_ttl: dict[str, float] = {}
        self._benchmarks: dict[str, BenchmarkResult] = {}
        self._recommendation_order: dict[str, list[str]] = {
            "code": ["gpt-5.3-codex", "gpt-5.2", "gpt-4o", "claude-sonnet-4-20250514", "claude-3-5-sonnet", "deepseek-chat"],
            "cheap": ["gpt-4o-mini", "gpt-3.5-turbo", "deepseek-chat", "gemma2-9b-it", "mixtral-8x7b"],
            "analysis": ["gpt-5.5", "deepseek-reasoner", "claude-3-opus"],
            "multimodal": ["gpt-5.5", "gpt-4o", "claude-sonnet-4-20250514"],
            "general": ["gpt-4o", "claude-3-5-sonnet", "qwen-max"],
        }

    def _make_model(self, model_id: str, provider: str = "custom", **overrides) -> ModelInfo:
        info = dict(KNOWN_MODELS.get(model_id, {}))
        info.update(overrides)
        info["id"] = model_id
        info["provider"] = provider
        info["context_window"] = info.get("context_window", info.get("max_tokens", 128000))
        return ModelInfo(**{k: v for k, v in info.items() if k in ModelInfo.__dataclass_fields__})

    def _fallback_known_models(self, provider: str) -> list[ModelInfo]:
        """Return known models for a provider when network discovery fails."""
        models: list[ModelInfo] = []
        provider_models = {
            "openai": ["gpt-5.5", "gpt-5.4", "gpt-5.3-codex", "gpt-5.2", "gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4", "gpt-3.5-turbo"],
            "groq": ["llama-3.3-70b", "mixtral-8x7b", "gemma2-9b-it"],
            "deepseek": ["deepseek-chat", "deepseek-reasoner"],
        }
        for mid in provider_models.get(provider, []):
            if mid in KNOWN_MODELS:
                models.append(self._make_model(mid, provider=provider))
        return models

    async def discover_openai_models(self, api_key: str, base_url: str = "https://api.openai.com/v1", provider: str = "openai") -> list[ModelInfo]:
        models: list[ModelInfo] = []
        url = base_url.rstrip("/") + "/models"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url, headers={"Authorization": f"Bearer {api_key}"})
                if resp.status_code == 200:
                    data = resp.json()
                    for m in data.get("data", []):
                        mid = m.get("id", "")
                        if mid and not any(skip in mid.lower() for skip in ["dall-e", "tts-", "whisper", "embedding", "moderation", "audio", "davinci", "babbage", "curie", "ada"]):
                            info = self._make_model(mid, provider=provider, owned_by=m.get("owned_by", "openai"))
                            models.append(info)
        except Exception:
            pass
        # Fallback: return known models when network discovery fails
        if not models:
            models = self._fallback_known_models(provider)
        return models

    async def discover_ollama_models(self, base_url: str = "http://localhost:11434") -> list[ModelInfo]:
        models: list[ModelInfo] = []
        url = base_url.rstrip("/") + "/api/tags"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    for m in data.get("models", []):
                        mid = m.get("name", m.get("model", ""))
                        if mid:
                            details = m.get("details", {})
                            models.append(ModelInfo(
                                id=mid, provider="ollama", owned_by="local",
                                context_window=details.get("parameter_size", "").upper() if details else "",
                                max_tokens=8192, input_price=0.0, output_price=0.0,
                                speed_tier="medium", recommended_for="general",
                                available=True,
                            ))
        except Exception:
            pass
        return models

    async def discover_groq_models(self, api_key: str) -> list[ModelInfo]:
        models: list[ModelInfo] = []
        url = "https://api.groq.com/openai/v1/models"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url, headers={"Authorization": f"Bearer {api_key}"})
                if resp.status_code == 200:
                    data = resp.json()
                    for m in data.get("data", []):
                        mid = m.get("id", "")
                        if mid:
                            info = self._make_model(mid, provider="groq", owned_by=m.get("owned_by", "groq"))
                            models.append(info)
        except Exception:
            pass
        if not models:
            models = self._fallback_known_models("groq")
        return models

    async def discover_deepseek_models(self, api_key: str) -> list[ModelInfo]:
        models: list[ModelInfo] = []
        url = "https://api.deepseek.com/v1/models"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url, headers={"Authorization": f"Bearer {api_key}"})
                if resp.status_code == 200:
                    data = resp.json()
                    for m in data.get("data", []):
                        mid = m.get("id", "")
                        if mid:
                            info = self._make_model(mid, provider="deepseek", owned_by=m.get("owned_by", "deepseek"))
                            models.append(info)
        except Exception:
            pass
        if not models:
            models = self._fallback_known_models("deepseek")
        return models

    async def discover_all(self, api_key: str = "", ollama_url: str = "http://localhost:11434",
                           groq_key: str = "", deepseek_key: str = "") -> list[ModelInfo]:
        tasks = []
        if api_key:
            tasks.append(self.discover_openai_models(api_key))
        if groq_key:
            tasks.append(self.discover_groq_models(groq_key))
        if deepseek_key:
            tasks.append(self.discover_deepseek_models(deepseek_key))
        tasks.append(self.discover_ollama_models(ollama_url))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_models: list[ModelInfo] = []
        for r in results:
            if isinstance(r, list):
                all_models.extend(r)
        return all_models

    async def test_model(self, provider: str, model_name: str, api_key: str = "",
                         base_url: str = "") -> BenchmarkResult:
        messages = [{"role": "user", "content": "Say 'hello' and count from 1 to 10."}]
        start = time.time()
        try:
            url = base_url or "https://api.openai.com/v1"
            if provider == "groq":
                url = "https://api.groq.com/openai/v1"
            elif provider == "deepseek":
                url = "https://api.deepseek.com/v1"
            elif provider == "ollama":
                url = base_url or "http://localhost:11434"
            if provider == "ollama":
                chat_url = url.rstrip("/") + "/api/chat"
                payload = {"model": model_name, "messages": messages, "stream": False}
                headers = {}
            else:
                chat_url = url.rstrip("/") + "/chat/completions"
                payload = {"model": model_name, "messages": messages, "max_tokens": 50, "temperature": 0.0}
                headers = {"Authorization": f"Bearer {api_key}"}
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(chat_url, json=payload, headers=headers)
                elapsed = (time.time() - start) * 1000
                if resp.status_code == 200:
                    data = resp.json()
                    if provider == "ollama":
                        output = data.get("message", {}).get("content", "")
                        in_tokens = data.get("prompt_eval_count", 0)
                        out_tokens = data.get("eval_count", len(output.split()))
                    else:
                        output = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                        usage = data.get("usage", {})
                        in_tokens = usage.get("prompt_tokens", 0)
                        out_tokens = usage.get("completion_tokens", len(output.split()))
                    tps = out_tokens / (elapsed / 1000) if elapsed > 0 else 0
                    result = BenchmarkResult(
                        model_id=model_name, provider=provider,
                        latency_ms=round(elapsed, 1),
                        tokens_per_sec=round(tps, 1),
                        input_tokens=in_tokens, output_tokens=out_tokens,
                    )
                else:
                    result = BenchmarkResult(
                        model_id=model_name, provider=provider,
                        latency_ms=round(elapsed, 1), tokens_per_sec=0,
                        error=f"HTTP {resp.status_code}: {resp.text[:100]}", success=False,
                    )
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            result = BenchmarkResult(
                model_id=model_name, provider=provider,
                latency_ms=round(elapsed, 1), tokens_per_sec=0,
                error=str(e)[:200], success=False,
            )
        key = f"{provider}/{model_name}"
        self._benchmarks[key] = result
        return result

    def recommend(self, model_type: str = "code") -> dict:
        candidates = self._recommendation_order.get(model_type, self._recommendation_order.get("general", []))
        results = []
        best = None
        for mid in candidates:
            info = KNOWN_MODELS.get(mid, {})
            bench_key = f"{info.get('provider', '')}/{mid}"
            bench = self._benchmarks.get(bench_key)
            entry = {
                "id": mid,
                "provider": info.get("provider", ""),
                "context_window": info.get("context_window", 0),
                "speed_tier": info.get("speed_tier", ""),
                "input_price": info.get("input_price", 0.0),
                "output_price": info.get("output_price", 0.0),
                "benchmark_latency_ms": bench.latency_ms if bench else None,
                "benchmark_tps": bench.tokens_per_sec if bench else None,
            }
            results.append(entry)
            if best is None:
                best = entry
        return {"type": model_type, "recommended": best, "alternatives": results}

    def cache_results(self, ttl_seconds: int = 3600) -> int:
        count = 0
        now = time.time()
        for key, ttl in list(self._cache_ttl.items()):
            if now >= ttl:
                self._cache.pop(key, None)
                self._cache_ttl.pop(key, None)
                count += 1
        return count

    def get_cache_age(self) -> dict:
        now = time.time()
        ages = {}
        for key, ttl in self._cache_ttl.items():
            remaining = max(0, ttl - now)
            ages[key] = round(remaining, 1)
        return ages

    async def list_models(self, base_url: str, api_key: str, provider: str = "openai", timeout: float = 10.0) -> list[ModelInfo]:
        cache_key = f"{base_url}:{api_key[:8]}"
        if cache_key in self._cache and time.time() < self._cache_ttl.get(cache_key, 0):
            return self._cache[cache_key]
        models = await self.discover_openai_models(api_key, base_url, provider)
        self._cache[cache_key] = models
        self._cache_ttl[cache_key] = time.time() + 3600
        return models

    def get_context_limit(self, model_id: str, user_max_tokens: int = 0) -> dict:
        info = KNOWN_MODELS.get(model_id, {"context_window": 128000})
        hard_limit = info.get("context_window", info.get("max_tokens", 128000))
        effective = min(user_max_tokens, hard_limit) if user_max_tokens > 0 else hard_limit
        return {
            "model_limit": hard_limit,
            "user_setting": user_max_tokens,
            "effective": effective,
            "compaction_threshold": int(effective * 0.85),
        }


model_discovery = ModelDiscovery()