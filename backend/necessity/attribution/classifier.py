"""归因分类器 —— 三层架构的 Layer 1 + Layer 2（ATTRIBUTION.md §3 / §5）。

为什么规则优先（§3.2）：
    纯 LLM 不可复现、慢贵、无法解释、且可能被轨迹里的文本误导。
    规则只看客观信号，**信号即证据**。所以规则打底，LLM 只补漏。

Layer 2 是**可注入**的：默认 None，此时无信号命中直接返回 `unknown`。
    这样测试完全离线、不需要 LLM，也符合 INTEGRATION.md §8.1
    「Attribution 默认关闭，分析用」。

冲突处理（§8.4）：Layer 1 与 Layer 2 结论冲突时**以 Layer 1 为准**
    （客观信号优先），并把冲突记进结果供分析。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, runtime_checkable

from . import taxonomy as tx
from .signals import extract_signals, pick_primary

# 置信度低于该值时，规则结论仍保留但标注——不强行升级为高置信
LOW_CONFIDENCE = 0.5


@dataclass
class AttributionResult:
    """一次失败的归因结论（ATTRIBUTION.md §3.3 的 JSON 形状）。

    不变式：
      - `primary` 非 `unknown` 时 `evidence` 必须非空（**没有证据的归因等于猜**）
      - `method` ∈ {rule, llm, unknown}
      - `agent_fault` 由分类决定（§2.3），不是调用方传入的
    """
    attempt_id: str
    primary: str
    confidence: float = 0.0
    method: str = "rule"
    evidence: list[dict] = field(default_factory=list)
    contributing: list[str] = field(default_factory=list)
    improvement: str = ""
    agent_fault: bool = False
    note: str = ""
    conflict: str = ""

    def __post_init__(self) -> None:
        # 不变式护栏：非 unknown 必须有证据。抓的是「猜」这种错误。
        if self.primary != tx.UNKNOWN and not self.evidence:
            raise ValueError(
                f"归因 {self.primary} 没有任何证据（ATTRIBUTION.md §3.3）"
            )
        if not self.improvement:
            self.improvement = tx.improvement_for(self.primary)
        self.agent_fault = tx.is_agent_fault(self.primary)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["agent_fault"] = self.agent_fault
        return d


@runtime_checkable
class LLMClassifier(Protocol):
    """Layer 2 的注入点。返回 None 或无法给出证据时必须放弃，不得硬猜。"""

    def classify(self, summary: str) -> dict | None:
        """返回 {primary, confidence, evidence:[str,...], contributing?} 或 None。"""
        ...


def build_trace_summary(trace: Any, session_id: str, task: dict | None = None,
                        result: Any = None, max_lines: int = 40) -> str:
    """构造给 LLM 的**结构化摘要**（§5.2）。

    不投喂完整轨迹 —— 太长且噪声大。只给压缩后的事实骨架，
    让 LLM 把注意力放在模式上（反复横跳、整体回滚等）。
    """
    events = trace.events(session_id=session_id) if trace is not None else []
    events = sorted(events, key=lambda e: (getattr(e, "turn", 0), getattr(e, "ts", 0.0)))
    lines: list[str] = []
    if task:
        desc = str(task.get("description") or task.get("task") or "").strip()
        if desc:
            lines.append(f"任务：{desc[:300]}")
    if result is not None:
        lines.append(f"结果：{'pass' if getattr(result, 'ok', False) else 'fail'}")
        lines.append(f"轮次：{getattr(result, 'turns', 0)}")
    lines.append("")
    lines.append("轨迹摘要：")
    for e in events[:max_lines]:
        kind, turn = getattr(e, "kind", ""), getattr(e, "turn", 0)
        p = getattr(e, "payload", {}) or {}
        if kind == "file_read":
            lines.append(f"  turn {turn}  读取 {p.get('path', '?')}")
        elif kind == "file_write":
            lines.append(
                f"  turn {turn}  写 {p.get('path', '?')} "
                f"(+{p.get('added', 0)}/-{p.get('removed', 0)})")
        elif kind == "compaction":
            lines.append(f"  turn {turn}  上下文压缩 {p.get('token_before', '?')} → "
                         f"{p.get('token_after', '?')}")
        elif kind == "edit_revert":
            lines.append(f"  turn {turn}  回滚 {p.get('path', '?')}")
        elif kind == "constraint_violation":
            lines.append(f"  turn {turn}  约束违反 {p.get('constraint_id', '?')}")
        elif kind == "test_run":
            lines.append(f"  turn {turn}  测试 {p.get('suite', '?')} → {p.get('result', '?')}")
        else:
            lines.append(f"  turn {turn}  {kind}")
    if len(events) > max_lines:
        lines.append(f"  …（共 {len(events)} 条事件，已截断）")
    return "\n".join(lines)


class FailureClassifier:
    """Layer 1（规则）+ Layer 2（可注入 LLM）的失败归因。"""

    def __init__(self, trace: Any = None, llm: LLMClassifier | None = None,
                 task_meta: dict | None = None):
        self.trace = trace
        self.llm = llm
        self.task_meta = task_meta or {}

    # ── Layer 1 ──────────────────────────────────────────────────

    def _rule_classify(self, session_id: str, task: dict | None,
                       result: Any) -> AttributionResult | None:
        signals = extract_signals(self.trace, session_id=session_id,
                                  task=task or self.task_meta, result=result)
        primary, rest = pick_primary(signals)
        if primary is None:
            return None
        evidence = [primary.as_evidence()]
        # 证据不止一条：把同类的次因也带上（§4.2 允许一条归因多条证据）
        for s in rest:
            if s.category == primary.category and len(evidence) < 5:
                evidence.append(s.as_evidence())
        contributing = sorted({s.category for s in rest if s.category != primary.category})
        return AttributionResult(
            attempt_id=session_id, primary=primary.category,
            confidence=primary.confidence, method="rule", evidence=evidence,
            contributing=contributing,
        )

    # ── Layer 2 ──────────────────────────────────────────────────

    def _llm_classify(self, session_id: str, task: dict | None,
                      result: Any) -> AttributionResult | None:
        if self.llm is None:
            return None
        summary = build_trace_summary(self.trace, session_id, task, result)
        try:
            out = self.llm.classify(summary)
        except Exception:
            # Layer 2 故障不得阻塞（契约 1）—— 退回 unknown
            return None
        if not isinstance(out, dict):
            return None
        cat = out.get("primary")
        # §5.3：无法给出具体证据 → 必须 unknown，不得硬猜
        raw_ev = out.get("evidence") or []
        if not tx.is_known(cat) or not raw_ev:
            return None
        evidence = [
            e if isinstance(e, dict) else {"turn": 0, "signal": "llm", "detail": str(e)}
            for e in raw_ev
        ]
        contributing = [c for c in (out.get("contributing") or []) if tx.is_known(c)]
        return AttributionResult(
            attempt_id=session_id, primary=cat,
            confidence=float(out.get("confidence", 0.6) or 0.6),
            method="llm", evidence=evidence, contributing=contributing,
        )

    # ── 入口 ─────────────────────────────────────────────────────

    def attribute(self, result: Any, task: dict | None = None,
                  session_id: str = "") -> AttributionResult | None:
        """对一个失败尝试归因。pass 的尝试返回 None（归因只针对失败）。

        §8.1：无轨迹 → 标 `unattributable`，不计入统计。
        §8.2：轨迹不完整 → 降级为 LLM 层，报告低置信度。
        """
        sid = session_id or getattr(result, "task_id", "")
        if result is None:
            return None

        if getattr(result, "ok", False):
            return None

        # 无轨迹：显式标记，避免拿「没有数据」当「归因为 capability」
        if self.trace is not None and not self.trace.events(session_id=sid):
            return AttributionResult(
                attempt_id=sid, primary=tx.UNKNOWN, confidence=0.0,
                method="unattributable", evidence=[],
                note="无轨迹：Agent 未输出结构化事件，该尝试不可归因（§8.1）",
            )

        rule_res = self._rule_classify(sid, task, result)          # Layer 1
        llm_res = None
        if rule_res is None or rule_res.confidence < LOW_CONFIDENCE:
            llm_res = self._llm_classify(sid, task, result)        # Layer 2

        if rule_res is not None and llm_res is not None:
            if rule_res.primary != llm_res.primary:
                # §8.4：以 Layer 1 为准，记录冲突供分析
                rule_res.conflict = (
                    f"规则判 {rule_res.primary}，LLM 判 {llm_res.primary}；"
                    f"依 §8.4 以规则为准"
                )
                return rule_res
            return rule_res
        if rule_res is not None:
            return rule_res
        if llm_res is not None:
            return llm_res

        # 有轨迹但无法给出有证据的归因 —— `unknown` 是合法输出（§5.3）
        return AttributionResult(
            attempt_id=sid, primary=tx.UNKNOWN, confidence=0.0,
            method="unknown", evidence=[],
            note="轨迹存在但无高置信信号命中，且无 LLM 层可用；"
                 "不强行分类以免污染统计（§5.3）",
        )


__all__ = [
    "AttributionResult", "FailureClassifier", "LLMClassifier", "LOW_CONFIDENCE",
    "build_trace_summary",
]
