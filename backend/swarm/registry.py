# -*- coding: utf-8 -*-
"Backend registry — detect and select best available backend."
from __future__ import annotations
import logging
from .backends import InProcessBackend, TerminalBackend, SwarmBackend, BackendKind, BackendConfig

logger = logging.getLogger("aurora.swarm.registry")

class BackendRegistry:
    def __init__(self):
        self._backends = {BackendKind.IN_PROCESS: InProcessBackend()}
        if TerminalBackend().is_available():
            self._backends[BackendKind.TERMINAL] = TerminalBackend()
    def get(self, kind=None):
        if kind and kind in self._backends: return self._backends[kind]
        return self._backends.get(BackendKind.IN_PROCESS)
    def get_best(self, prefer_terminal=False):
        if prefer_terminal and BackendKind.TERMINAL in self._backends:
            return self._backends[BackendKind.TERMINAL]
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