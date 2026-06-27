from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException

_ALLOWED_ROOT_KEYS = (
    "permissions.allowed_roots",
    "sandbox.allowed_roots",
    "workspace.allowed_roots",
    "allowed_roots",
)


def _config_get(config: Any, key: str, default: Any = None) -> Any:
    if config is None:
        return default
    if hasattr(config, "get"):
        value = config.get(key, None)
        if value is not None:
            return value
    if isinstance(config, dict):
        current: Any = config
        for part in key.split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current
    return default


def _configured_roots(config: Any) -> list[str]:
    roots: list[str] = []
    for key in _ALLOWED_ROOT_KEYS:
        value = _config_get(config, key, None)
        if isinstance(value, str):
            roots.append(value)
        elif isinstance(value, list):
            roots.extend(str(item) for item in value if item)
    return roots


def _dedupe_roots(raw_roots: list[str]) -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()
    for raw in raw_roots:
        resolved = Path(raw).expanduser().resolve()
        key = str(resolved).lower()
        if key not in seen:
            roots.append(resolved)
            seen.add(key)
    return roots


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def allowed_roots(workspace: str = ".", config: Any = None) -> list[Path]:
    configured = _configured_roots(config)
    if not configured:
        return _dedupe_roots([workspace or "."])

    roots = _dedupe_roots(configured)
    workspace_path = Path(workspace or ".").expanduser().resolve()
    if not any(_is_within(workspace_path, root) for root in roots):
        raise HTTPException(403, "Workspace outside allowed roots")
    return roots


def resolve_allowed_path(target: str, workspace: str = ".", config: Any = None) -> Path:
    raw_target = Path(target).expanduser()
    if raw_target.is_absolute():
        roots = _dedupe_roots(_configured_roots(config) or [workspace or "."])
        resolved = raw_target.resolve()
        if any(_is_within(resolved, root) for root in roots):
            return resolved
        raise HTTPException(403, "Path outside allowed roots")

    roots = allowed_roots(workspace, config)
    workspace_path = Path(workspace or ".").expanduser().resolve()
    resolved = (workspace_path / raw_target).resolve()
    if any(_is_within(resolved, root) for root in roots):
        return resolved
    raise HTTPException(403, "Path outside allowed roots")
