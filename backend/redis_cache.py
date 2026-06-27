# Aurora Redis 缓存模块 — 会话缓存 + RAG 缓存 + 速率限制
from __future__ import annotations
import json, time, asyncio
from typing import Any
import logging
logger = logging.getLogger("aurora")

class RedisClient:
    """Redis 客户端封装 — 带内存降级"""

    def __init__(self, url: str = "redis://localhost:6379"):
        self.url = url
        self._redis = None
        self._fallback: dict[str, tuple[Any, float | None]] = {}

    async def _ensure(self):
        if self._redis is not None: return
        try:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(self.url, decode_responses=True)
            await self._redis.ping()
        except Exception:
            self._redis = False  # 标记为不可用，降级到内存

    async def get(self, key: str) -> Any | None:
        await self._ensure()
        if self._redis:
            try:
                val = await self._redis.get(key)
                return json.loads(val) if val else None
            except Exception:
                pass
        # 内存降级
        entry = self._fallback.get(key)
        if entry:
            val, expiry = entry
            if expiry and time.time() > expiry: return None
            return val
        return None

    async def set(self, key: str, value: Any, ttl: int | None = None):
        await self._ensure()
        if self._redis:
            try:
                s = json.dumps(value, ensure_ascii=False, default=str)
                if ttl:
                    await self._redis.setex(key, ttl, s)
                else:
                    await self._redis.set(key, s)
                return
            except Exception: logger.debug('redis ping failed', exc_info=True)
        expiry = time.time() + ttl if ttl else None
        self._fallback[key] = (value, expiry)

    async def delete(self, key: str):
        await self._ensure()
        if self._redis:
            try: await self._redis.delete(key); return
            except Exception: logger.debug('redis get failed', exc_info=True)
        self._fallback.pop(key, None)

    async def exists(self, key: str) -> bool:
        await self._ensure()
        if self._redis:
            try: return bool(await self._redis.exists(key))
            except Exception: logger.debug('redis set failed', exc_info=True)
        entry = self._fallback.get(key)
        if entry:
            _, expiry = entry
            if expiry and time.time() > expiry: return False
            return True
        return False

    async def incr(self, key: str, ttl: int = 60) -> int:
        await self._ensure()
        if self._redis:
            try:
                val = await self._redis.incr(key)
                await self._redis.expire(key, ttl)
                return val
            except Exception: logger.debug('redis delete failed', exc_info=True)
        val, _ = self._fallback.get(key, (0, None))
        val = val + 1
        self._fallback[key] = (val, time.time() + ttl)
        return val

redis_client = RedisClient()

# ── 会话缓存 ──
class SessionCache:
    PREFIX = "aurora:session:"
    TTL = 3600 * 24  # 24小时

    async def save(self, session_id: str, data: dict):
        await redis_client.set(f"{self.PREFIX}{session_id}", data, ttl=self.TTL)

    async def load(self, session_id: str) -> dict | None:
        return await redis_client.get(f"{self.PREFIX}{session_id}")

    async def delete(self, session_id: str):
        await redis_client.delete(f"{self.PREFIX}{session_id}")

session_cache = SessionCache()

# ── RAG 查询缓存 ──
class RAGCache:
    PREFIX = "aurora:rag:"
    TTL = 300  # 5分钟

    async def get(self, query_hash: str) -> list[dict] | None:
        return await redis_client.get(f"{self.PREFIX}{query_hash}")

    async def set(self, query_hash: str, results: list[dict]):
        await redis_client.set(f"{self.PREFIX}{query_hash}", results, ttl=self.TTL)

rag_cache = RAGCache()

# ── 速率限制 ──
class RateLimiter:
    def __init__(self, max_requests: int = 60, window_sec: int = 60):
        self.max_requests = max_requests
        self.window_sec = window_sec

    async def check(self, key: str) -> bool:
        count = await redis_client.incr(f"aurora:ratelimit:{key}", ttl=self.window_sec)
        return count <= self.max_requests

rate_limiter = RateLimiter()

# ── Pub/Sub 事件总线 ──
class EventBus:
    def __init__(self, channel: str = "aurora:events"):
        self.channel = channel
        self._local_handlers: list = []
        self._pubsub = None

    async def publish(self, event_type: str, data: dict):
        event = json.dumps({"type": event_type, "data": data, "ts": time.time()}, ensure_ascii=False, default=str)
        await redis_client._ensure()
        if redis_client._redis:
            try: await redis_client._redis.publish(self.channel, event)
            except Exception: logger.debug('redis cache_url failed', exc_info=True)
        for h in self._local_handlers:
            try: h(event_type, data)
            except Exception: logger.debug('redis get_cached_url failed', exc_info=True)

    def subscribe(self, handler):
        self._local_handlers.append(handler)

event_bus = EventBus()