"""统一资源预算 —— 规范 §1.7。

## 为什么单独一层

规范的原话：「A6 是 4 倍成本、A2 是离线长跑，都没有资源约束」。
v1 只有 A6 被限流，其余五项没有上限 —— 于是一个「什么都没做但把预算烧光」
的能力可以和「有效但便宜」的能力一样通过验收。

这里给六项能力**同一个形状**的预算声明，让成本可见、可比较、可执行。

## 与 `reduce/budget.py` 的关系（刻意不合并）

`reduce/budget.py` 是 ddmin 专用的：它的 `max_test_runs` / `max_hunk_count`
只对「反复跑测试判定子集」这一种工作有意义。本模块是**通用**的
三类资源（时间 / token / 工具调用），六项能力共用。

强行合并会让 ddmin 的 `Budget` 背上它不需要的字段，也会让通用预算
被迫理解「hunk」这个它不该知道的概念。两者保持独立，但形状对齐
（都是 `Budget(max_*)/on_exceed`），这样调用方读起来是同一套心智模型。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

OnExceed = Literal["skip", "degrade", "fail"]

# 规范 §1.7 的表格。数值来源分两类，刻意标注清楚：
#   · 设计参数（规范直接给的）：A2/A4/A6 的墙钟上限、A5 的臂数与任务数
#   · 占位值（规范未给，等 §0.3 阶段 B 标定）：token / 工具调用上限
# 占位值不假装有依据 —— 它们是「先有个上限，别让它无限跑」，
# 标定后再填实测分位数。
_UNCALIBRATED = -1  # 显式标记「等标定」，而不是填一个看起来合理的数


@dataclass
class BudgetResult:
    """一次预算检查的结果。`allowed=False` 时调用方必须按 `action` 处理。"""

    allowed: bool
    action: str          # "" | skip | degrade | fail
    reason: str = ""

    def to_dict(self) -> dict:
        return {"allowed": self.allowed, "action": self.action, "reason": self.reason}


class BudgetExceeded(RuntimeError):
    """`on_exceed="fail"` 时抛出。

    必须是可识别的独立异常类型：调用方要能把它与普通错误区分开 ——
    「预算不够」是预期的资源边界，不是 bug。
    """


@dataclass
class Budget:
    """一次能力执行的资源上限。

    `on_exceed` 三选一，语义各不相同（规范 §1.7 要求显式声明）：
        skip     跳过这项工作（返回 allowed=False，调用方放弃）
        degrade  降级执行（返回 allowed=False + action="degrade"）
        fail     抛 BudgetExceeded（调用方**不能**静默继续）
    这三个不能混：`skip` 与 `degrade` 都返回 False，但前者是「不做」，
    后者是「做个简化版」；`fail` 则是「这个上限是硬约束，绝不越线」。
    """

    max_wall_time_ms: int = _UNCALIBRATED
    max_tokens: int = _UNCALIBRATED
    max_tool_calls: int = _UNCALIBRATED
    on_exceed: OnExceed = "degrade"

    # 已用量（由 `record` 累积；enforce 允许额外传增量）
    used_tokens: int = field(default=0, init=False)
    used_tool_calls: int = field(default=0, init=False)
    _started: float = field(default=0.0, init=False, repr=False)

    def __post_init__(self) -> None:
        # 未知动作必须在**构造时**就失败。否则 a拼写错误（"ignore"）会让
        # 超限时既不 skip 也不 degrade 也不 fail —— 变成无保护运行，
        # 而这正是配置类事故最典型的形态。
        if self.on_exceed not in ("skip", "degrade", "fail"):
            raise ValueError(
                f"未知的 on_exceed={self.on_exceed!r}；"
                "只接受 skip / degrade / fail（规范 §1.7）")
        if not self._started:
            self._started = time.monotonic()

    # ── 用量 ─────────────────────────────────────────────────────

    @property
    def elapsed_ms(self) -> float:
        return max(0.0, (time.monotonic() - self._started) * 1000.0)

    def record(self, tokens: int = 0, tool_calls: int = 0) -> None:
        """记一笔已用量。"""
        self.used_tokens += max(0, int(tokens))
        self.used_tool_calls += max(0, int(tool_calls))

    def reset(self) -> None:
        self.used_tokens = 0
        self.used_tool_calls = 0
        self._started = time.monotonic()

    # ── 检查 ─────────────────────────────────────────────────────

    def _violations(self, tokens: int, tool_calls: int,
                    elapsed_ms: float | None) -> list[str]:
        """列出超限的维度。

        负数上限（`_UNCALIBRATED`）表示「未标定」——**不参与判定**。
        这比把未标定值当成 0（等于永远超限）或当成无穷（等于没约束）都好：
        它诚实地表示「这条约束还没有依据」，与规范 §0.3「未标定前不得
        作为验收依据」一致。
        """
        out: list[str] = []
        if self.max_tokens >= 0 and self.used_tokens + tokens > self.max_tokens:
            out.append(f"tokens 超限（{self.used_tokens + tokens}/{self.max_tokens}）")
        if self.max_tool_calls >= 0 and \
                self.used_tool_calls + tool_calls > self.max_tool_calls:
            out.append(
                f"tool_calls 超限（{self.used_tool_calls + tool_calls}/{self.max_tool_calls}）")
        if self.max_wall_time_ms >= 0:
            now = self.elapsed_ms if elapsed_ms is None else elapsed_ms
            if now > self.max_wall_time_ms:
                out.append(f"wall_time 超限（{now:.0f}/{self.max_wall_time_ms}ms）")
        return out

    def enforce(self, tokens: int = 0, tool_calls: int = 0,
                elapsed_ms: float | None = None) -> BudgetResult:
        """检查是否超限；按 `on_exceed` 决定行为。

        未超限时**顺带记用量**（调用方不必先 record 再 enforce）——
        少一个「忘了记账」的机会，而漏记账会让预算永远不触发。
        """
        bad = self._violations(tokens, tool_calls, elapsed_ms)
        if not bad:
            self.record(tokens=tokens, tool_calls=tool_calls)
            return BudgetResult(allowed=True, action="", reason="")

        reason = "；".join(bad)
        if self.on_exceed == "fail":
            raise BudgetExceeded(reason)
        return BudgetResult(allowed=False, action=self.on_exceed, reason=reason)


# ── 六项能力的预算声明（规范 §1.7）──────────────────────────────

def _b(minutes: float = -1, tokens: int = _UNCALIBRATED,
       calls: int = _UNCALIBRATED, on_exceed: OnExceed = "degrade") -> Budget:
    return Budget(max_wall_time_ms=int(minutes * 60_000) if minutes > 0 else _UNCALIBRATED,
                  max_tokens=tokens, max_tool_calls=calls, on_exceed=on_exceed)


# 触发条件写在注释里而不是编码进 Budget：它们说的是「**要不要**做这件事」
# （如 A6 仅在影响面 >3 文件时启用），属于调度决策，不是资源上限。
# 把它们塞进 Budget 会让「预算」这个概念同时承担两种职责。
CAPABILITY_BUDGETS: dict[str, Budget] = {
    # 同步段轻、异步段重（reduce 跑上百次测试）-> 超时保持 partial，不伪造数据
    "A1": _b(minutes=10, on_exceed="degrade"),
    # 离线扫全库，只在空闲期跑；超时返回已完成部分
    "A2": _b(minutes=10, on_exceed="degrade"),
    # 信号采集轻（读索引），规范说「无限制」-> 用未标定表示不设上限
    "A3": _b(),
    # 导入时一次性，单扩展 ≤30s；超时降级为「无法判定」（不是放行）
    "A4": _b(minutes=0.5, on_exceed="degrade"),
    # 离线评测，不占运行时预算；但要限臂数与任务数，否则跑不完
    "A5": _b(on_exceed="skip"),
    # 4× 成本 -> 最需要硬约束，超限直接 fail（不能静默多跑 3 个候选）
    "A6": _b(minutes=15, on_exceed="fail"),
}

__all__ = ["Budget", "BudgetExceeded", "BudgetResult", "CAPABILITY_BUDGETS", "OnExceed"]
