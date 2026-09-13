"""可注入 Agent —— 让整条评测链在没有 LLM 的机器上也能被验证。

从 harness.py 拆出来（文件行数硬上限 + 寿命不同）：arm 的定义很稳定，
Agent 接入会随宿主演进，两者的修改理由完全不同。

runner 只依赖 `AgentRunner` 协议，因此：
  - 真实模式：aurora_agent.AuroraAgent（需要 LLM key）
  - 测试 / 自检：ScriptedAgent（离线、确定性、**假数据**）

缺 key 一律抛 AgentUnavailable，由 runner 转成可执行的配置指引 ——
绝不静默降级成假数字。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

# 早停：任务超过轮次上限即终止并计入 fail（EVAL.md §7.3）
DEFAULT_TURN_LIMIT = 40

# 本机能配置的 LLM key（Aurora 支持的四个环境变量）
LLM_KEY_ENV = (
    "AURORA_LLM_API_KEY", "AURORA_API_KEY", "AURORA_AUTH_API_KEY", "OPENAI_API_KEY",
)


class AgentUnavailable(RuntimeError):
    """Agent 无法运行（缺 key / 缺宿主 / 缺依赖）。

    单独一个异常类型：runner 要把它与「任务失败」区分开 ——
    前者应该中止整批，后者只是记录一次 fail。
    """


@dataclass
class AgentRunResult:
    """一次运行的原始产出。runner 负责补上时间戳/arm/attempt_id 等元数据。"""
    status: str = "fail"                 # pass / fail / error / timeout
    turns: int = 0
    tokens: int = 0
    events: list[dict] = field(default_factory=list)   # AgentEvent 序列化
    diff_text: str = ""
    diff_stats: dict = field(default_factory=dict)
    error: str = ""
    meta: dict = field(default_factory=dict)


class AgentRunner(Protocol):
    """可注入 Agent 的契约。"""

    name: str

    def available(self) -> tuple[bool, str]:
        """(可否运行, 原因)。不可运行时原因必须是可执行的指引。"""
        ...

    def run(self, *, task_text: str, repo: Path, arm: str,
            run_index: int, session_id: str,
            hooks: Any, turn_limit: int = DEFAULT_TURN_LIMIT) -> AgentRunResult:
        ...


# ── 脚本化 Agent（离线，仅供测试与管线自检）──────────────────────

@dataclass
class Behavior:
    """一次脚本化运行的行为描述。

    用显式字段而不是「让测试去 mock 事件」：事件序列是 Gate 0 的输入，
    构造它必须一眼可读，否则测试本身就成了不可信的黑盒。
    """
    status: str = "pass"
    turns: int = 8
    tokens: int = 1000
    read_paths: list[str] = field(default_factory=list)
    reread_same_turn: list[str] = field(default_factory=list)
    read_after_compaction: list[str] = field(default_factory=list)
    compaction_turn: int = 0
    written_paths: list[str] = field(default_factory=list)
    constraint_violations: int = 0
    diff_text: str = ""
    diff_stats: dict = field(default_factory=dict)
    error: str = ""


class ScriptedAgent:
    """按任务查表吐事件的假 Agent。**绝不用于真实跑分。**"""

    name = "scripted"

    def __init__(self, behaviors: dict[str, Behavior] | None = None,
                 default: Behavior | None = None):
        self.behaviors = behaviors or {}
        self.default = default or Behavior()

    def available(self) -> tuple[bool, str]:
        return True, "scripted agent（离线假数据，仅供测试）"

    def behavior_for(self, task_id: str) -> Behavior:
        return self.behaviors.get(task_id, self.default)

    def run(self, *, task_text: str, repo: Path, arm: str, run_index: int,
            session_id: str, hooks: Any, turn_limit: int = DEFAULT_TURN_LIMIT) -> AgentRunResult:
        task_id = str(repo).replace("\\", "/").rstrip("/").split("/")[-1]
        b = self.behavior_for(task_id)
        events = events_from_behavior(b, session_id)
        status, err = b.status, b.error
        turns = b.turns
        if b.turns > turn_limit:
            # 早停（§7.3）：超轮次上限即终止，计入 fail
            status, err = "timeout", f"超出轮次上限 {turn_limit}（实际 {b.turns}）"
            turns = turn_limit
        return AgentRunResult(
            status=status, turns=turns, tokens=b.tokens, events=events,
            diff_text=b.diff_text, diff_stats=dict(b.diff_stats), error=err,
            meta={"scripted": True},
        )


def events_from_behavior(b: Behavior, session_id: str) -> list[dict]:
    """把 Behavior 展开成事件序列（顺序即发生顺序，Gate 0 依赖它）。"""
    ev: list[dict] = []
    base_turn = max(1, len(b.read_paths))

    def add(kind: str, turn: int, **payload: Any) -> None:
        ev.append({"session_id": session_id, "kind": kind, "turn": turn,
                   "ts": time.time(), "payload": payload})

    for i, p in enumerate(b.read_paths):
        add("file_read", i + 1, path=p, bytes=100)
    # 压缩（Gate 0 用它区分「遗忘式重读」）
    if b.compaction_turn:
        add("compaction", b.compaction_turn, before=1000, after=300)
    # 压缩后重读同一文件 → 浪费
    for p in b.read_after_compaction:
        add("file_read", b.compaction_turn + 1, path=p, bytes=100)
    # 同轮重复读 → 浪费（turn 相同即命中）
    for p in b.reread_same_turn:
        add("file_read", base_turn, path=p, bytes=100)
        add("file_read", base_turn, path=p, bytes=100)
    for p in b.written_paths:
        add("file_write", base_turn, path=p, writer="agent", added=3)
    for _ in range(b.constraint_violations):
        add("constraint_violation", base_turn, rule="file_scope")
    add("test_run", max(1, b.turns), passed=(b.status == "pass"))
    return ev
