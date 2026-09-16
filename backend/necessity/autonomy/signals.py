"""A3 风险信号采集 —— 规范 §5.4 的 9 类信号。

## 问题定义（规范 §5.1）

当前权限是**固定的**：要么全自动，要么所有操作都问。两者都错：

    全自动  → 高风险操作没人拦
    全问    → 用户被淹没，养成「无脑点同意」的习惯（**比不问更危险**）

所以需要按不确定性与潜在损失动态调整。第一步是把「不确定性」**测量**出来 ——
本模块就做这件事：只采集事实，不打分（打分在 `score.py`）。

## 关键纪律：「未检测」≠「无风险」

规范 §5.6 对需求歧义说得很清楚：compiler 未启用时
「`requirement_ambiguous` 默认 `False`，**并标注为「未检测」而非「无歧义」**」。

这不是个别要求，是本项目反复出现的主题：**把未知当已知是最危险的错误形态**。
所以每个信号都是三态（`True` / `False` / `None`），`None` = 未检测，
且**不打分时按最保守处理**（见 `score.py`）。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("necessity.autonomy.signals")

# 高风险领域关键词（规范 §5.3 的 involves_risky_domain）。
# 刻意包含中英两套：项目代码与需求描述都可能是中文。
_RISKY_DOMAINS = (
    "database", "db", "migration", "migrate", "schema",
    "auth", "permission", "acl", "rbac", "login", "token", "credential",
    "security", "crypto", "encrypt", "secret",
    "dependency", "dependencies", "requirement", "upgrade", "bump",
    "数据库", "迁移", "权限", "认证", "登录", "密钥", "加密", "安全",
    "依赖", "升级", "审计",
)

# 回滚成本高的信号：删文件 / 改公开接口 / 大批量改动
_ROLLBACK_COSTLY = (
    "delete", "remove", "rename", "drop", "truncate",
    "refactor", "restructure", "rewrite", "migrate",
    "删除", "移除", "重命名", "重构", "重写", "迁移",
)


@dataclass
class RiskSignals:
    """规范 §5.4 定义的 9 类信号。

    ⚠️ 每个字段是**三态**：`True` / `False` / `None`（未检测）。
    `None` 表示「拿不到这个信号」，与 `False`（「确认无风险」）含义完全不同。
    打分时必须区别对待（见 `score.py::_risk_points`）。
    """

    requirement_ambiguous: bool | None = None   # 需求有多种合理解释
    target_symbol_unique: bool | None = None    # 能否唯一定位目标符号
    callgraph_fresh: bool | None = None         # 调用图是否新鲜（§1.5）
    touches_public_api: bool | None = None      # 是否触及公共接口
    has_related_tests: bool | None = None       # 有无关联测试
    involves_risky_domain: bool | None = None   # 数据库/权限/安全/依赖升级
    scope_over_budget: bool | None = None       # 修改范围超预算
    rollback_costly: bool | None = None         # 回滚成本高
    active_contracts: int = 0                   # 生效的自动契约数（§1.6 交互③）

    # 采集失败的原因（供报告与门禁使用）
    unknown: list[str] = field(default_factory=list)

    def mark_unknown(self, name: str, why: str) -> None:
        """把某信号标为「未检测」并记录原因。

        统一走这个方法而不是各处 `setattr(..., None)`：
        原因必须被记录 —— 否则「未检测」会退化成静默的缺失。
        """
        setattr(self, name, None)
        self.unknown.append(f"{name}: {why}")

    @property
    def unknown_count(self) -> int:
        """有多少信号是未检测的 —— 门禁与报告要看这个数。"""
        fields = ("requirement_ambiguous", "target_symbol_unique", "callgraph_fresh",
                  "touches_public_api", "has_related_tests", "involves_risky_domain",
                  "scope_over_budget", "rollback_costly")
        return sum(1 for f in fields if getattr(self, f) is None)

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


# ── 各信号的采集 ─────────────────────────────────────────────────

def collect(task_text: str = "", *, compile_result=None, store=None,
            workspace: str = ".", touched_paths=None, touched_symbols=None,
            changed_files: int = 0, file_budget: int = 5,
            depth: int = 1, freshness=None, contracts: int = 0,
            test_history=None) -> RiskSignals:
    """采集 9 类信号。**每个信号独立失败**，失败即标未检测。

    参数都是可选的：调用方能给多少就给多少，给不了的信号会被如实标为
    「未检测」（而不是默默按 False 处理）。
    """
    s = RiskSignals(active_contracts=int(contracts or 0))

    _req_ambiguity(s, compile_result)
    _risky_domain(s, task_text, touched_paths)
    _scope(s, changed_files, file_budget)
    _rollback(s, task_text, changed_files)
    _callgraph(s, freshness)
    _target_symbol(s, store, workspace, touched_symbols)
    _public_api(s, store, workspace, touched_symbols)
    _tests(s, test_history, touched_paths)
    return s


def _req_ambiguity(s: RiskSignals, compile_result) -> None:
    """需求歧义 —— 来自 `guard/compiler`，**不自己调 LLM**（规范 §5.6）。"""
    if compile_result is None:
        s.mark_unknown("requirement_ambiguous",
                       "未提供 CompileResult（Guard 未编译约束）"
                       "—— 这是「未检测」，不是「无歧义」")
        return
    if not getattr(compile_result, "ambiguity_checked", False):
        # compiler 存在但没跑歧义判定（旧版本产生的对象）
        s.mark_unknown("requirement_ambiguous",
                       "CompileResult 未做歧义判定（ambiguity_checked=False）")
        return
    s.requirement_ambiguous = bool(getattr(compile_result, "ambiguous", False))


def _risky_domain(s: RiskSignals, task_text: str, touched_paths) -> None:
    """是否涉及数据库/权限/安全/依赖升级。

    判据是**任务描述 + 改动路径**的关键词。这是启发式，会有漏报 ——
    所以它只作为**加分项**，不单独决定 ask（规范 §5.12 次要风险的缓解措施：
    「该项只作为加分信号，不单独决定 ask」）。
    """
    if not task_text and not touched_paths:
        s.mark_unknown("involves_risky_domain", "既无任务描述也无改动路径")
        return
    blob = (str(task_text or "") + " " + " ".join(
        str(p) for p in (touched_paths or []))).lower()
    s.involves_risky_domain = any(k in blob for k in _RISKY_DOMAINS)


def _scope(s: RiskSignals, changed_files: int, budget: int) -> None:
    """改动范围是否超预算。"""
    if budget <= 0:
        s.mark_unknown("scope_over_budget", f"预算值非法（{budget}）")
        return
    s.scope_over_budget = int(changed_files or 0) > int(budget)


def _rollback(s: RiskSignals, task_text: str, changed_files: int) -> None:
    """回滚成本是否高。

    两个判据：任务描述含破坏性/重构词汇，或改动文件数偏多。
    刻意用「或」而不是「且」—— 回滚成本是**上界估计**，
    低估的代价（该问没问）远大于高估（多问一次）。
    """
    blob = str(task_text or "").lower()
    costly_words = any(k in blob for k in _ROLLBACK_COSTLY)
    many_files = int(changed_files or 0) >= 5
    s.rollback_costly = bool(costly_words or many_files)


def _callgraph(s: RiskSignals, freshness) -> None:
    """调用图是否新鲜（规范 §1.5）。

    「`callgraph_fresh=false` **本身就是一个风险信号**」——
    所以这不是「拿不到就忽略」，而是必须采到；拿不到即标未检测。
    """
    if freshness is None:
        s.mark_unknown("callgraph_fresh",
                       "未提供 freshness 结果（索引新鲜度未挂载）")
        return
    s.callgraph_fresh = bool(getattr(freshness, "callgraph_fresh", True))


def _target_symbol(s: RiskSignals, store, workspace: str, symbols) -> None:
    """能否唯一定位目标符号。"""
    if symbols is None:
        s.mark_unknown("target_symbol_unique", "未提供改动符号列表")
        return
    s.target_symbol_unique = len(symbols) == 1


def _public_api(s: RiskSignals, store, workspace: str, symbols) -> None:
    """是否触及公共接口。

    判据：符号名不以 `_` 开头。这是**近似** —— 真正的公共性要看导出/签名，
    但那需要完整类型信息。规范 §5.2 说这个信号来自 guard 的
    `signature_stable`，所以在没有 guard 结果时如实标未检测。
    """
    if symbols is None:
        s.mark_unknown("touches_public_api", "未提供改动符号列表")
        return
    names = [str(getattr(x, "name", "") or "") for x in symbols]
    s.touches_public_api = any(n and not n.startswith("_") for n in names)


def _tests(s: RiskSignals, test_history, touched_paths) -> None:
    """有无关联测试。

    `test_history` 由 `trace.test_run` 事件提供（规范 §5.2 标注「需补采集点」）。
    没有历史数据时标未检测 —— **不要**因为没有历史就当作「无测试」，
    那会无谓提高权限档，让用户被多余的问题打扰。
    """
    if test_history is None:
        s.mark_unknown("has_related_tests",
                       "无测试历史（trace.test_run 未提供）")
        return
    s.has_related_tests = bool(test_history)


__all__ = ["RiskSignals", "collect"]
