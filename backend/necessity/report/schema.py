"""证据化补丁的数据结构 —— 规范 §3.4。

## 为什么先冻结 schema

规范把它列为 A1 的第一条产出物（「`EvidenceBundle` schema 定义并**冻结**」）。
理由与 `eval/records.py` 当初必须先定同一个：runner / report / attribution
是多方独立的模块，不先统一记录格式，各自会发明自己的字段名。
上一个符号 id 冲突就是这么来的（两套方案导致整张图无法关联）。

## 五个问题的对应关系（规范 §3.1）

用户拿到一个补丁后无法回答的五个问题，每个都有对应字段：

    需求覆盖了吗？      -> requirement_coverage
    每处修改为什么必要？ -> necessity（异步段填充）
    影响了哪些调用方？   -> impact
    被验证过吗？        -> verification
    **哪些部分还没验证？** -> unverified        <- 规范标为「核心」

最后一条是重点：绝大多数 Agent 只报「我做了什么」，不报「我没做什么」。
所以 `unverified` 与 `residual_risk` 是**必填**的，且参与门禁
（规范 §3.9：「`unverified` 永远为空 → ⚠️ 可疑」）。

## status / pending 是 v2 新增（修 v1 的时序矛盾）

v1 想让同步的 `on_task_end` 汇总**必要性证据**，但必要性要跑上百次测试
（`reduce/` 明确不能在主循环）。所以 bundle 分两段：

    同步段  build_bundle()   立即交付，status="partial"，pending=["necessity"]
    异步段  enrich_bundle()  补 necessity，status="complete"

`status=partial` **必须**在显眼处标注 —— 不得让用户以为已完成
（规范 §3.6 硬性要求 1）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

BundleStatus = Literal["complete", "partial"]

# 同步段必须填的 7 个字段（规范 §3.8 硬要求：填充率 = 100%）。
# 列在这里而不是散落在门禁代码里 —— 门禁与构建器要认同一份清单，
# 否则「加了新字段但忘了纳入门禁」就会静默漏检。
SYNC_FIELDS: tuple[str, ...] = (
    "requirement_coverage", "changes", "impact",
    "verification", "unverified", "residual_risk", "security",
)


@dataclass
class CoverageItem:
    """一条需求 → 是否被覆盖 + 凭什么判定。"""

    requirement: str
    covered: bool
    evidence: str = ""          # 覆盖的依据（改了哪个符号 / 哪条测试变绿）


@dataclass
class ChangeItem:
    """一处改动。"""

    path: str
    kind: str = "modify"        # add / modify / delete
    added: int = 0
    removed: int = 0
    by_agent: bool = True       # False = 非 Agent 写的（外部改动），影响归因


@dataclass
class ImpactItem:
    """一个被影响的使用点。"""

    symbol: str = ""
    path: str = ""
    line: int = 0
    # ★ 规范 §1.5：过期数据必须**显式标注**，不隐藏
    possibly_stale: bool = False
    note: str = ""


@dataclass
class NecessityItem:
    """一处改动的必要性（**异步段**填充）。"""

    hunk_id: str = ""
    path: str = ""
    necessary: bool = False
    reason: str = ""            # 例如「撤销后目标测试失败」


@dataclass
class VerifyItem:
    """一次验证动作。"""

    command: str = ""
    exit_code: int = -1
    passed: bool = False
    output_excerpt: str = ""


@dataclass
class SecurityEvidence:
    """改动的安全扫描结果。

    `scanned=False` 与「扫描了但没发现」是**不同**的事实 ——
    前者是「不知道」，后者是「确认干净」。混同会让用户以为扫过了。
    """

    scanned: bool = False
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    note: str = ""

    @property
    def clean(self) -> bool:
        """只有**确实扫过**才谈得上「干净」。

        `scanned=False` 时返回 False —— 「没扫」不等于「干净」。
        这是本类最容易写错的地方：把未知当通过。
        """
        return self.scanned and not (self.critical or self.high)


@dataclass
class StalenessInfo:
    """索引新鲜度（规范 §1.5 的传播结果）。"""

    stale_files: list[str] = field(default_factory=list)
    callgraph_fresh: bool = True

    @property
    def note(self) -> str:
        if self.callgraph_fresh:
            return ""
        return (f"影响面可能过期（{len(self.stale_files)} 个文件已变更）—— "
                "该结论基于变更前的索引")


@dataclass
class EvidenceBundle:
    """一次任务的完整证据包。字段含义见模块头。"""

    task_id: str = ""
    status: BundleStatus = "partial"
    pending: list[str] = field(default_factory=list)

    requirement_coverage: list[CoverageItem] = field(default_factory=list)
    changes: list[ChangeItem] = field(default_factory=list)
    impact: list[ImpactItem] = field(default_factory=list)
    necessity: list[NecessityItem] = field(default_factory=list)
    verification: list[VerifyItem] = field(default_factory=list)
    security: SecurityEvidence = field(default_factory=SecurityEvidence)
    # 「本次没验证什么」。**默认非空** —— 见下面 default_unverified()
    unverified: list[str] = field(default_factory=list)
    residual_risk: list[str] = field(default_factory=list)
    staleness: StalenessInfo = field(default_factory=StalenessInfo)

    # 采集失败的原因（规范 §3.12 主要风险：「为凑字段而造假数据」）。
    # 取不到就写这里，而不是填一个看起来合理的值。
    collection_errors: list[str] = field(default_factory=list)

    # ── 门禁（规范 §3.9）─────────────────────────────────────────

    @property
    def filled_ratio(self) -> float:
        """同步段 7 字段的填充率（硬要求 = 100%）。

        「填充」的定义是「**有内容**或**显式声明为空**」——
        空列表不算未填，只要它在 `unverified` 那种「显式声明」语义下。
        所以这里检查的是字段是否被**赋过值**（类型正确且不为 None），
        而不是是否非空。真正的「有没有漏做」由 `unverified` 承载。
        """
        filled = 0
        for name in SYNC_FIELDS:
            v = getattr(self, name, None)
            if v is None:
                continue
            if name in ("unverified", "residual_risk"):
                # 这两项必须**显式**：要么非空，要么由构建器写入「无」
                filled += 1
            else:
                filled += 1
        return filled / len(SYNC_FIELDS)

    @property
    def unverified_explicit(self) -> bool:
        """`unverified` 是否已显式声明。

        规范 §3.9：「`unverified` 永远为空 → ⚠️ 可疑 —— 说明验收项拆分没工作」。
        所以要求它**要么非空，要么显式声明「无」**。
        这里的判据：字段被赋过值（构建器负责写入 `["（无）"]` 这种显式声明），
        而不是一个空列表 —— 空列表恰恰是「没做这项工作」的信号。
        """
        return bool(self.unverified)

    @property
    def verifiable(self) -> bool:
        """是否可发布（规范 §3.9 的门禁）。"""
        return self.filled_ratio == 1.0 and self.unverified_explicit

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


def default_unverified() -> list[str]:
    """「本次没验证什么」的**显式**默认值。

    规范门禁把「`unverified` 永远为空」视为可疑 —— 因为一个真的什么都没漏的
    任务极少见，恒定为空更可能是「没人拆验收项」。
    所以默认给一条显式声明，而不是留空列表让门禁与读者都无所适从。
    """
    return ["（未采集验证项：本次运行未提供验收测试或测试未执行）"]


__all__ = [
    "BundleStatus", "ChangeItem", "CoverageItem", "EvidenceBundle",
    "ImpactItem", "NecessityItem", "SYNC_FIELDS", "SecurityEvidence",
    "StalenessInfo", "VerifyItem", "default_unverified",
]
