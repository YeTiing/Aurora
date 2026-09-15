"""把 `FreshnessGate` 的检查结果适配成 A1 能消费的 `staleness`。

## 为什么需要适配器

两个模块的接口天然不匹配，而且**不该**为了匹配去改任何一方：

    gate/freshness.py   `check()` 返回 `FreshnessResult`（含 stale_files /
                        stale_symbols / stale_edges / rebuild 状态 / 告警）
    report/bundle.py    `_collect_staleness` 读一个 `.staleness` 属性

`FreshnessResult` 是「一次检查的完整产出」，包含重建调度等 A1 不关心的
细节；A1 只需要「哪些文件过期了、图能不能直接用」。硬把
`FreshnessResult` 改成带 `.staleness` 会让它背上一个只为单一消费者存在的
属性（那正是「为调用方改数据结构」的反模式）。

所以中间加一层薄适配：`StalenessProvider` 持有最近一次检查结果，
对外只暴露 `.staleness`。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from backend.necessity.report.schema import StalenessInfo


@dataclass
class StalenessProvider:
    """`bundle_hooks` 期望的形状 —— 只有 `.staleness` 一个属性。

    用法：
        provider = StalenessProvider()
        result = freshness_gate.check(contents)      # FreshnessResult
        provider.update(result)
        bundle = build_bundle(..., bundle_hooks=provider)
    """

    staleness: StalenessInfo = field(default_factory=StalenessInfo)
    # 保留重建告警：报告里该看到「图是旧的，而且重建失败了」
    alerts: list[str] = field(default_factory=list)

    def update(self, result) -> None:
        """从一次 `FreshnessResult` 更新。"""
        if result is None:
            return
        stale = sorted(str(f) for f in (getattr(result, "stale_files", ()) or ()))
        fresh = bool(getattr(result, "callgraph_fresh", True))
        self.staleness = StalenessInfo(stale_files=stale, callgraph_fresh=fresh)
        self.alerts = list(getattr(result, "alerts", None) or [])

    def check(self, contents: dict, gate=None, **kw):
        """便利入口：跑一次检查并更新自己。`gate` 省略时什么都不做。

        不做「自动建 gate」—— 建 gate 需要 db 与 workspace，
        那属于调用方的决策（规范 §1.5 的三个步骤都需要它们）。
        """
        if gate is None:
            return None
        result = gate.check(contents, **kw)
        self.update(result)
        return result


__all__ = ["StalenessProvider"]
