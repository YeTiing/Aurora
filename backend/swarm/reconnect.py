# -*- coding: utf-8 -*-
"Reconnection manager for swarm agents."
from __future__ import annotations
import asyncio, logging, time
from dataclasses import dataclass

logger = logging.getLogger("aurora.swarm.reconnect")

@dataclass
class ReconnectState:
    agent_id: str = ""
    backend: str = ""
    last_seen: float = 0.0
    retry_count: int = 0
    max_retries: int = 3
    backoff_sec: float = 1.0
    reconnecting: bool = False

class ReconnectionManager:
    def __init__(self):
        self._states = {}
    def register(self, agent_id, backend):
        self._states[agent_id] = ReconnectState(agent_id=agent_id, backend=backend, last_seen=time.time())
    def heartbeat(self, agent_id):
        s = self._states.get(agent_id)
        if s: s.last_seen = time.time()
    def check_timeout(self, timeout_sec=30):
        stale = []
        now = time.time()
        for aid, s in self._states.items():
            if now - s.last_seen > timeout_sec and not s.reconnecting:
                stale.append(aid)
        return stale
    async def reconnect(self, agent_id, spawn_fn):
        s = self._states.get(agent_id)
        if not s or s.retry_count >= s.max_retries: return None
        s.reconnecting = True
        s.retry_count += 1
        await asyncio.sleep(s.backoff_sec * (2 ** (s.retry_count - 1)))
        try:
            result = await spawn_fn(agent_id)
            s.reconnecting = False
            s.retry_count = 0
            s.last_seen = time.time()
            return result
        except Exception as e:
            logger.error(f"Reconnect failed for {agent_id}: {e}")
            s.reconnecting = False
            return None
    def remove(self, agent_id):
        self._states.pop(agent_id, None)