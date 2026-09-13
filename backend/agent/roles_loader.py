# Agent 角色加载器 — 读取 backend/agent/roles/*.toml 并注入 system prompt
# 修复: 之前 12 个角色 TOML 全项目零引用，角色系统形同虚设。
# 现在通过 RU(agent_role=...) / spawn_agent(role=...) 真正生效。
from __future__ import annotations
import logging
from pathlib import Path

logger = logging.getLogger("aurora")

_ROLES_DIR = Path(__file__).resolve().parent / "roles"
_cache: dict[str, dict] | None = None


def _load_all() -> dict[str, dict]:
    """加载所有角色 TOML。文件名(stem)是角色的 key，小写 + 连字符。"""
    global _cache
    if _cache is not None:
        return _cache
    roles: dict[str, dict] = {}
    if _ROLES_DIR.is_dir():
        for f in sorted(_ROLES_DIR.glob("*.toml")):
            try:
                import tomllib
                data = tomllib.loads(f.read_text(encoding="utf-8"))
                role = data.get("role", {})
                prompt = data.get("prompt", {}).get("system_prompt_template", "")
                roles[f.stem.lower()] = {
                    "key": f.stem,
                    "name": role.get("name") or f.stem,
                    "description": role.get("description", ""),
                    "triggers": role.get("triggers", []),
                    "prompt": prompt,
                }
            except Exception as e:
                logger.warning(f"roles_loader: failed to load {f.name}: {e}")
    _cache = roles
    return roles


def list_roles() -> list[str]:
    """返回所有可用角色的 key 列表（如 ['architect', 'code-explorer', ...]）"""
    return sorted(_load_all().keys())


def get_role(name: str) -> dict | None:
    """按 key / 显示名 / 部分名匹配角色。返回 None 表示未找到。"""
    if not name:
        return None
    roles = _load_all()
    key = name.strip().lower().replace(" ", "-")
    if key in roles:
        return roles[key]
    # 显示名匹配（如 "Security Reviewer" -> security-reviewer）
    for k, v in roles.items():
        if v["name"].lower() == key or k.startswith(key) or key.startswith(k):
            return v
    return None


def get_role_prompt(name: str) -> str | None:
    """获取角色 system_prompt_template 文本。"""
    r = get_role(name)
    return r["prompt"] if r and r.get("prompt") else None


def inject_role(name: str, prompt: str) -> str:
    """把角色块注入主 prompt（放在最前，作为身份覆盖）。

    找不到角色时原样返回 prompt，不报错。
    """
    r = get_role(name)
    if not r or not r.get("prompt"):
        return prompt
    block = f"# 当前角色：{r['name']}\n\n{r['prompt']}"
    return block + "\n\n" + prompt


def clear_cache() -> None:
    """测试用：清空角色缓存。"""
    global _cache
    _cache = None
