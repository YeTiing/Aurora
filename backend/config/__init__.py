# Aurora TOML/JSON 配置系统
from __future__ import annotations
import os, copy, json
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:
    tomllib = None


def _load_dotenv(project_root: Path) -> dict:
    """Load .env file from project root, return env overrides dict."""
    env_path = project_root / ".env"
    if not env_path.exists():
        return {}
    overrides = {}
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip("\"'")
                if val:
                    # Map .env vars to config keys
                    overrides[key] = val
    return _map_env_to_config(overrides)


def _map_env_to_config(env: dict) -> dict:
    """Map AURORA_ env vars to config keys."""
    result = {}
    mapping = {
        "AURORA_LLM_API_KEY": ("llm.api_key", "llm", "api_key"),
        "AURORA_LLM_MODEL": ("llm.model", "llm", "model"),
        "AURORA_LLM_BASE_URL": ("llm.base_url", "llm", "base_url"),
        "AURORA_LLM_PROVIDER": ("llm.provider", "llm", "provider"),
        "AURORA_VISION_API_KEY": ("vision_fallback.api_key", "vision_fallback", "api_key"),
        "AURORA_VISION_MODEL": ("vision_fallback.model", "vision_fallback", "model"),
        "AURORA_HOST": ("server.host", "server", "host"),
        "AURORA_PORT": ("server.port", "server", "port"),
    }
    for env_key, (_, section, field) in mapping.items():
        if env_key in env:
            result.setdefault(section, {})[field] = env[env_key]
    return result


class Config:
    """Three-tier config: global < user < project, supports TOML + JSON + .env"""

    def __init__(self, project_root: str | Path = ".", test_mode: bool = False):
        self.project_root = Path(project_root).resolve()
        self._test_mode = test_mode
        self._global = self._load(self._global_path())
        self._user = self._load(self._user_path())
        self._project = self._load(self.project_root / "aurora.json")
        # Load .env overrides (highest priority)
        self._env = _load_dotenv(self.project_root)

    @staticmethod
    def _global_path() -> Path:
        base = Path(os.environ.get("AURORA_HOME", Path.home() / ".aurora"))
        return base / "config.toml"

    @staticmethod
    def _user_path() -> Path:
        return Config._global_path()

    @staticmethod
    def _load(path: Path) -> dict:
        if not path.exists():
            return {}
        raw = path.read_bytes()
        # Strip BOM
        if raw[:3] == b'\xef\xbb\xbf':
            raw = raw[3:]
        # Try TOML
        if tomllib:
            try:
                data = tomllib.loads(raw.decode("utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        # Try JSON
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _deep_merge(self, base: dict, override: dict) -> dict:
        for k, v in override.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                base[k] = self._deep_merge(copy.deepcopy(base.get(k, {})), v)
            else:
                base[k] = v
        return base

    def get(self, key: str, default: Any = None) -> Any:
        # Priority: .env > project > user > global
        chain = [self._env, self._project, self._user, self._global]
        for section in chain:
            val = section
            for part in key.split("."):
                if isinstance(val, dict):
                    val = val.get(part)
                else:
                    val = None
                    break
            if val is not None:
                return val
        return default

    def set(self, key: str, value: Any) -> None:
        """Set a config value in the project-level config file."""
        parts = key.split(".")
        target = self._project
        for part in parts[:-1]:
            if part not in target:
                target[part] = {}
            target = target[part]
        target[parts[-1]] = value
        # Persist to project config file
        try:
            import json
            project_path = self._project_root / "aurora.json"
            with open(project_path, "w", encoding="utf-8") as f:
                json.dump(self._project, f, indent=2, ensure_ascii=False)
        except Exception:
            pass  # Non-fatal: in-memory change still applies

    def all(self) -> dict:
        result = copy.deepcopy(self._global)
        self._deep_merge(result, self._user)
        self._deep_merge(result, self._project)
        self._deep_merge(result, self._env)
        return result

    # Convenience properties
    @property
    def llm_model(self) -> str:
        return self.get("llm.model", "gpt-4o")

    @property
    def llm_api_key(self) -> str:
        key = self.get("llm.api_key", "")
        if not key:
            key = os.environ.get("AURORA_LLM_API_KEY", "")
        return key

    @property
    def llm_base_url(self) -> str:
        return self.get("llm.base_url", "https://api.openai.com/v1")

    @property
    def max_turn_iter(self) -> int:
        return self.get("agent.max_turn_iter", 30)

    @property
    def tool_timeout(self) -> int:
        return self.get("tools.timeout_sec", 30)

    @property
    def token_budget(self) -> int:
        return self.get("context.token_budget", 24000)

    @property
    def rag_enabled(self) -> bool:
        return self.get("rag.enabled", True)

    @property
    def skills_roots(self) -> list[str]:
        return self.get("skills.roots", ["./skills"])

    @property
    def sandbox_image(self) -> str:
        return self.get("sandbox.image", "aurora-sandbox:latest")

    @property
    def max_parallel_agents(self) -> int:
        return self.get("multi_agent.max_parallel", 4)

    @property
    def model_context_window(self) -> int:
        return self.get("model_context_window", 128000)

    @property
    def sandbox_mode(self) -> str:
        return self.get("sandbox_mode", "workspace-write")


config: Config | None = None


def init_config(project_root: str | Path = ".") -> Config:
    global config
    config = Config(project_root)
    return config
