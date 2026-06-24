# -*- coding: utf-8 -*-
"""Plugin Hot-Reload — watcher that auto-reloads plugins when files change.

Uses polling for cross-platform compatibility (no watchdog dependency).
Watches plugin directories for *.py changes, triggers reload automatically.
Also adds a reload-all-plugins CLI command.
"""

from __future__ import annotations
import asyncio, hashlib, logging, os, time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger("aurora.plugins.hotreload")


@dataclass
class PluginFileState:
    path: str = ""
    hash: str = ""
    mtime: float = 0.0


class PluginHotReload:
    """Watches plugin directories and auto-reloads changed plugins."""

    def __init__(self, plugin_dirs: list[str] = None, plugin_manager=None, poll_interval: float = 3.0):
        self._dirs = plugin_dirs or []
        self._manager = plugin_manager
        self._interval = poll_interval
        self._running = False
        self._task: asyncio.Task | None = None
        self._states: dict[str, PluginFileState] = {}
        self._reload_count = 0
        self._last_reload: float = 0.0
        self._reload_callbacks: list[Callable] = []
        self._debounce_sec: float = 1.0  # Debounce multiple rapid changes

    def set_manager(self, manager):
        self._manager = manager

    def add_dir(self, path: str):
        if path not in self._dirs:
            self._dirs.append(path)

    def on_reload(self, callback: Callable):
        self._reload_callbacks.append(callback)

    @property
    def reload_count(self) -> int:
        return self._reload_count

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._scan_all()
        self._task = asyncio.create_task(self._watch_loop())
        logger.info(f"Plugin hot-reload started: {len(self._dirs)} dirs, {len(self._states)} files")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Plugin hot-reload stopped")

    async def _watch_loop(self) -> None:
        while self._running:
            try:
                changed = self._scan_changes()
                if changed:
                    # Debounce: wait for quiet period
                    await asyncio.sleep(self._debounce_sec)
                    # Re-scan after debounce
                    changed2 = self._scan_changes()
                    if changed2:
                        await self._reload_changed(changed2)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Hot-reload watch error: {e}")
            await asyncio.sleep(self._interval)

    def _scan_all(self) -> None:
        """Initial scan of all plugin files."""
        self._states.clear()
        for d in self._dirs:
            if not os.path.isdir(d):
                continue
            for root, dirs, files in os.walk(d):
                dirs[:] = [dn for dn in dirs if dn != "__pycache__"]
                for fname in files:
                    if fname.endswith(".py"):
                        fp = os.path.join(root, fname)
                        self._states[fp] = self._state_of(fp)

    def _scan_changes(self) -> set[str]:
        """Find changed files. Returns set of changed plugin names."""
        changed_files = []
        for d in self._dirs:
            if not os.path.isdir(d):
                continue
            for root, dirs, files in os.walk(d):
                dirs[:] = [dn for dn in dirs if dn != "__pycache__"]
                for fname in files:
                    if fname.endswith(".py"):
                        fp = os.path.join(root, fname)
                        new_state = self._state_of(fp)
                        old_state = self._states.get(fp)
                        if old_state is None:
                            self._states[fp] = new_state
                            continue
                        if new_state.hash != old_state.hash:
                            self._states[fp] = new_state
                            changed_files.append(fp)

        # Map files to plugin names
        changed_plugins = set()
        if changed_files and self._manager:
            all_plugins = self._manager.list_all()
            for cf in changed_files:
                for p in all_plugins:
                    pdir = p.get("dir", "")
                    if pdir and cf.startswith(pdir):
                        changed_plugins.add(p.get("name", ""))
                        break

        return changed_plugins

    async def _reload_changed(self, plugin_names: set[str]) -> None:
        """Reload changed plugins."""
        if not self._manager:
            logger.warning("Hot-reload: no plugin manager attached")
            return

        for name in plugin_names:
            logger.info(f"Hot-reloading plugin: {name}")
            try:
                self._manager.reload(name)
                self._reload_count += 1
                self._last_reload = time.time()
            except Exception as e:
                logger.error(f"Failed to reload '{name}': {e}")

        if plugin_names:
            for cb in self._reload_callbacks:
                try:
                    result = cb(plugin_names)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    logger.debug(f"Hot-reload callback error: {e}")

    def reload_all(self) -> dict:
        """Reload all loaded plugins. Returns result."""
        if not self._manager:
            return {"error": "no plugin manager"}
        results = []
        plugins = self._manager.list_all()
        for p in plugins:
            name = p.get("name", "")
            if p.get("loaded", False):
                try:
                    self._manager.reload(name)
                    results.append({"plugin": name, "status": "reloaded"})
                except Exception as e:
                    results.append({"plugin": name, "status": f"error: {e}"})
        self._reload_count += 1
        self._last_reload = time.time()
        return {"reloaded": len(results), "results": results}

    @staticmethod
    def _state_of(filepath: str) -> PluginFileState:
        try:
            st = os.stat(filepath)
            content = open(filepath, "rb").read()
            return PluginFileState(
                path=filepath,
                hash=hashlib.sha256(content).hexdigest()[:16],
                mtime=st.st_mtime,
            )
        except OSError:
            return PluginFileState(path=filepath)

    def stats(self) -> dict:
        return {
            "running": self._running,
            "dirs": len(self._dirs),
            "files_watched": len(self._states),
            "reload_count": self._reload_count,
            "last_reload": self._last_reload,
        }


_hotreload: Optional[PluginHotReload] = None

def get_hotreload() -> PluginHotReload:
    global _hotreload
    if _hotreload is None:
        _hotreload = PluginHotReload()
    return _hotreload
