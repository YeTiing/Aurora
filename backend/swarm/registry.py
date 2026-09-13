# -*- coding: utf-8 -*-
"Backend registry — detect and select best available backend."
from __future__ import annotations
import logging
from .backends import InProcessBackend, TerminalBackend, TmuxBackend, SwarmBackend, BackendKind, BackendConfig

logger = logging.getLogger("aurora.swarm.registry")

class BackendRegistry:
    def __init__(self):
        self._backends = {BackendKind.IN_PROCESS: InProcessBackend()}
        if TerminalBackend().is_available():
            self._backends[BackendKind.TERMINAL] = TerminalBackend()
        # tmux 后端此前只在枚举里声明、从未注册，按 kind 取会拿到 None
        if TmuxBackend().is_available():
            self._backends[BackendKind.TMUX] = TmuxBackend()
    def get(self, kind=None):
        if kind and kind in self._backends: return self._backends[kind]
        return self._backends.get(BackendKind.IN_PROCESS)
    def get_best(self, prefer_terminal=False):
        # 偏好顺序：tmux（可重连）> 独立终端 > 进程内。
        # tmux 具备 reconnection 能力，长时间运行的 agent 优先。
        if prefer_terminal:
            for kind in (BackendKind.TMUX, BackendKind.TERMINAL):
                if kind in self._backends:
                    return self._backends[kind]
        return self._backends.get(BackendKind.IN_PROCESS)
    def available_backends(self):
        return list(self._backends.keys())
    def register(self, kind, backend):
        self._backends[kind] = backend

_registry = None
def get_backend_registry():
    global _registry
    if _registry is None: _registry = BackendRegistry()
    return _registry