# -*- coding: utf-8 -*-
"Swarm Backends — agent execution backends."
from __future__ import annotations
import asyncio, logging, os, subprocess, sys, json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger("aurora.swarm.backends")

class BackendKind(str, Enum):
    IN_PROCESS = "in_process"
    TERMINAL = "terminal"
    TMUX = "tmux"
    REMOTE = "remote"

@dataclass
class BackendCapabilities:
    independent_terminal: bool = False
    visual_layout: bool = False
    permission_sync: bool = False
    reconnection: bool = False

@dataclass
class BackendConfig:
    kind: BackendKind = BackendKind.IN_PROCESS
    cwd: str = ""
    env: dict = field(default_factory=dict)
    shell: str = ""
    window_title: str = ""
    layout: str = "stacked"

@dataclass
class AgentContext:
    agent_id: str = ""
    name: str = ""
    task: str = ""
    parent_id: str = ""
    priority: int = 0
    metadata: dict = field(default_factory=dict)
    backend_config: BackendConfig = field(default_factory=BackendConfig)

class SwarmBackend(ABC):
    def __init__(self, config=None):
        self.config = config or BackendConfig()
    @property
    @abstractmethod
    def kind(self) -> BackendKind: ...
    @property
    @abstractmethod
    def capabilities(self) -> BackendCapabilities: ...
    @abstractmethod
    async def spawn(self, ctx, runner): ...
    @abstractmethod
    async def send_message(self, agent_id, message): ...
    @abstractmethod
    async def stop_agent(self, agent_id): ...
    @abstractmethod
    async def shutdown(self): ...
    def is_available(self) -> bool: return True

class InProcessBackend(SwarmBackend):
    def __init__(self, config=None):
        super().__init__(config or BackendConfig(kind=BackendKind.IN_PROCESS))
        self._agents = {}
        self._queues = {}
        self._stops = {}
    @property
    def kind(self): return BackendKind.IN_PROCESS
    @property
    def capabilities(self): return BackendCapabilities(permission_sync=True)
    async def spawn(self, ctx, runner):
        q = asyncio.Queue()
        stop = asyncio.Event()
        self._queues[ctx.agent_id] = q
        self._stops[ctx.agent_id] = stop
        task = asyncio.create_task(self._run(ctx, runner, q, stop), name=f"swarm-{ctx.agent_id}")
        self._agents[ctx.agent_id] = task
        logger.info(f"InProcess agent: {ctx.agent_id} ({ctx.name})")
        return {"agent_id": ctx.agent_id, "backend": self.kind.value}
    async def _run(self, ctx, runner, q, stop):
        try:
            ctx.metadata["_queue"] = q
            ctx.metadata["_stop"] = stop
            result = runner(ctx)
            if asyncio.iscoroutine(result): await result
        except asyncio.CancelledError: pass
        except Exception as e: logger.error(f"Agent {ctx.agent_id}: {e}")
        finally:
            self._agents.pop(ctx.agent_id, None)
            self._queues.pop(ctx.agent_id, None)
            self._stops.pop(ctx.agent_id, None)
    async def send_message(self, agent_id, message):
        q = self._queues.get(agent_id)
        if q: await q.put(message)
    async def stop_agent(self, agent_id):
        task = self._agents.get(agent_id)
        stop = self._stops.get(agent_id)
        if stop: stop.set()
        if task and not task.done():
            task.cancel()
            try: await task
            except asyncio.CancelledError: pass
    async def shutdown(self):
        for aid in list(self._agents): await self.stop_agent(aid)
    async def get_messages(self, agent_id):
        q = self._queues.get(agent_id)
        if not q: return []
        msgs = []
        while not q.empty():
            try: msgs.append(q.get_nowait())
            except asyncio.QueueEmpty: break
        return msgs

class TerminalBackend(SwarmBackend):
    def __init__(self, config=None):
        super().__init__(config or BackendConfig(kind=BackendKind.TERMINAL))
        self._procs = {}
    @property
    def kind(self): return BackendKind.TERMINAL
    @property
    def capabilities(self): return BackendCapabilities(independent_terminal=True, visual_layout=True)
    async def spawn(self, ctx, runner):
        title = self.config.window_title or f"Aurora: {ctx.name}"
        cwd = self.config.cwd or os.getcwd()
        ctx_json = json.dumps({"agent_id":ctx.agent_id,"name":ctx.name,"task":ctx.task,"parent_id":ctx.parent_id,"priority":ctx.priority})
        script_lines = [
            "import sys,asyncio,json,os",
            f"sys.path.insert(0,{json.dumps(cwd)})",
            f"ctx_data = json.loads({json.dumps(ctx_json)})",
            "print(f'Aurora Agent: {ctx_data[\"name\"]}')",
            "print(f'Task: {ctx_data[\"task\"][:120]}')",
            "print('Press Ctrl+C to stop')",
            "try:",
            "    while True: import time; time.sleep(3600)",
            "except KeyboardInterrupt: print('Stopped')",
        ]
        script = "; ".join(script_lines)
        if sys.platform == "win32":
            proc = subprocess.Popen(["start", title, "powershell", "-NoExit", "-Command", f"python -c \"{script}\""], shell=True)
        elif sys.platform == "darwin":
            proc = subprocess.Popen(["osascript", "-e", f'tell app "Terminal" to do script "python3 -c \'{script}\'"'])
        else:
            proc = subprocess.Popen(["xterm", "-title", title, "-e", f"python3 -c '{script}'"])
        self._procs[ctx.agent_id] = proc
        logger.info(f"Terminal agent: {ctx.agent_id} PID={proc.pid}")
        return {"agent_id": ctx.agent_id, "backend": self.kind.value, "pid": proc.pid}
    async def send_message(self, agent_id, message):
        proc = self._procs.get(agent_id)
        if proc and proc.stdin:
            proc.stdin.write((message+"\n").encode()); proc.stdin.flush()
    async def stop_agent(self, agent_id):
        proc = self._procs.pop(agent_id, None)
        if proc:
            try: proc.terminate(); proc.wait(5)
            except: proc.kill()
    async def shutdown(self):
        for aid in list(self._procs): await self.stop_agent(aid)
    def is_available(self):
        if sys.platform in ("win32", "darwin"): return True
        import shutil
        return any(shutil.which(t) for t in ["xterm","xfce4-terminal","gnome-terminal"])