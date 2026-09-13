"""checker.py —— 确定性验证器入口（GUARD.md §4.1）。

**硬约束：本模块（及其依赖的 checks.py）不得导入任何 LLM 客户端。**
验证每轮都要跑，必须：
  1. 可复现（同一输入两次结果相同）—— 否则 ρ 指标失去意义
  2. 快且便宜
  3. 不可被 Agent 说服绕过
  4. 可解释（「为什么判我违反」必须说得清）

本文件只放**共用类型 + 分派**；8 种类型的具体检查器在 checks.py。
输入统一是「一组 FileChange + 一组约束」，因此**预检与后检共用同一套
判定逻辑**，区别只在于 FileChange 从哪来（意图 vs 实际落盘）。
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from backend.necessity.hooks import FileChange

from .spec import StructuredConstraint

__all__ = ["Violation", "CheckContext", "check_constraints", "matches_any"]


@dataclass
class Violation:
    """一次约束违反。字段与 GUARD.md §8 的 constraint_violation 表对应。"""
    constraint_id: str
    ctype: str
    message: str
    paths: list[str] = field(default_factory=list)
    lines: int = 0
    detail: str = ""

    def to_dict(self) -> dict:
        return {"constraint_id": self.constraint_id, "type": self.ctype,
                "message": self.message, "paths": list(self.paths),
                "lines": self.lines, "detail": self.detail}


@dataclass
class CheckContext:
    """验证所需的全部外部信息 —— 显式传入而非全局读取，便于测试与降级。

    store / symbols 缺失 → 结构类约束无法验证，进入 `unsupported` 而非
    误判违反（§9 第 7 条：检查器故障放行 + 记录）。
    """
    workspace: str = ""
    store: Any = None
    symbols: dict[str, list[dict]] = field(default_factory=dict)
    signatures: dict[str, str] = field(default_factory=dict)
    baseline_signatures: dict[str, str] = field(default_factory=dict)
    test_results: dict[str, bool] = field(default_factory=dict)
    #: 额外证据（无法塞进 FileChange 的信息）：
    #:   manifest_added: {path: [pkg, ...]}   新增依赖
    #:   imports_added:  [module, ...]         新增 import
    extra: dict = field(default_factory=dict)
    unsupported: list[str] = field(default_factory=list)


def matches_any(path: str, patterns: list[str]) -> bool:
    """glob 匹配（含 `**`）。统一按 posix 分隔符比较。"""
    p = (path or "").replace("\\", "/").lstrip("./")
    for pat in patterns or []:
        q = (pat or "").replace("\\", "/").lstrip("./")
        if not q:
            continue
        if q.endswith("/**"):
            base = q[:-3].rstrip("/")
            if p == base or p.startswith(base + "/"):
                return True
        if fnmatch.fnmatch(p, q) or PurePosixPath(p).match(q):
            return True
    return False


def check_constraints(
    changes: list[FileChange],
    constraints: list[StructuredConstraint],
    ctx: CheckContext | None = None,
) -> list[Violation]:
    """对一组改动跑全部约束，返回违反列表（空 = 全部保持）。

    `changes` 里 `by_agent=False` 的条目在入口就被剔除 —— GUARD.md §6.3
    要求排除 git 操作与外部进程，否则会产生误判。
    """
    from .checks import HANDLERS      # 延迟导入：避免 checker↔checks 循环

    ctx = ctx or CheckContext()
    agent_changes = [c for c in (changes or []) if c and c.by_agent and c.path]
    if not agent_changes:
        return []

    out: list[Violation] = []
    for sc in constraints or []:
        fn = HANDLERS.get(sc.type)
        if fn is None:
            ctx.unsupported.append(sc.id)
            continue
        try:
            v = fn(sc, agent_changes, ctx)
        except Exception as e:
            # §9 第 7 条：检查器自身抛异常 → 放行 + 记录，绝不阻塞任务
            ctx.unsupported.append(f"{sc.id}(error:{type(e).__name__})")
            continue
        if v:
            out.append(v)
    return out
