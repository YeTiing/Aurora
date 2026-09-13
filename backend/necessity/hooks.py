"""Necessity 钩子接口 —— 所有能力的契约。

本文件定义宿主与 core 之间的唯一交互面。**这是整个系统的契约层**：
其他模块（context / guard / reduce / attribution）都只通过这里的类型
与 Protocol 与宿主通信，不直接依赖任何宿主的内部结构。

对应文档：INTEGRATION.md §3。

三条硬性契约（INTEGRATION.md §3.3）：
  1. 钩子抛异常 → 放行 + 记录（检查系统故障不得阻塞任务）
  2. 钩子必须无副作用（除 after_write / scan_workspace / on_task_end）
  3. 钩子不得调用 LLM（除 compiler / classifier —— 它们不在主循环路径上）

注意第 1 条与 Aurora `approval_gate` 的取舍不同：那个是**安全**组件，
故障时必须 fail-closed（拒绝）；本层是**质量**组件，故障时 fail-open
（放行）才是对的 —— 因为它不该让任务跑不下去。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

__all__ = [
    "ToolCall",
    "ToolResult",
    "Decision",
    "FileChange",
    "TaskResult",
    "ReadResult",
    "NecessityHooks",
    "NullHooks",
    "DecisionAction",
    "FileChangeKind",
]

DecisionAction = Literal["allow", "block", "modify"]
FileChangeKind = Literal["added", "modified", "deleted"]


# ── 基础类型 ──────────────────────────────────────────────────────

@dataclass
class ToolCall:
    """一次工具调用的意图（before_tool 的输入）。"""
    name: str
    arguments: dict
    turn: int
    call_id: str = ""


@dataclass
class ToolResult:
    """工具调用的结果（after_tool 的输入）。"""
    ok: bool
    output: str = ""
    error: str | None = None
    duration_ms: float = 0.0


@dataclass
class Decision:
    """预检结果。

    action 语义（INTEGRATION.md §3.2）：
      allow  —— 正常执行
      block  —— 不执行，reason 作为工具错误返回给 Agent
      modify —— 用 replacement 替换原调用后执行

    `reason` 会**直接进入 Agent 的上下文**，这是防约束衰减的反馈通道
    （GUARD.md §7.2）—— 所以要写成 Agent 能理解并据此纠正的措辞，
    而不是内部错误码。
    """
    action: DecisionAction = "allow"
    reason: str = ""
    replacement: ToolCall | None = None
    constraint_id: str | None = None

    def __post_init__(self) -> None:
        if self.action == "modify" and self.replacement is None:
            raise ValueError("Decision(action='modify') 必须携带 replacement")
        if self.action == "block" and not self.reason:
            # 拦截必须给理由，否则 Agent 无从纠正，且违反 GUARD.md §7.2
            raise ValueError("Decision(action='block') 必须提供 reason")


@dataclass
class FileChange:
    """工作区里一处文件变更（scan_workspace 的产出）。

    `by_agent` 用于区分「Agent 干的」与「git / 外部进程造成的」——
    GUARD.md §6.3 指出后者不应算越界，否则会误判。
    """
    path: str
    kind: FileChangeKind
    added: int = 0
    removed: int = 0
    by_agent: bool = True


@dataclass
class TaskResult:
    """任务结束的汇总（on_task_end 的输入 / on_task_end 产出的基础）。"""
    task_id: str
    ok: bool
    turns: int = 0
    tokens: int = 0
    diff_stats: dict = field(default_factory=dict)


@dataclass
class ReadResult:
    """read_file 钩子的返回。

    mode 取值：
      full     —— 完整内容（未命中状态表，真读了磁盘）
      index    —— 只返回符号索引（命中状态表，避免重复读）—— Context Paging 的目标形态
      recall   —— 按符号取回的内容片段
    """
    content: str = ""
    mode: Literal["full", "index", "recall"] = "full"
    path: str = ""
    content_hash: str = ""
    symbols: list[dict] = field(default_factory=list)
    token_count: int = 0
    note: str = ""


# ── 钩子协议 ──────────────────────────────────────────────────────

@runtime_checkable
class NecessityHooks(Protocol):
    """宿主在关键点回调的接口。

    所有方法都是**可选语义**：实现方可以做空操作。宿主侧必须把每个
    调用包在 try/except 里并放行（契约 1），因此实现方无需自己兜异常。
    """

    # === 生命周期 ===
    def on_task_start(self, task: dict) -> None:
        """任务开始：编译约束、准备 trace、重置会话状态。"""

    def on_turn_end(self, turn: int) -> None:
        """每轮结束：Guard 后检、状态更新。"""

    def on_task_end(self, result: TaskResult) -> dict:
        """任务结束：产出报告（冗余率 / 约束统计 / 归因）。

        返回 dict 而非 None —— 契约 2 允许本方法写存储。
        """
        return {}

    # === 工具调用 ===
    def before_tool(self, call: ToolCall) -> Decision:
        """预检：可拦截、可改写。"""
        return Decision()

    def after_tool(self, call: ToolCall, result: ToolResult) -> None:
        """后检 + 轨迹记录。"""

    # === 文件读写 ===
    def read_file(self, path: str, opts: dict) -> ReadResult | None:
        """接管读文件。

        返回 None 表示「本层不管，宿主按原逻辑读」。这样默认关闭能力时
        宿主行为完全不变（I1 空操作挂载的判据）。
        """
        return None

    def after_write(self, path: str, writer: str) -> None:
        """写文件后：标记 dirty（writer='agent'）或 stale（其他）。"""

    # === 上下文压缩 ===
    def before_compaction(self, messages: list) -> None:
        """压缩前：快照状态表版本（不得被压缩影响）。"""

    def after_compaction(self, summary: str) -> str:
        """压缩后：返回增强的 summary（注入 file_state 索引）。"""
        return summary

    # === 工作区 ===
    def scan_workspace(self) -> list[FileChange]:
        """扫描工作区变更（Guard 后检的权威数据源）。"""
        return []


class NullHooks:
    """全空实现 —— I1「空操作挂载」用它验证不改变行为。

    这不是测试桩：它是**生产可用的默认实现**（所有能力关闭时使用），
    也是 INTEGRATION.md §8.2 降级矩阵的落点。
    """

    def on_task_start(self, task: dict) -> None:
        return None

    def on_turn_end(self, turn: int) -> None:
        return None

    def on_task_end(self, result: TaskResult) -> dict:
        return {}

    def before_tool(self, call: ToolCall) -> Decision:
        return Decision()

    def after_tool(self, call: ToolCall, result: ToolResult) -> None:
        return None

    def read_file(self, path: str, opts: dict) -> ReadResult | None:
        return None

    def after_write(self, path: str, writer: str) -> None:
        return None

    def before_compaction(self, messages: list) -> None:
        return None

    def after_compaction(self, summary: str) -> str:
        return summary

    def scan_workspace(self) -> list[FileChange]:
        return []
