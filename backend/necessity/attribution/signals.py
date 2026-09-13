"""确定性信号提取（Layer 1）—— ATTRIBUTION.md §4 · 基础设施与入口。

设计原则：
    只从**客观事件**推导，不读轨迹里的自然语言文本。理由（§3.2）：
    规则可复现、快、免费、可解释（信号即证据），且**不会被轨迹里的
    文本误导** —— Agent 自己说「环境有问题」不算证据，事件才算。

    这一层预期覆盖 60~70% 的失败案例（§3.1）。

输入是 `core/index/trace.py` 的 `TraceStore`（6 类事件）。
输出是 `Signal` 列表，每条自带 `turn / signal / detail`，
可直接作为 §3.3 要求的证据（**没有证据的归因等于猜**）。

本文件放类型、常量、索引与优先级入口；具体规则在 `rules.py`。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from . import taxonomy as tx

# 环境类异常的错误类型（§4.2「异常栈含 ImportError / 连接超时 / 权限拒绝」）
ENV_ERROR_TYPES = frozenset({
    "importerror", "modulenotfounderror", "connectionerror", "connectiontimeout",
    "timeout", "timeouterror", "permissionerror", "permissiondenied",
    "filenotfounderror", "oserror", "socket.gaierror", "sslerror",
})
# 编辑写错的错误类型（语法/缩进类）
EDIT_ERROR_TYPES = frozenset({"syntaxerror", "indentationerror", "taberror"})
# 工具参数类错误（§4.2「工具返回 invalid-arguments」）
TOOL_ERROR_TYPES = frozenset({
    "invalid_arguments", "invalid-arguments", "invalid_args", "tool_error",
    "toolerror", "bad_arguments", "schema_error",
})
# 反复改-回滚的阈值（§4.2「≥ 3 次」）
THRASH_THRESHOLD = 3
# 首次写入发生在轮次进度超过该比例之后 → 疑似规划问题
LATE_WRITE_RATIO = 0.7


@dataclass(frozen=True)
class Signal:
    """一条确定性信号。`turn / name / detail` 就是 §3.3 的证据三元组。"""
    name: str
    category: str
    confidence: float
    turn: int
    detail: str

    def as_evidence(self) -> dict:
        """转成 §3.3 的 evidence 条目格式。"""
        return {"turn": self.turn, "signal": self.name, "detail": self.detail}


@dataclass
class _Index:
    """把原始事件按 kind 归拢一次，避免每条规则重复遍历。"""
    reads: list[Any] = field(default_factory=list)
    writes: list[Any] = field(default_factory=list)
    compactions: list[Any] = field(default_factory=list)
    reverts: list[Any] = field(default_factory=list)
    violations: list[Any] = field(default_factory=list)
    tests: list[Any] = field(default_factory=list)
    all: list[Any] = field(default_factory=list)


def _load(trace: Any, session_id: str) -> _Index:
    """从真实 TraceStore 读事件。也接受事件列表（便于纯函数测试）。"""
    if trace is None:
        raw: list[Any] = []
    elif isinstance(trace, (list, tuple)):
        raw = [e for e in trace
               if not session_id or getattr(e, "session_id", "") == session_id]
    else:
        raw = trace.events(session_id=session_id)
    # 按 turn + ts 排序，保证「压缩前/后」「读/写先后」的判断稳定
    raw = sorted(raw, key=lambda e: (getattr(e, "turn", 0), getattr(e, "ts", 0.0)))
    idx = _Index(all=raw)
    for e in raw:
        kind = getattr(e, "kind", "")
        bucket = {
            "file_read": idx.reads, "file_write": idx.writes,
            "compaction": idx.compactions, "edit_revert": idx.reverts,
            "constraint_violation": idx.violations, "test_run": idx.tests,
        }.get(kind)
        if bucket is not None:
            bucket.append(e)
    return idx


def _p(e: Any, key: str, default: Any = None) -> Any:
    return (getattr(e, "payload", {}) or {}).get(key, default)


def _agent_writes(writes: Iterable[Any]) -> list[Any]:
    """只算 Agent 自己写的文件 —— 外部/git 改动不归因给 Agent（§2.3 同理）。"""
    return [w for w in writes if _p(w, "writer", "agent") == "agent"]


def _env_error_of(e: Any) -> str:
    return str(_p(e, "error_type") or _p(e, "error") or "").lower()


def pick_primary(signals: list[Signal]) -> tuple[Signal | None, list[Signal]]:
    """按 §4.3 优先级选 primary，其余作为 contributing。

    排序键：类别优先级升序 → 置信度降序 → 出现顺序。
    **先排除「不是 Agent 的锅」，再归因到 Agent。**
    """
    if not signals:
        return None, []
    ranked = sorted(
        enumerate(signals),
        key=lambda pair: (tx.priority_for(pair[1].category), -pair[1].confidence, pair[0]),
    )
    primary = ranked[0][1]
    rest = [s for _, s in ranked[1:]]
    return primary, rest


# 规则实现放在 rules.py（每文件 ≤300 行的预算）。
# 在文件末尾导入以避免循环依赖：rules 需要本文件的类型与辅助函数。
from .rules import extract_signals  # noqa: E402

__all__ = [
    "Signal", "ENV_ERROR_TYPES", "EDIT_ERROR_TYPES", "TOOL_ERROR_TYPES",
    "THRASH_THRESHOLD", "LATE_WRITE_RATIO", "extract_signals", "pick_primary",
]
