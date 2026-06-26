# Aurora 插件系统 v2 — .codex-plugin/plugin.json + marketplace + capabilities
"""兼容 Codex 插件格式的插件系统"""
from __future__ import annotations
import importlib, importlib.util, json, sys, asyncio, time, hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# ── 插件清单 — .codex-plugin/plugin.json 格式 ──
sys.stdout.reconfigure(encoding='utf-8')
@dataclass
class PluginInterface:
    displayName: str = ""
    shortDescription: str = ""
    longDescription: str = ""
    developerName: str = ""
    category: str = ""
    capabilities: list[str] = field(default_factory=list)  # Interactive, Read, Write
    websiteURL: str = ""
    privacyPolicyURL: str = ""
    termsOfServiceURL: str = ""
    defaultPrompt: list[str] = field(default_factory=list)
    brandColor: str = "#58a6ff"
    composerIcon: str = ""
    logo: str = ""
    screenshots: list[str] = field(default_factory=list)

@dataclass
class PluginAuthor:
    name: str = ""
    email: str = ""
    url: str = ""

@dataclass
class PluginManifest:
    name: str
    version: str
    description: str = ""
    author: PluginAuthor = field(default_factory=PluginAuthor)
    homepage: str = ""
    repository: str = ""
    license: str = ""
    keywords: list[str] = field(default_factory=list)
    skills: str = "./skills/"
    interface: PluginInterface = field(default_factory=PluginInterface)
    entry_point: str = "main.py"
    permissions: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    bundledContentVariant: str = ""

    @classmethod
    def from_codex_plugin(cls, data: dict) -> "PluginManifest":
        author_data = data.get("author", {})
        if isinstance(author_data, str): author_data = {"name": author_data}
        interface_data = data.get("interface", {})

        return cls(
            name=data.get("name", ""),
            version=data.get("version", "0.1.0"),
            description=data.get("description", ""),
            author=PluginAuthor(**{k: author_data.get(k, "") for k in ["name", "email", "url"]}),
            homepage=data.get("homepage", ""),
            repository=data.get("repository", ""),
            license=data.get("license", ""),
            keywords=data.get("keywords", []),
            skills=data.get("skills", "./skills/"),
            interface=PluginInterface(**{k: interface_data.get(k, v.default if isinstance(v, dc_field()) else v)
                                         for k, v in PluginInterface.__dataclass_fields__.items()}),
            permissions=data.get("permissions", []),
            dependencies=data.get("dependencies", []),
            bundledContentVariant=data.get("bundledContentVariant", ""),
        )

# ── Marketplace 条目 ──
@dataclass
class MarketplaceEntry:
    name: str
    source: dict  # { source: "local", path: "./plugins/xxx" }
    policy: dict = field(default_factory=lambda: {"installation": "AVAILABLE", "authentication": "ON_INSTALL"})
    category: str = ""
    displayName: str = ""

    @classmethod
    def from_codex(cls, data: dict) -> "MarketplaceEntry":
        return cls(
            name=data.get("name", ""),
            source=data.get("source", {}),
            policy=data.get("policy", {}),
            category=data.get("category", ""),
            displayName=data.get("displayName", ""),
        )

@dataclass
class Marketplace:
    name: str
    plugins: list[MarketplaceEntry] = field(default_factory=list)
    displayName: str = ""
    source: dict = field(default_factory=dict)

# ── 插件实例 ──
@dataclass
class PluginInstance:
    manifest: PluginManifest
    path: Path
    module: Any = None
    loaded: bool = False
    enabled: bool = True
    load_time: float = 0
    version: str = ""

class PluginManager:
    """插件管理器 v2 — 支持 .codex-plugin/ 和 marketplace"""

    AVAILABLE_PERMISSIONS = {"filesystem", "network", "shell", "sandbox", "env", "git", "interactive", "read", "write"}

    def __init__(self, plugin_dirs: list[str] | None = None, marketplace_path: str | None = None):
        self.plugin_dirs = plugin_dirs or ["./plugins", "~/.aurora/plugins"]
        self.plugins: dict[str, PluginInstance] = {}
        self.marketplaces: dict[str, Marketplace] = {}
        self._hooks: dict[str, list[Callable]] = {}
        self._marketplace_path = Path(marketplace_path) if marketplace_path else Path.home() / ".aurora" / "plugins" / "marketplace.json"
        self.discover()
        self.load_marketplaces()

    def discover(self):
        for d in self.plugin_dirs:
            p = Path(d).expanduser().resolve()
            if not p.exists():
                continue
            for item in p.iterdir():
                if not item.is_dir():
                    continue
                # .codex-plugin/plugin.json 优先
                manifest_file = item / ".codex-plugin" / "plugin.json"
                if not manifest_file.exists():
                    manifest_file = item / "plugin.json"
                if manifest_file.exists():
                    try:
                        data = json.loads(manifest_file.read_text("utf-8"))
                        # 自动检测格式
                        if "interface" in data:
                            manifest = PluginManifest.from_codex_plugin(data)
                        else:
                            # Build kwargs from data, using defaults for missing fields
                            kwargs = {}
                            for k, v in PluginManifest.__dataclass_fields__.items():
                                if k in ("author", "interface"):
                                    continue
                                if k in data:
                                    kwargs[k] = data[k]
                            manifest = PluginManifest(**kwargs)
                            self.plugins[manifest.name] = PluginInstance(manifest=manifest, path=item, version=manifest.version)
                    except Exception as e:
                        import logging; logging.getLogger("aurora.plugins").debug(f"Plugin discovery error in {item}: {e}")

    def load_marketplaces(self):
        for d in self.plugin_dirs:
            mp_path = Path(d).expanduser().resolve() / "marketplace.json"
            if mp_path.exists():
                try:
                    data = json.loads(mp_path.read_text("utf-8"))
                    name = data.get("name", mp_path.parent.name)
                    entries = [MarketplaceEntry.from_codex(e) for e in data.get("plugins", [])]
                    self.marketplaces[name] = Marketplace(name=name, plugins=entries, displayName=data.get("displayName", ""), source=data.get("source", {}))
                except Exception:
                    pass

    def load(self, name: str) -> bool:
        pi = self.plugins.get(name)
        if not pi or pi.loaded:
            return False
        if not self._check_permissions(pi.manifest):
            return False

        entry = pi.path / pi.manifest.entry_point
        if not entry.exists():
            entry = pi.path / "main.py"

        if not entry.exists():
            return False

        try:
            spec = importlib.util.spec_from_file_location(f"aurora_plugin_{name}", str(entry))
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                sys.modules[f"aurora_plugin_{name}"] = mod
                spec.loader.exec_module(mod)
                pi.module = mod
                pi.loaded = True
                pi.load_time = time.time()
                self._register_hooks(pi)
                return True
        except Exception:
            return False
        return False

    def unload(self, name: str) -> bool:
        pi = self.plugins.get(name)
        if not pi or not pi.loaded:
            return False
        self._unregister_hooks(pi)
        sys.modules.pop(f"aurora_plugin_{name}", None)
        pi.module = None
        pi.loaded = False
        return True

    def reload(self, name: str) -> bool:
        self.unload(name)
        importlib.invalidate_caches()
        return self.load(name)

    def enable(self, name: str):
        pi = self.plugins.get(name)
        if pi:
            pi.enabled = True
            if not pi.loaded:
                self.load(name)

    def disable(self, name: str):
        pi = self.plugins.get(name)
        if pi:
            if pi.loaded:
                self.unload(name)
            pi.enabled = False

    def _check_permissions(self, m: PluginManifest) -> bool:
        for perm in m.permissions:
            if perm not in self.AVAILABLE_PERMISSIONS:
                return False
        return True

    def _register_hooks(self, pi: PluginInstance):
        for hn in ("on_startup", "on_shutdown", "on_tool_call", "on_response", "on_file_save"):
            h = getattr(pi.module, hn, None)
            if h:
                self._hooks.setdefault(hn, []).append(h)

    def _unregister_hooks(self, pi: PluginInstance):
        mod_name = f"aurora_plugin_{pi.manifest.name}"
        for hn, handlers in self._hooks.items():
            self._hooks[hn] = [h for h in handlers if getattr(h, "__module__", "") != mod_name]

    async def fire(self, hook: str, *args, **kwargs):
        results = []
        for h in self._hooks.get(hook, []):
            try:
                if asyncio.iscoroutinefunction(h):
                    r = await h(*args, **kwargs)
                else:
                    r = h(*args, **kwargs)
                results.append(r)
            except Exception:
                pass
        return results

    def list_all(self) -> list[dict]:
        return [{
            "name": p.manifest.name, "version": p.manifest.version,
            "displayName": p.manifest.interface.displayName or p.manifest.name,
            "loaded": p.loaded, "enabled": p.enabled,
            "description": p.manifest.description,
            "capabilities": p.manifest.interface.capabilities,
            "category": p.manifest.interface.category,
            "brandColor": p.manifest.interface.brandColor,
        } for p in self.plugins.values()]

    def scaffold(self, name: str, dir_path: str, display_name: str = "") -> str:
        """创建 .codex-plugin 格式插件骨架"""
        base = Path(dir_path) / name
        codex_plugin_dir = base / ".codex-plugin"
        codex_plugin_dir.mkdir(parents=True, exist_ok=True)

        if not display_name:
            display_name = name.replace("-", " ").title()

        manifest = {
            "name": name,
            "version": "0.1.0",
            "description": f"{display_name} plugin for Aurora",
            "author": {"name": ""},
            "interface": {
                "displayName": display_name,
                "shortDescription": f"{display_name} plugin",
                "longDescription": f"{display_name} plugin for Aurora Agent",
                "category": "Engineering",
                "capabilities": ["Read", "Write"],
                "brandColor": "#58a6ff",
            },
            "skills": "./skills/",
            "permissions": ["filesystem"],
        }
        (codex_plugin_dir / "plugin.json").write_text(json.dumps(manifest, indent=2), "utf-8")

        # Create main.py
        (base / "main.py").write_text(f'"""Plugin: {name}"""\n\ndef on_startup():\n    print("[{name}] loaded")\n\ndef on_shutdown():\n    print("[{name}] unloaded")\n', "utf-8")

        # Create skills dir
        (base / "skills").mkdir(exist_ok=True)

        # Create marketplace entry
        self._add_marketplace_entry(name, display_name, manifest["interface"]["category"])

        return str(base)

    def _add_marketplace_entry(self, name: str, display_name: str, category: str):
        entry = {
            "name": name,
            "source": {"source": "local", "path": f"./plugins/{name}"},
            "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
            "category": category,
            "displayName": display_name,
        }

        mp_path = Path.home() / ".aurora" / "plugins" / "marketplace.json"
        mp_path.parent.mkdir(parents=True, exist_ok=True)

        if mp_path.exists():
            data = json.loads(mp_path.read_text("utf-8"))
        else:
            data = {"name": "personal", "displayName": "Personal", "plugins": []}

        existing = [e for e in data.get("plugins", []) if e.get("name") == name]
        if not existing:
            data.setdefault("plugins", []).append(entry)
            mp_path.write_text(json.dumps(data, indent=2), "utf-8")


plugin_manager = PluginManager()