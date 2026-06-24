# 多Agent 编排器 — 父子DAG + spawn/send/wait/close + 任务队列
from __future__ import annotations
import asyncio, uuid, time, traceback
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine

class AgentStatus(Enum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING = "waiting"
    DONE = "done"
    ERROR = "error"
    CLOSED = "closed"

@dataclass
class AgentNode:
    id: str
    name: str
    parent_id: str | None = None
    status: AgentStatus = AgentStatus.IDLE
    task: str = ""
    result: str = ""
    error: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: float = 0
    finished_at: float = 0
    children: list[str] = field(default_factory=list)
    messages: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    priority: int = 0

class MultiAgentOrchestrator:
    """Agent 编排器：管理 agent DAG、并发控制、消息路由"""

    def __init__(self, max_parallel: int = 4, default_timeout: float = 300):
        self.max_parallel = max_parallel
        self.default_timeout = default_timeout
        self.agents: dict[str, AgentNode] = {}
        self._running: set[str] = set()
        self._pending: set[str] = set()
        self._lock = asyncio.Lock()
        self._task_registry: dict[str, asyncio.Task] = {}
        self._executor_registry: dict[str, Callable[[AgentNode], Coroutine]] = {}

    async def spawn(self, parent_id: str | None, name: str, task: str, priority: int = 0, metadata: dict | None = None) -> AgentNode:
        # Use swarm backend when available
        try:
            from backend.swarm import get_backend_registry, AgentContext as SwarmCtx, InProcessBackend
            registry = get_backend_registry()
            backend = registry.get_best()
            agent_id = f"agent_{uuid.uuid4().hex[:8]}"
            ctx = SwarmCtx(agent_id=agent_id, name=name, task=task, parent_id=parent_id or "", priority=priority, metadata=metadata or {})
            # Store backend reference for later use
            if not hasattr(self, "_swarm_backend"):
                self._swarm_backend = backend
        except ImportError:
            pass

        return await self._spawn_internal(parent_id, name, task, priority, metadata)

    async def _spawn_internal(self, parent_id: str | None, name: str, task: str, priority: int = 0, metadata: dict | None = None) -> AgentNode:
        agent = AgentNode(id=f"agent_{uuid.uuid4().hex[:8]}", name=name, parent_id=parent_id, task=task, priority=priority, metadata=metadata or {})
        async with self._lock:
            self.agents[agent.id] = agent
            if parent_id and parent_id in self.agents:
                self.agents[parent_id].children.append(agent.id)
        return agent

    async def _wrap_run(self, agent: AgentNode, executor: Callable[[AgentNode], Coroutine], agent_id: str | None = None):
        """Execute agent task, handle result/error, and drain queue."""
        aid = agent_id or agent.id
        try:
            result = await executor(agent)
            agent.result = str(result)[:10000] if result else ""
            agent.status = AgentStatus.DONE
        except Exception as e:
            agent.error = f"{type(e).__name__}: {str(e)[:500]}"
            agent.status = AgentStatus.ERROR
        finally:
            agent.finished_at = time.time()
            async with self._lock:
                self._running.discard(aid)
            await self._drain_queue()

    async def start(self, agent_id: str, executor: Callable[[AgentNode], Coroutine]):
        agent = self.agents.get(agent_id)
        if not agent: return
        async with self._lock:
            if len(self._running) >= self.max_parallel:
                self._pending.add(agent_id)
                agent.status = AgentStatus.WAITING
                self._executor_registry[agent_id] = executor
                return
            self._running.add(agent_id)
            agent.status = AgentStatus.RUNNING
            agent.started_at = time.time()

        task = asyncio.create_task(self._wrap_run(agent, executor, agent_id))
        self._task_registry[agent_id] = task

    async def _drain_queue(self):
        async with self._lock:
            if not self._pending or len(self._running) >= self.max_parallel:
                return
            next_ids = sorted(self._pending, key=lambda aid: -self.agents.get(aid, AgentNode(id="", name="")).priority)
            while next_ids and len(self._running) < self.max_parallel:
                aid = next_ids.pop(0)
                self._pending.discard(aid)
                agent = self.agents.get(aid)
                if agent and agent.status == AgentStatus.WAITING:
                    self._running.add(aid)
                    agent.status = AgentStatus.RUNNING
                    agent.started_at = time.time()
                    executor = self._executor_registry.pop(aid, None)
                    if executor:
                        asyncio.create_task(self._wrap_run(agent, executor))

    async def send(self, target_id: str, message: str, interrupt: bool = False) -> bool:
        agent = self.agents.get(target_id)
        if not agent or agent.status == AgentStatus.CLOSED: return False
        agent.messages.append({"role":"user","content":message,"ts":time.time()})
        if interrupt and agent.status not in (AgentStatus.RUNNING, AgentStatus.DONE):
            return True  # Will be picked up on next start
        return True

    async def wait(self, target_ids: list[str], timeout: float | None = None) -> list[AgentNode]:
        timeout = timeout or self.default_timeout
        deadline = time.time() + timeout
        while time.time() < deadline:
            all_ready = all(
                self.agents.get(tid, AgentNode(id="",name="")).status in (AgentStatus.DONE, AgentStatus.ERROR, AgentStatus.CLOSED)
                for tid in target_ids
            )
            if all_ready: break
            await asyncio.sleep(0.5)
        return [self.agents[tid] for tid in target_ids if tid in self.agents]

    async def close(self, target_id: str, cascade: bool = True) -> bool:
        agent = self.agents.get(target_id)
        if not agent: return False
        if cascade:
            for cid in agent.children:
                await self.close(cid, cascade=True)
        task = self._task_registry.pop(target_id, None)
        if task and not task.done(): task.cancel()
        agent.status = AgentStatus.CLOSED
        async with self._lock:
            self._running.discard(target_id)
            self._pending.discard(target_id)
        await self._drain_queue()
        return True

    def get_tree(self, root_id: str | None = None) -> dict:
        if root_id:
            roots = [self.agents[root_id]] if root_id in self.agents else []
        else:
            roots = [a for a in self.agents.values() if not a.parent_id]
        def node_tree(a: AgentNode) -> dict:
            return {"id":a.id,"name":a.name,"status":a.status.value,"task":a.task[:100],
                    "result":a.result[:200],"error":a.error[:200],"children":[node_tree(self.agents[c]) for c in a.children if c in self.agents]}
        return {"roots":[node_tree(r) for r in roots],"total":len(self.agents),"running":len(self._running),"pending":len(self._pending)}

    def list_agents(self, status_filter: AgentStatus | None = None) -> list[AgentNode]:
        agents = list(self.agents.values())
        return [a for a in agents if not status_filter or a.status == status_filter]

    def stats(self) -> dict:
        statuses = {}
        for a in self.agents.values():
            statuses[a.status.value] = statuses.get(a.status.value, 0) + 1
        return {"total":len(self.agents),"by_status":statuses,"max_parallel":self.max_parallel,"running":len(self._running)}

    async def cleanup(self):
        """Cancel all running tasks and clear state. Call at teardown."""
        for aid, task in list(self._task_registry.items()):
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._task_registry.clear()
        self._running.clear()
        self._pending.clear()
        self._executor_registry.clear()
        for agent in self.agents.values():
            if agent.status not in (AgentStatus.DONE, AgentStatus.ERROR, AgentStatus.CLOSED):
                agent.status = AgentStatus.CLOSED

orchestrator = MultiAgentOrchestrator()