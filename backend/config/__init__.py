# Aurora TOML/JSON 配置系统
from __future__ import annotations
import os, copy, json
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:
    tomllib = None


class Config:
    """Three-tier config: global < user < project, supports TOML + JSON"""

    def __init__(self, project_root: str | Path = "."):
        self.project_root = Path(project_root).resolve()
        self._global = self._load(self._global_path())
        self._user = self._load(self._user_path())
        self._project = self._load(self.project_root / "aurora.json")

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
        chain = [self._project, self._user, self._global]
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

    def all(self) -> dict:
        result = copy.deepcopy(self._global)
        self._deep_merge(result, self._user)
        self._deep_merge(result, self._project)
        return result

    # Convenience properties
    @property
    def llm_model(self) -> str:
        return self.get("llm.model", "gpt-4o")

    @property
    def llm_api_key(self) -> str:
        return self.get("llm.api_key", "")

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