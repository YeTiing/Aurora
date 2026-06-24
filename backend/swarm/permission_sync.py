# -*- coding: utf-8 -*-
"Permission sync between leader and swarm agents."
from __future__ import annotations
import asyncio, logging
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger("aurora.swarm.permissions")

@dataclass
class PermissionRequest:
    agent_id: str = ""
    tool: str = ""
    args: str = ""
    reason: str = ""
    timestamp: float = 0.0
    resolved: bool = False
    decision: str = ""

class PermissionSync:
    def __init__(self):
        self._pending = {}
        self._callbacks = {}
    async def request(self, agent_id, tool, args, reason=""):
        import time
        req = PermissionRequest(agent_id=agent_id, tool=tool, args=args, reason=reason, timestamp=time.time())
        self._pending[agent_id] = req
        cb = self._callbacks.get(agent_id)
        if cb:
            decision = await cb(req)
            req.resolved = True
            req.decision = decision
            return decision
        return "allow"
    def on_request(self, agent_id, callback):
        self._callbacks[agent_id] = callback
    def resolve(self, agent_id, decision):
        req = self._pending.get(agent_id)
        if req:
            req.resolved = True
            req.decision = decision
        self._pending.pop(agent_id, None)
    def get_pending(self):
        return list(self._pending.values())