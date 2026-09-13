"""intent.py —— 工具意图解析与路径工具（预检专用）。

**为什么预检必须保守**（GUARD.md §5.2）：
    预检只看意图。`shell_command("python fix.py")` 看不出会改什么文件，
    所以解析不出来就**放行**，交给后检兜底 —— 预检误拦的代价（阻止正确
    操作）高于漏拦的代价（后检能回滚）。宁可漏，不可误。

这里只解析能被静态确定的形态：显式路径参数、shell 重定向、`sed -i`。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from backend.necessity.hooks import FileChange

__all__ = ["intent_paths", "contain", "rel", "merge_changes", "git_show"]

# 工具参数里声明写入目标的字段名
_DECLARED_KEYS = ("path", "file", "file_path", "filename", "target", "targets")

# shell 重定向 / 就地编辑的确定性目标
_SHELL_TARGETS = re.compile(
    r"(?:>>?|tee\s+(?:-a\s+)?)\s*([^\s;|&()<>]+)"
    r"|sed\s+-i[^\s]*\s+(?:-e\s+\S+\s+)?(?:-e\s+\S+\s+)?([^\s;|&()]+)\s*$",
    re.M)

# 明显不是文件名的目标（重定向描述符、空设备等）
_NOT_A_PATH = re.compile(r"^(?:&?\d|null|/dev/null|con|nul)$", re.I)


def intent_paths(name: str, arguments: dict) -> list[str]:
    """从工具调用里提取**确定的**写入目标（解析不出则空 → 预检放行）。"""
    args = arguments if isinstance(arguments, dict) else {}
    out: list[str] = []
    for key in _DECLARED_KEYS:
        v = args.get(key)
        if isinstance(v, str) and v:
            out.append(v)
        elif isinstance(v, list):
            out.extend(str(x) for x in v if isinstance(x, str))
    cmd = args.get("command") or args.get("cmd") or args.get("script")
    if isinstance(cmd, str) and cmd:
        for m in _SHELL_TARGETS.finditer(cmd):
            cand = (m.group(1) or m.group(2) or "").strip().strip("'\"")
            if cand and not _NOT_A_PATH.match(cand):
                out.append(cand)
    return list(dict.fromkeys(p for p in out if p))


def contain(workspace: str, target: str) -> Path:
    """把 target 解析为工作区内路径，越界抛 PermissionError。

    **绝不用 str.startswith。** INTEGRATION.md §7.3 的真实教训：
    Aurora 早期用前缀匹配，`/data/proj-evil` 逃逸了 `/data/proj`。
    这里按路径分量判断（is_relative_to）。
    """
    ws = Path(workspace).resolve()
    p = Path(target)
    resolved = p.resolve() if p.is_absolute() else (ws / target).resolve()
    if resolved != ws and not resolved.is_relative_to(ws):
        raise PermissionError(f"路径越出工作区，拒绝操作: {target}")
    return resolved


def rel(path: str, workspace: str) -> str:
    """绝对路径 → 工作区相对 posix 路径；已是相对路径则归一化。"""
    try:
        p = Path(path)
        if p.is_absolute():
            return str(p.resolve().relative_to(
                Path(workspace).resolve())).replace("\\", "/")
    except Exception:
        pass
    return str(path).replace("\\", "/").lstrip("./")


def merge_changes(old: list[FileChange], new: list[FileChange]) -> list[FileChange]:
    """按 path 合并累积变更，行数相加（用于 scan_workspace 的权威列表）。"""
    by = {c.path: c for c in old}
    for c in new:
        prev = by.get(c.path)
        if prev is None:
            by[c.path] = c
        else:
            by[c.path] = FileChange(c.path, c.kind, prev.added + c.added,
                                    prev.removed + c.removed, c.by_agent)
    return list(by.values())


def git_show(workspace: str, relpath: str) -> str | None:
    """从 git HEAD 取基线内容（回滚的最后一条基线来源）。"""
    try:
        r = subprocess.run(["git", "show", f"HEAD:{relpath}"], cwd=workspace,
                           capture_output=True, text=True, timeout=5)
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None
