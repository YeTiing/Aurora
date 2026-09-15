"""索引新鲜度 —— 规范 §1.5（v2 新增，v1 完全没设计）。

## 它修的问题

A1 依赖 `impact`、A2 依赖 `callgraph`、A3 依赖 `lookup` —— **代码一改，图就过期，
三个能力同时失真**。而过期是**静默**的：查询照常返回结果，只是基于旧代码。
「返回了结果」和「返回了正确结果」在这里看起来完全一样。

## 设计原则（规范原话）

    **stale 不是错误，是必须传播的事实。**

所以这里不做「统一处理」，而是：
  ① 检测：内容哈希比对（复用 `store_schema.py::content_hash`）
  ② 传播：stale 符号的**直接调用边**一起标 stale —— 只标符号不够，
     因为「谁在调用这个已变的符号」才是影响面的核心
  ③ 重建：stale 占比超阈值触发增量重建；**失败必须保持 stale 并告警**，
     绝不能把「重建没成功」当成「已经新鲜」
  ④ 分能力降级：四个能力各自决定「过期数据能不能用」

第 ④ 步是重点，也是 v1 缺的那一环。统一当新鲜处理会让 A2 注入错的契约
（最危险），统一当不可用又会让 A1 白报一次「影响面未知」（其实可以带标注）。
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Callable

from backend.necessity.index.store_schema import content_hash

logger = logging.getLogger("necessity.gate.freshness")

# 各能力在 stale 时的处理方式。规范 §1.5 的表格，做成数据而不是四段 if ——
# 「哪个能力怎么降级」是策略，改策略不该改控制流。
CAPABILITY_POLICIES: dict[str, str] = {
    "A1": "annotate",      # 保留并标注「影响面可能过期」，不隐藏
    "A2": "block",         # 不注入依赖 stale 数据的契约（宁可少一条，不可注入错的）
    "A3": "elevate",       # callgraph_fresh=false 本身就是风险信号 -> 提高权限档
    "A6": "downweight",    # 影响面指标不可靠 -> 评分时降权该维度
}

# A6 降权后的权重。0.5 是设计选择：过期不等于无效，只是可信度下降，
# 降到 0 会让该维度直接消失（那和「不收集」没区别，反而丢信息）。
_STALE_IMPACT_WEIGHT = 0.5

# A3 升权后的档位名。用字符串而不是枚举：permission tier 由调用方定义，
# 本模块只负责「必须往上抬」这个动作。
_ELEVATED_TIER = "elevated"


@dataclass
class StaleSnapshot:
    """stale 事实的快照 —— 分能力策略的输入。

    单独一个类型而不是直接传 `FreshnessResult`：策略函数只需要知道
    「哪些文件过期了」，不需要（也不该）知道重建状态等调度细节。
    """

    stale_files: frozenset[str]
    note: str = ""

    @property
    def is_stale(self) -> bool:
        return bool(self.stale_files)


@dataclass
class PolicyDecision:
    """一个能力在 stale 情况下的处理结论。"""

    capability: str
    allowed: bool             # False = 该能力的这份产出不应被使用
    payload: dict             # 处理后的载荷（可能被清空/改写）
    note: str = ""
    action: str = ""


@dataclass
class FreshnessResult:
    """一次新鲜度检查的结果。"""

    stale_files: frozenset[str] = frozenset()
    stale_symbols: list[str] = field(default_factory=list)
    stale_edges: list = field(default_factory=list)
    rebuild_triggered: bool = False
    alerts: list[str] = field(default_factory=list)
    total_files: int = 0
    # 异步重建的句柄。挂在**结果**上而不是 Gate 上：检查是「一次事件」，
    # 结果应该自带「这次要不要等」。挂在 Gate 上会让「哪个重建属于哪次检查」
    # 变得含糊（同一个 Gate 反复 check 会互相覆盖）。
    rebuild_thread: object = field(default=None, repr=False)

    def wait_for_rebuild(self, timeout: float | None = None) -> None:
        """等待异步重建结束（测试与「任务结束收尾」用）。"""
        t = self.rebuild_thread
        if t is not None and getattr(t, "is_alive", lambda: False)():
            t.join(timeout)

    @property
    def is_stale(self) -> bool:
        return bool(self.stale_files)

    @property
    def callgraph_fresh(self) -> bool:
        """图是否可用于**无需标注**的消费。

        注意语义：stale 时它是 False，但**不代表图不能用** ——
        而是「用的时候必须按能力策略处理」。这正是 §1.5 的核心。
        """
        return not self.stale_files

    @property
    def stale_ratio(self) -> float:
        if not self.total_files:
            return 1.0 if self.stale_files else 0.0
        return len(self.stale_files) / self.total_files


class FreshnessGate:
    """检测 / 传播 / 重建 / 分能力降级。"""

    def __init__(self, db, workspace: str, *, total_files: int = 0,
                 rebuild_threshold: float = 0.2) -> None:
        self.db = db
        self.workspace = str(workspace)
        self.total_files = max(0, int(total_files))
        self.rebuild_threshold = float(rebuild_threshold)
        # 重建句柄挂在每次的 FreshnessResult 上（见该类注释）

    # ── 只读快照（策略函数的输入）────────────────────────────────

    @staticmethod
    def stale_snapshot(files) -> StaleSnapshot:
        """构造一个 stale 快照。

        独立成静态方法：策略函数与测试都需要一个**不依赖 db** 的快照，
        否则「A2 拒绝注入」这种纯策略断言会被迫去建索引。
        """
        names = frozenset(str(f) for f in (files or ()))
        note = (f"索引可能过期（{len(names)} 个文件已变更）" if names else "")
        return StaleSnapshot(stale_files=names, note=note)

    # ── ① 检测 + ② 传播 ─────────────────────────────────────────

    def check(self, contents: dict[str, str], *,
              rebuild: Callable[[set[str]], object] | None = None,
              asynchronous: bool = False) -> FreshnessResult:
        """比对内容哈希，找出 stale 文件并传播到符号与调用边。"""
        res = FreshnessResult(total_files=self.total_files or len(contents))

        for path, content in (contents or {}).items():
            stored = None
            try:
                stored = self.db.get_file_content(self.workspace, path)
            except Exception as e:      # 库坏了不该让检查本身失效
                logger.warning("读取索引内容失败 %s: %s", path, e)
            recorded = (stored or {}).get("content_hash")
            if recorded is None or recorded != content_hash(content):
                # 没有记录 = 从未索引 = 同样属于「不可依赖」
                res.stale_files = res.stale_files | {path}

        if res.stale_files:
            self._propagate(res)

        # ③ 重建
        if res.stale_files and res.stale_ratio > self.rebuild_threshold:
            self._rebuild(res, rebuild, asynchronous)

        return res

    def _propagate(self, res: FreshnessResult) -> None:
        """把 stale 传播到符号与其**直接调用边**。

        只标符号不够：影响面的核心是「谁在调用这个已变的符号」。
        边不标 stale 的话，`impact.analyze()` 会照旧返回一批依赖旧代码的调用方。
        """
        for path in sorted(res.stale_files):
            # `query_symbols` 按 content_hash 定位那批符号 —— 正是我们想标 stale
            # 的那批（旧的、已失效的）。拿不到哈希就没有符号可标，
            # 但要**如实告警**而不是静默跳过：漏标符号会让影响面照旧返回旧调用方。
            try:
                old = self.db.get_file_content(self.workspace, path) or {}
                recorded = old.get("content_hash")
                if recorded is None:
                    res.alerts.append(
                        f"{path} 已变更但索引里没有它的旧哈希，无法定位其符号；"
                        "依赖该文件的影响面/契约结论不可用")
                    continue
                symbols = self.db.query_symbols(self.workspace, path,
                                                content_hash=recorded)
            except Exception as e:
                logger.warning("查询符号失败 %s: %s", path, e)
                symbols = []
            for sym in symbols or []:
                sid = sym.get("id") or sym.get("symbol_id")
                if not sid:
                    continue
                res.stale_symbols.append(str(sid))
                try:
                    callers = self.db.callers(str(sid))
                except Exception:
                    callers = []
                for edge in callers or []:
                    res.stale_edges.append(edge)

    def _rebuild(self, res: FreshnessResult, rebuild, asynchronous: bool) -> None:
        """触发增量重建。**失败时保持 stale 并告警** —— 不得伪装成新鲜。"""
        if rebuild is None:
            res.alerts.append(
                f"{len(res.stale_files)} 个文件已过期但未提供重建入口；"
                "依赖这些文件的结论必须按各能力策略降级处理")
            return

        files = set(res.stale_files)
        res.rebuild_triggered = True

        def _run() -> None:
            try:
                rebuild(files)
            except Exception as e:
                # 关键：重建失败**不清除** stale。清除等于声称「现在新鲜了」，
                # 而实际上图还是旧的 —— 这是错误结论，不是降级。
                msg = f"索引重建失败（{type(e).__name__}: {e}）；索引保持过期状态"
                res.alerts.append(msg)
                logger.warning(msg)

        if asynchronous:
            # 不阻塞任务：重建可能秒级到分钟级，卡在检查里会拖慢主循环
            t = threading.Thread(target=_run, name="necessity-index-rebuild",
                                 daemon=True)
            res.rebuild_thread = t
            t.start()
        else:
            _run()


# ── ④ 分能力降级策略 ─────────────────────────────────────────────

def apply_capability_policy(capability: str, stale: StaleSnapshot,
                            payload: dict | None = None) -> PolicyDecision:
    """按能力各自的策略处理 stale 数据。

    四个能力的策略**刻意不同**（规范 §1.5）：
        A1 annotate    保留数据 + 标注，不隐藏 —— 有标注的过期数据仍有价值
        A2 block       清空并标记不可用 —— 注入错的契约会拦住正确改动
        A3 elevate     提升权限档 —— 图不新鲜本身就是风险信号
        A6 downweight  降低影响面权重 —— 数字仍可用，只是可信度打折
    """
    payload = dict(payload or {})
    mode = CAPABILITY_POLICIES.get(capability)
    if mode is None or not stale.is_stale:
        return PolicyDecision(capability=capability, allowed=True, payload=payload)

    if mode == "annotate":
        return PolicyDecision(capability, True, payload,
                              note=f"{stale.note}；影响面可能过期，请对照最新代码",
                              action="annotate")

    if mode == "block":
        # 清空而不是留空字段：留空会让下游以为「真的没有契约」，
        # 而实际是「这次不能给」。两者对调用方的含义完全不同。
        cleared = {k: [] if isinstance(v, list) else v for k, v in payload.items()}
        return PolicyDecision(capability, False, cleared,
                              note=f"{stale.note}；依赖过期索引的契约不予注入",
                              action="block")

    if mode == "elevate":
        payload["permission_tier"] = _ELEVATED_TIER
        return PolicyDecision(capability, True, payload,
                              note=f"{stale.note}；调用图不新鲜，已提高权限档",
                              action="elevate")

    if mode == "downweight":
        payload["impact_weight"] = _STALE_IMPACT_WEIGHT
        return PolicyDecision(capability, True, payload,
                              note=f"{stale.note}；影响面指标降权",
                              action="downweight")

    return PolicyDecision(capability, True, payload)


__all__ = [
    "CAPABILITY_POLICIES", "FreshnessGate", "FreshnessResult",
    "PolicyDecision", "StaleSnapshot", "apply_capability_policy",
]
