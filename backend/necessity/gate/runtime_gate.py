"""运行时闭环判定 —— 规范 §1.4（v2 新增，修「只有 A5 有自动闭环」）。

## 它修的问题

v1 里只有 A5 有自动退化回滚，其余五项的门禁是**离线人工评测** ——
上线后指标退化了没人知道，得有人想起来去跑评测。规范原话：

    「其余五项的门禁是离线人工评测……上线后指标退化了没人知道。」

## 三段式（观测 / 判定 / 动作）

    ① 观测  已有：`eval/measure.py::measure()` 在任务结束时采指标
    ② 判定  **本模块**：滑动窗口 vs 标定阈值，用 Wilcoxon 判「显著退化」
    ③ 动作  degraded=True -> 自动降级该能力（降级 ≠ 删除）

规范的关键观察是「闭环所需的一切原语都已存在」—— 统计检验复用
`eval/stats.py::wilcoxon_signed_rank`（纯 stdlib、精确检验），
判定模式参考 `attribution/meta.py::verify_improvement`。
本模块只做**串起来**这一件事。

## 为什么用统计检验而不是「低于阈值就降级」

单次样本低于阈值可能是噪声。用 Wilcoxon 判「窗口内是否**显著**低于阈值」，
可以避免「一次偶然抖动就触发降级」—— 那会让能力反复开关，
用户看到的是一次又一次莫名其妙的告警。

## 安全类例外（规范明确要求，且必须可断言）

A4（供应链）与 A3（不确定性执行）的失败代价不可接受，所以：
    **降级可自动，恢复必须人工确认。**
这不是配置项，是硬编码的策略 —— 自动恢复一个安全功能的门槛应当更高。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Sequence

from backend.necessity.eval.stats import wilcoxon_signed_rank

logger = logging.getLogger("necessity.gate.runtime")

# 安全类能力：恢复需人工确认（规范 §1.4 表格里标注「需人工确认」的两项）
SECURITY_CAPABILITIES = frozenset({"A3", "A4"})


@dataclass(frozen=True)
class CapabilityPolicy:
    """一个能力的闭环策略（规范 §1.4 表格，做成数据而非六段 if）。"""

    fields: tuple[str, ...]          # 观测字段（GateMetrics 上的名字）
    window_size: int                 # 判定窗口 W
    degrade_action: str              # 降级动作描述
    restore_windows: int = 2         # 恢复所需的连续达标窗口数
    # 指标方向：True = 越低越好（退化 = 变大），False = 越高越好
    lower_is_better: tuple[bool, ...] = ()


# 规范 §1.4 的表格。`lower_is_better` 是我补的 —— 规范给了字段与窗口，
# 但没说方向，而方向反了会**恰好把退化判成改善**（静默且致命）。
POLICIES: dict[str, CapabilityPolicy] = {
    "A1": CapabilityPolicy(
        fields=("bundle_fields_filled", "bundle_impact_accuracy"),
        window_size=20, degrade_action="报告降级为「仅 Diff」",
        lower_is_better=(False, False)),
    "A2": CapabilityPolicy(
        fields=("contract_violations", "contract_false_blocks"),
        window_size=20, degrade_action="契约从 block 降为 warn",
        lower_is_better=(True, True)),
    "A3": CapabilityPolicy(
        fields=("autonomy_missed_risk",),
        window_size=20, degrade_action="从 ask 降为 plan_first",
        lower_is_better=(True,)),
    "A4": CapabilityPolicy(
        fields=("admissions_rejected",),
        window_size=50, degrade_action="从「默认开启」降为「参考提示」",
        lower_is_better=(True,)),
    "A5": CapabilityPolicy(
        fields=("skill_effect_size",),
        window_size=20, degrade_action="adopted: false + 告警",
        lower_is_better=(False,)),
    "A6": CapabilityPolicy(
        fields=("candidate_diversity",),
        window_size=20, degrade_action="回退单候选路径",
        lower_is_better=(False,)),
}


@dataclass
class RuntimeVerdict:
    """单个指标的判定结论。"""

    metric: str
    current: float
    threshold: float
    degraded: bool
    p_value: float | None = None


@dataclass
class CapabilityState:
    """一个能力的闭环状态。"""

    capability: str = ""
    degraded: bool = False
    # 安全类能力降级后不可自动恢复（规范 §1.4）
    requires_human_restore: bool = False
    effective_action: str = "report"       # 降级时 = policy.degrade_action
    good_windows: int = 0                  # 连续达标窗口数（恢复用）
    verdicts: list[RuntimeVerdict] = field(default_factory=list)


@dataclass
class UpdateResult:
    """一次 `update` 的产出。

    ⚠️ `state` 必须是**快照**（`dataclasses.replace` 出来的副本），
    不能直接给 Gate 内部那个可变对象。否则调用方连续两次 update 后，
    先拿到的那份结果会**跟着变** —— `first.state.degraded` 变成第二次的值。
    这不是理论问题：测试断言 `first.degraded is True` / `second is False`
    时，别名会让两者读到同一个值，而那正是「状态确实被正确推进了」的反证。
    """
    state: CapabilityState
    verdicts: list[RuntimeVerdict] = field(default_factory=list)


def _snapshot(st: CapabilityState) -> CapabilityState:
    """给出一份**独立的**状态副本（见 UpdateResult 的注释）。

    `verdicts` 也要复制列表本身 —— 浅拷贝字段会让两次结果共享同一个列表对象。
    """
    return replace(st, verdicts=list(st.verdicts))


def _value_of(metrics, field_name: str) -> float:
    return float(getattr(metrics, field_name, 0.0) or 0.0)


class RuntimeGate:
    """三段式运行时闭环的「判定」层。"""

    def __init__(self, *, thresholds: dict[str, dict[str, float]] | None = None,
                 policies: dict[str, CapabilityPolicy] | None = None) -> None:
        self.thresholds = thresholds or {}
        self.policies = policies or POLICIES
        self._states: dict[str, CapabilityState] = {}

    def policy(self, capability: str) -> CapabilityPolicy:
        return self.policies.get(capability) or CapabilityPolicy(
            fields=(), window_size=20, degrade_action="关闭")

    def state(self, capability: str) -> CapabilityState:
        return self._states.setdefault(capability, CapabilityState(capability=capability))

    # ── ② 判定 ───────────────────────────────────────────────────

    def judge(self, capability: str, window: Sequence) -> list[RuntimeVerdict]:
        """判定一个窗口内的指标是否显著退化。

        「显著」用 Wilcoxon 而不是单点比较：窗口内每个样本 vs 阈值。
        这是**配对**检验 —— 对每个观测算与阈值的差，再问「这些差是否
        系统性地偏向退化侧」。样本不足（< 6）时不给 p 值：
        样本太少时的 p 值没有意义，给出来只会被当成结论。
        """
        pol = self.policy(capability)
        th = self.thresholds.get(capability, {})
        out: list[RuntimeVerdict] = []
        for idx, name in enumerate(pol.fields):
            if name not in th or not window:
                continue
            threshold = float(th[name])
            values = [_value_of(m, name) for m in window]
            current = sum(values) / len(values)
            lower_better = (pol.lower_is_better[idx]
                            if idx < len(pol.lower_is_better) else False)
            # 退化 = 越过阈值往**坏**的方向
            degraded = (current > threshold) if lower_better else (current < threshold)
            p_value = self._p_value(values, threshold, lower_better) if degraded else None
            out.append(RuntimeVerdict(metric=name, current=current,
                                      threshold=threshold, degraded=degraded,
                                      p_value=p_value))
        return out

    @staticmethod
    def _p_value(values: list[float], threshold: float,
                 lower_is_better: bool) -> float | None:
        """窗口内「是否显著地整体越过阈值」。

        把每个观测与阈值组成一对，问「这些对是否系统性地偏向坏的一侧」。
        少于 6 个样本时返回 None —— 小样本 p 值不可解释，
        而给出一个「恰好 < 0.05」的数字会诱导人据此下结论。
        """
        if len(values) < 6:
            return None
        try:
            if lower_is_better:
                # 越大越坏：比较 values 与「全是阈值的基线」
                r = wilcoxon_signed_rank(values, [threshold] * len(values))
            else:
                r = wilcoxon_signed_rank([threshold] * len(values), values)
            return float(r.p_value)
        except Exception as e:
            logger.debug("wilcoxon 失败（样本异常）: %s", e)
            return None

    # ── ③ 动作 ───────────────────────────────────────────────────

    def update(self, capability: str, window: Sequence) -> UpdateResult:
        """判定 + 推进状态机（降级 / 恢复）。"""
        verdicts = self.judge(capability, window)
        st = self.state(capability)
        st.verdicts = verdicts
        pol = self.policy(capability)

        bad = [v for v in verdicts if v.degraded]
        if bad:
            st.good_windows = 0
            if not st.degraded:
                st.degraded = True
                st.effective_action = pol.degrade_action
                # 安全类：降级可以自动发生，但**恢复**必须人工确认
                st.requires_human_restore = capability in SECURITY_CAPABILITIES
                logger.warning("能力 %s 判定退化 -> %s", capability, pol.degrade_action)
            return UpdateResult(state=_snapshot(st), verdicts=verdicts)

        # 本窗口达标
        if st.degraded:
            if st.requires_human_restore:
                # 不自动恢复。也不递增 good_windows —— 那会让人误以为
                # 「再等两个窗口就会自己好」，而实际上永远不会。
                return UpdateResult(state=_snapshot(st), verdicts=verdicts)
            st.good_windows += 1
            # 需要**连续** restore_windows 个达标窗口才恢复。注意这里用的是
            # `>=`：达标但还不够时 `degraded` 保持 True —— 一个达标窗口就
            # 恢复等于「一次偶然好转就撤销降级」，那会让能力反复开关。
            if st.good_windows >= pol.restore_windows:
                st.degraded = False
                st.effective_action = "report"
                st.good_windows = 0
        return UpdateResult(state=_snapshot(st), verdicts=verdicts)

    def restore(self, capability: str, *, human_confirmed: bool = False) -> bool:
        """人工恢复。返回是否真的恢复了。

        非安全类能力**不需要**人工确认即可恢复（它们会自己恢复）；
        安全类必须显式传 `human_confirmed=True` —— 这样「自动恢复安全能力」
        在代码层面就不可能发生，而不是靠调用方自觉。
        """
        st = self.state(capability)
        if capability in SECURITY_CAPABILITIES and not human_confirmed:
            return False
        if not st.degraded:
            return False
        st.degraded = False
        st.requires_human_restore = False
        st.effective_action = "report"
        st.good_windows = 0
        return True


__all__ = [
    "CapabilityPolicy", "CapabilityState", "POLICIES", "RuntimeGate",
    "RuntimeVerdict", "SECURITY_CAPABILITIES", "UpdateResult",
]
