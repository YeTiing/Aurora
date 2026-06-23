# coding: utf-8
"""Plugin Marketplace - install, uninstall, search plugins."""
from __future__ import annotations
import json, os, shutil, subprocess, time, re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PluginEntry:
    id: str
    name: str
    version: str
    description: str
    author: str
    tags: list[str] = field(default_factory=list)
    installed: bool = False
    repo_url: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "version": self.version,
            "description": self.description, "author": self.author,
            "tags": self.tags, "installed": self.installed,
            "repo_url": self.repo_url,
        }


class PluginMarketplace:
    """Manages plugin discovery, installation, and removal."""

    def __init__(self, plugins_dir: str | None = None, registry_path: str | None = None):
        if plugins_dir is None:
            plugins_dir = str(Path(__file__).parent.parent / "plugins")
        if registry_path is None:
            registry_path = str(Path(__file__).parent / "plugin_registry.json")
        self.plugins_dir = Path(plugins_dir)
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.registry_path = Path(registry_path)
        self._registry: dict[str, PluginEntry] = {}
        self._load_registry()

    def _load_registry(self):
        self._registry = {}
        if self.registry_path.exists():
            data = json.loads(self.registry_path.read_bytes().decode("utf-8-sig"))
            for p in data.get("plugins", []):
                entry = PluginEntry(
                    id=p["id"], name=p["name"], version=p.get("version", "0.1.0"),
                    description=p.get("description", ""), author=p.get("author", ""),
                    tags=p.get("tags", []), repo_url=p.get("repo_url", ""),
                )
                entry.installed = (self.plugins_dir / p["id"] / "plugin.json").exists()
                self._registry[p["id"]] = entry

    def _save_registry(self):
        data = {
            "plugins": [
                {
                    "id": e.id, "name": e.name, "version": e.version,
                    "description": e.description, "author": e.author,
                    "tags": e.tags, "repo_url": e.repo_url,
                }
                for e in self._registry.values()
            ],
        }
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self.registry_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def list_available(self) -> list[dict]:
        """List all plugins (registry + locally discovered)."""
        self._load_registry()
        local_ids = set()
        for item in self.plugins_dir.iterdir():
            if item.is_dir():
                pj = item / "plugin.json"
                if pj.exists():
                    try:
                        mani = json.loads(pj.read_text(encoding="utf-8"))
                        pid = mani.get("name", item.name)
                        local_ids.add(pid)
                        if pid in self._registry:
                            self._registry[pid].installed = True
                    except Exception:
                        pass
        result = []
        for e in self._registry.values():
            d = e.to_dict()
            e.installed = (self.plugins_dir / e.id / "plugin.json").exists()
            d["installed"] = e.installed
            result.append(d)
        for pid in sorted(local_ids - set(self._registry.keys())):
            pj = self.plugins_dir / pid / "plugin.json"
            try:
                mani = json.loads(pj.read_text(encoding="utf-8"))
                result.append({
                    "id": pid, "name": mani.get("name", pid),
                    "version": mani.get("version", "0.1.0"),
                    "description": mani.get("description", ""),
                    "author": mani.get("author", {}).get("name", "") if isinstance(mani.get("author"), dict) else "",
                    "tags": mani.get("keywords", []),
                    "installed": True,
                    "repo_url": "",
                })
            except Exception:
                pass
        return result

    def install_from_github(self, repo_url: str) -> dict:
        """Clone a plugin repo into plugins/{name}/."""
        name = repo_url.rstrip("/").split("/")[-1].replace(".git", "")
        target = self.plugins_dir / name
        if target.exists():
            return {"success": False, "error": f"Plugin {name} already exists at {target}"}
        try:
            result = subprocess.run(
                ["git", "clone", "--depth", "1", repo_url, str(target)],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                return {"success": False, "error": result.stderr[:500]}
        except FileNotFoundError:
            return {"success": False, "error": "git not found in PATH"}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "Clone timed out after 60s"}
        pj = target / "plugin.json"
        version = "0.1.0"
        desc = ""
        author = ""
        tags = []
        if pj.exists():
            try:
                mani = json.loads(pj.read_text(encoding="utf-8"))
                version = mani.get("version", version)
                desc = mani.get("description", desc)
                tags = mani.get("keywords", [])
                auth_obj = mani.get("author", {})
                if isinstance(auth_obj, dict):
                    author = auth_obj.get("name", "")
                elif isinstance(auth_obj, str):
                    author = auth_obj
            except Exception:
                pass
        if name not in self._registry:
            self._registry[name] = PluginEntry(
                id=name, name=name, version=version,
                description=desc, author=author, tags=tags,
                installed=True, repo_url=repo_url,
            )
            self._save_registry()
        return {"success": True, "name": name, "path": str(target), "version": version}

    def uninstall(self, plugin_name: str) -> dict:
        """Remove plugin directory."""
        target = self.plugins_dir / plugin_name
        if not target.exists():
            return {"success": False, "error": f"Plugin {plugin_name} not found"}
        try:
            shutil.rmtree(str(target))
        except Exception as e:
            return {"success": False, "error": str(e)}
        if plugin_name in self._registry:
            self._registry[plugin_name].installed = False
            self._save_registry()
        return {"success": True, "name": plugin_name}

    def check_updates(self, plugin_name: str) -> dict:
        """Compare local version vs registry version."""
        target = self.plugins_dir / plugin_name
        pj = target / "plugin.json"
        if not pj.exists():
            return {"name": plugin_name, "installed": False, "update_available": False}
        try:
            mani = json.loads(pj.read_text(encoding="utf-8"))
            local_ver = mani.get("version", "0.0.0")
        except Exception:
            local_ver = "0.0.0"
        reg_ver = "0.0.0"
        if plugin_name in self._registry:
            reg_ver = self._registry[plugin_name].version
        return {
            "name": plugin_name,
            "installed": True,
            "local_version": local_ver,
            "registry_version": reg_ver,
            "update_available": self._version_gt(reg_ver, local_ver),
        }

    def search(self, query: str) -> list[dict]:
        """Search plugins by name or description."""
        q = query.lower()
        results = []
        for entry in self._registry.values():
            if q in entry.name.lower() or q in entry.description.lower() or any(q in t.lower() for t in entry.tags):
                results.append(entry.to_dict())
        return results

    @staticmethod
    def _version_gt(a: str, b: str) -> bool:
        """Compare semver strings: is a > b?"""
        try:
            pa = [int(x) for x in a.split(".")]
            pb = [int(x) for x in b.split(".")]
            while len(pa) < 3:
                pa.append(0)
            while len(pb) < 3:
                pb.append(0)
            for i in range(3):
                if pa[i] > pb[i]:
                    return True
                if pa[i] < pb[i]:
                    return False
            return False
        except Exception:
            return False


_marketplace: PluginMarketplace | None = None


def get_marketplace() -> PluginMarketplace:
    global _marketplace
    if _marketplace is None:
        _marketplace = PluginMarketplace()
    return _marketplace