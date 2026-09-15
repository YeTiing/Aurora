"""把每次运行的指标喂给运行时闭环（规范 §1.4 的「观测」段）。

## 为什么需要这一层

`gate/runtime_gate.py` 实现了判定与动作，但**没有调用方** ——
它需要「一段时间的窗口」，而窗口只能由**每次运行累积**。

规范 §1.4 的三段式里：
    ① 观测  `measure()` 已在任务结束时采集指标（**已存在**）
    ② 判定  `runtime_gate`（已实现）
    ③ 动作  自动降级（已实现）

缺的是 ①→② 的那一步：谁把采集到的指标喂进去。
本模块就是这一步，接在 `eval/execute.py::run_and_measure` 之后。

## 为什么不用全局单例

闭环状态必须能被测试隔离。所以 `RuntimeFeed` 是显式对象，
由调用方持有并传递；`execute.py` 只接受「可选的 feed」。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field

from backend.necessity.gate.runtime_gate import RuntimeGate

logger = logging.getLogger("necessity.eval.runtime_feed")

# 一个能力的窗口填满后，`judge` 才有意义。低于这个数只累积不判定 ——
# 样本太少的判定会把噪声当退化（规范 §1.4 的窗口语义）。
MIN_JUDGE_SAMPLES = 6


@dataclass
class CapabilityFeed:
    """累积一个能力的观测窗口。

    ## 判定频率（规范只写「滑动窗口 W 个任务」，未定义频率，这里必须明确）

    采用**每积累一个完整窗口判一次**，而不是每个样本判一次：

      · 每个样本判一次 = 连续重叠的窗口，同一批数据被反复判定。
        结果是 `restore_windows` 的语义失效 —— 「连续 2 个窗口达标」变成
        「连续 2 个样本达标」，恢复门槛被悄悄降低（实测：一个达标样本
        就把降级状态清掉了，那与规范 §1.4 的「连续 M 个窗口」不符）。
      · 每窗口判一次才有真正的「窗口」概念，恢复条件也才可解释。

    窗口不重叠：判完即清空重新累积。
    """

    capability: str
    window_size: int
    samples: list = field(default_factory=list)
    verdicts: list = field(default_factory=list)

    def add(self, gates) -> bool:
        """加入一个样本。返回窗口是否**刚填满**（满则应当判定并清空）。"""
        self.samples.append(gates)
        if len(self.samples) >= self.window_size:
            return True
        return False

    def take_window(self) -> list:
        """取出并清空当前窗口 —— 窗口之间不重叠。"""
        window = self.samples
        self.samples = []
        return window


class RuntimeFeed:
    """把 `GateMetrics` 累积成各能力的窗口，喂给 `RuntimeGate`。

    用法（在评测 runner 里）：
        feed = RuntimeFeed()
        attempt = run_and_measure(...)
        report = feed.observe(attempt)      # 窗口满时返回判定结果

    刻意**不自动降级任何能力** —— 它只返回判定结论。真正的降级动作
    由宿主决定（`gate` 的状态机负责记账，本层不碰能力开关）。
    这样「观测」与「动作」是分开的，符合 §1.4 的三段式。
    """

    def __init__(self, gate: RuntimeGate | None = None) -> None:
        self.gate = gate or RuntimeGate()
        self._feeds: dict[str, CapabilityFeed] = {}
        self.observed = 0

    def _feed_for(self, capability: str) -> CapabilityFeed:
        if capability not in self._feeds:
            pol = self.gate.policy(capability)
            self._feeds[capability] = CapabilityFeed(
                capability=capability, window_size=pol.window_size)
        return self._feeds[capability]

    def observe(self, attempt) -> dict:
        """记一次运行。返回本能力的判定摘要（未满窗口时为空）。

        ⚠️ 只喂**有该能力观测字段**的数据。规范 §1.4 表格里每个能力
        关心不同字段，喂错字段会让判定基于零值 —— 那会产生
        「指标一直是 0，所以一直在退化」的荒谬结论。
        """
        gates = getattr(attempt, "gates", None)
        if gates is None:
            return {}
        self.observed += 1

        out: dict = {}
        for cap, pol in self.gate.policies.items():
            threshold = self.gate.thresholds.get(cap) or {}
            if not threshold:
                # 未标定的能力不判定 —— 规范 §0.3：「未标定前不得作为验收依据」
                continue
            feed = self._feed_for(cap)
            if not feed.add(gates):
                continue
            # 取走并清空：窗口之间不重叠（见 CapabilityFeed 的说明）
            window = feed.take_window()
            try:
                res = self.gate.update(cap, window)
            except Exception as e:
                logger.warning("能力 %s 闭环判定失败: %s", cap, e)
                continue
            feed.verdicts = res.verdicts
            out[cap] = {
                "degraded": res.state.degraded,
                "action": res.state.effective_action,
                "requires_human_restore": res.state.requires_human_restore,
                "metrics": [v.metric for v in res.verdicts if v.degraded],
                "p_values": [v.p_value for v in res.verdicts if v.degraded],
            }
        return out

    def state(self) -> dict:
        """当前所有能力的闭环状态（供报告读取）。"""
        return {
            cap: {
                "degraded": st.degraded,
                "action": st.effective_action,
                "requires_human_restore": st.requires_human_restore,
                "samples": len(self._feed_for(cap).samples),
                "window_size": self._feed_for(cap).window_size,
            }
            for cap, st in ((c, self.gate.state(c))
                            for c in self.gate.policies)
        }

    def degraded_capabilities(self) -> list[str]:
        """当前处于降级状态的能力 —— 报告与告警用。"""
        return [c for c in self.gate.policies if self.gate.state(c).degraded]


__all__ = ["CapabilityFeed", "MIN_JUDGE_SAMPLES", "RuntimeFeed"]
