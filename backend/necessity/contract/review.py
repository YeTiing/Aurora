"""契约审批交互 —— 规范 §4.7（v2 补 v1 的洞）。

## 它修的问题

规范原文：「A2 自动挖出并注入约束后，用户可能被一堆**他没要求过的**约束拦住。
v1 完全没设计这个。」

这是「挖得越准反而越烦人」的困境 —— 一个会自动加约束的系统，
如果不给用户确认权，就是在替用户做他没同意的决定。

## v2 的设计：三档注入 + 首次触达即审批

    ≥0.8 且 ≥2 条独立来源  自动注入，但**首次触发时通知**（不直接 block）
    0.5~0.8                不注入，进 review_queue.md
    <0.5                   不提

关键在**首次触发流程**（规范 §4.7 的原文）：

    自动注入的契约首次被违反
      → 不直接 block（用户还没确认过它）
      → 降级为 warn + 插入「检测到可能违反约定…[确认为约束][忽略本次][永久忽略这条]」
      → 用户「确认」后该契约升级为可 block

## 本模块的职责边界

它只**记录与判断**（该不该 block、用户选了什么），不弹 UI ——
弹窗由宿主负责。这样「审批状态」是纯数据，可测试、可持久化、可回放。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("necessity.contract.review")

# 用户对一个契约的处置
CONFIRMED = "confirmed"       # 确认为约束 -> 升级为可 block
IGNORED_ONCE = "ignored_once"  # 忽略本次 -> 下次还会提醒
IGNORED_ALWAYS = "ignored"    # 永久忽略 -> 不再注入也不再提醒

DEFAULT_QUEUE = ".necessity/review_queue.md"
DEFAULT_STATE = ".necessity/contract_review.json"


@dataclass
class ReviewItem:
    """一条待人工确认的契约（进 `review_queue.md`）。"""

    id: str
    statement: str
    confidence: float
    sources: list[str] = field(default_factory=list)
    guard_type: str = ""
    status: str = "pending"        # pending / confirmed / ignored

    def to_dict(self) -> dict:
        return {"id": self.id, "statement": self.statement,
                "confidence": round(self.confidence, 4),
                "sources": list(self.sources), "guard_type": self.guard_type,
                "status": self.status}


class ReviewQueue:
    """人工确认队列 + 已确认状态。

    **状态持久化**是必需的：用户确认过的契约要在下次运行里保持「可 block」，
    否则每次重启都要重新确认一遍 —— 那正是「养成无脑点同意」的成因。
    """

    def __init__(self, *, state_path: str | Path = DEFAULT_STATE) -> None:
        self.state_path = Path(state_path)
        self.status: dict[str, str] = {}
        self.load()

    # ── 持久化 ───────────────────────────────────────────────────

    def load(self) -> None:
        """读状态。文件缺失/损坏都当作空 —— 保守方向是「全部未确认」。"""
        self.status = {}
        if not self.state_path.is_file():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self.status = {str(k): str(v) for k, v in data.items()}
        except Exception as e:
            logger.warning("契约确认状态读取失败，按未确认处理: %s", e)

    def save(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps(self.status, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except Exception as e:
            logger.warning("契约确认状态写入失败: %s", e)

    # ── 判定 ─────────────────────────────────────────────────────

    def effective_action(self, contract_id: str, default: str = "warn") -> str:
        """该契约当前该用什么动作。

        规范 §4.7 的核心：自动挖出的契约**默认是 warn**（用户没确认过），
        只有用户明确「确认为约束」之后才升级为 `block`。
        「永久忽略」的直接不生效。
        """
        st = self.status.get(contract_id, "pending")
        if st == CONFIRMED:
            return "block"
        if st == IGNORED_ALWAYS:
            return "off"
        return default          # pending / ignored_once 都保持 warn

    def decide(self, contract_id: str, decision: str) -> bool:
        """记录用户的处置。返回是否真的改变了状态。"""
        if decision not in (CONFIRMED, IGNORED_ONCE, IGNORED_ALWAYS):
            raise ValueError(f"未知的处置 {decision!r}")
        if self.status.get(contract_id) == decision:
            return False
        self.status[contract_id] = decision
        self.save()
        return True

    def is_off(self, contract_id: str) -> bool:
        return self.status.get(contract_id) == IGNORED_ALWAYS

    # ── 队列 ─────────────────────────────────────────────────────

    def pending_items(self, candidates: list) -> list[ReviewItem]:
        """挑出该进人工队列的候选（§4.7 的 0.5~0.8 档）。"""
        out: list[ReviewItem] = []
        for c in (candidates or []):
            if getattr(c, "injection", "") != "review":
                continue
            if self.is_off(c.id):
                continue
            out.append(ReviewItem(
                id=c.id, statement=c.statement, confidence=c.confidence,
                sources=list(c.sources), guard_type=c.guard_type,
                status=self.status.get(c.id, "pending")))
        return out

    def render_queue(self, candidates: list) -> str:
        """渲染 `review_queue.md`。

        为什么要有这个文件：规范 §4.7 的中间档「不注入，进 review_queue.md」
        —— 用户**主动查看**后才启用。所以它必须是一份可读的清单，
        每条都带上依据与置信度，让用户能判断。
        """
        items = self.pending_items(candidates)
        lines = ["# 待确认的隐式契约", ""]
        if not items:
            lines.append("（无：本次没有处于待确认档的契约）")
            return "\n".join(lines) + "\n"

        lines.append(
            f"以下 {len(items)} 条契约由代码分析自动挖掘，**尚未注入**。"
            "查看后认为成立，可将其状态改为 `confirmed` 启用。")
        lines.append("")
        for it in items:
            lines.append(f"## `{it.id}`")
            lines.append("")
            lines.append(f"- 描述：{it.statement}")
            lines.append(f"- 置信度：{it.confidence:.2f}")
            lines.append(f"- 依据：{'、'.join(it.sources) or '（未记录）'}")
            lines.append(f"- 拟编译为：`{it.guard_type or '（无对应 guard 类型）'}`")
            lines.append(f"- 当前状态：`{it.status}`")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def write_queue(self, candidates: list, path: str | Path = DEFAULT_QUEUE) -> str:
        p = Path(path)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(self.render_queue(candidates), encoding="utf-8")
        except Exception as e:
            logger.warning("review_queue 写入失败: %s", e)
        return str(p)


__all__ = [
    "CONFIRMED", "DEFAULT_QUEUE", "DEFAULT_STATE", "IGNORED_ALWAYS",
    "IGNORED_ONCE", "ReviewItem", "ReviewQueue",
]
