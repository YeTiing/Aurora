"""证据包构建器 —— 规范 §3.4 / §3.6。

## 两段式（v2 修 v1 的时序矛盾）

    【同步段】build_bundle()   在 on_task_end 内，立即交付，status="partial"
    【异步段】enrich_bundle()  空闲期跑 reduce 补必要性，status="complete"

v1 想让同步段直接汇总必要性证据，但必要性要跑**上百次测试**
（`reduce/` 文档明确说了不能在主循环）。两段式是唯一自洽的做法。

## 一条铁律（规范 §3.12 主要风险）

    **「为凑字段而造假数据」**

所以本模块的每个来源都遵循同一模式：**取不到就写「未采集」**，
绝不填一个看起来合理的默认值。`collection_errors` 记录每一次失败原因。
门禁把「字段长期为空」定为失败（§3.9），正是为了逼出这类静默降级。
"""
from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from ._budget import default_budget_for
from ._collect import _collect_security, _collect_unverified
from .schema import (
    ChangeItem,
    CoverageItem,
    EvidenceBundle,
    ImpactItem,
    NecessityItem,
    SecurityEvidence,
    StalenessInfo,
    VerifyItem,
    default_unverified,
)

logger = logging.getLogger("necessity.report.bundle")


def _safe(label: str, fn, bundle: EvidenceBundle, default):
    """执行一个证据采集，失败时记进 `collection_errors` 而不是抛。

    为什么要统一包一层：六个来源中任何一个失败都不该让整份报告消失 ——
    用户需要看到「其余五项的证据 + 这一项没采到」，
    而不是一个异常把人挡在门外。
    """
    try:
        return fn()
    except Exception as e:
        msg = f"{label} 采集失败：{type(e).__name__}: {e}"
        bundle.collection_errors.append(msg)
        logger.warning(msg)
        return default


# ── 各来源的采集 ─────────────────────────────────────────────────

def _collect_changes(bundle: EvidenceBundle, *, changes, diff_stats) -> None:
    """改动清单。`changes` 是 `FileChange` 序列（来自 scan_workspace）。"""
    out: list[ChangeItem] = []
    for c in (changes or []):
        out.append(ChangeItem(
            path=str(getattr(c, "path", "")),
            kind=str(getattr(c, "kind", "modify") or "modify"),
            added=int(getattr(c, "added", 0) or 0),
            removed=int(getattr(c, "removed", 0) or 0),
            # `by_agent` 区分「Agent 写的」与「外部改的」——这是归因的基础
            by_agent=bool(getattr(c, "by_agent", True)),
        ))
    if not out and diff_stats:
        # 拿不到逐文件明细时，至少要如实说明「有改动但明细未采集」
        bundle.collection_errors.append(
            f"改动明细未采集（diff_stats={diff_stats}）；"
            "只有汇总数字，无法逐文件核对")
    bundle.changes = out


def _collect_impact(bundle: EvidenceBundle, *, store, symbols, depth: int = 2) -> None:
    """影响面：对每个改动符号跑 `impact.analyze`。

    ⚠️ 规范 §1.5：过期数据必须**显式标注**而不是隐藏 ——
    所以 `possibly_stale` 会带上，并在报告里显示。
    """
    if store is None or not symbols:
        bundle.collection_errors.append(
            "影响面未采集（缺少索引库或改动符号）；该条目不是「无影响」，是「未知」")
        return
    from backend.necessity.index.impact import analyze

    seen: set[tuple[str, int]] = set()
    out: list[ImpactItem] = []
    for sym in symbols:
        sid = str(getattr(sym, "id", None) or sym)
        try:
            imp = analyze(store, sid, depth=depth)
        except Exception as e:
            bundle.collection_errors.append(f"影响面分析失败 {sid}: {e}")
            continue
        for node in (getattr(imp, "nodes", None) or []):
            path = str(getattr(node, "file", "") or "")
            line = int(getattr(node, "line", 0) or 0)
            if (path, line) in seen:
                continue
            seen.add((path, line))
            out.append(ImpactItem(symbol=str(getattr(node, "name", sid)),
                                  path=path, line=line))
    bundle.impact = out


def _collect_verification(bundle: EvidenceBundle, *, test_results) -> None:
    """验证证据：来自 `trace.test_run` 事件或调用方直接给的测试结果。"""
    out: list[VerifyItem] = []
    for r in (test_results or []):
        if isinstance(r, dict):
            out.append(VerifyItem(
                command=str(r.get("command", "")),
                exit_code=int(r.get("exit_code", -1)),
                passed=bool(r.get("passed", False)),
                output_excerpt=str(r.get("output", ""))[:500]))
    bundle.verification = out


# ── 同步段入口 ───────────────────────────────────────────────────

def build_bundle(task_result, *, changes=None, store=None, symbols=None,
                 test_results=None, requirements=None, bundle_hooks=None,
                 scanner=None) -> EvidenceBundle:
    """同步段：立即产出一份 `status="partial"` 的证据包。

    参数刻意都是可选的：任何一个来源缺席都只是「该项未采集」，
    而不是让整份报告构建失败。这是§3.12「不造假」的落地方式 ——
    缺席被如实记录，而不是被一个默认值掩盖。

    `scanner` 可注入（测试用），默认取 `security_scanner.get_scanner()`。
    """
    b = EvidenceBundle(task_id=str(getattr(task_result, "task_id", "")))
    diff_stats = getattr(task_result, "diff_stats", None) or {}

    _safe("改动清单", lambda: _collect_changes(
        b, changes=changes, diff_stats=diff_stats), b, None)
    _safe("影响面", lambda: _collect_impact(
        b, store=store, symbols=symbols), b, None)
    _safe("验证证据", lambda: _collect_verification(
        b, test_results=test_results), b, None)
    _safe("安全证据", lambda: _collect_security(
        b, paths=[c.path for c in b.changes], scanner=scanner), b, None)

    # 需求覆盖：给了 requirements 才能算，否则如实说明
    if requirements:
        b.requirement_coverage = [
            CoverageItem(requirement=str(r), covered=bool(r in (test_results or [])),
                         evidence="")
            for r in requirements
        ]
    else:
        b.collection_errors.append(
            "需求覆盖未采集（本次运行未提供需求/验收项清单）")

    _safe("未验证项", lambda: _collect_unverified(
        b, requirements=requirements, verified=b.verification), b, None)
    if not b.unverified:
        # 兜底：绝不留下空列表（门禁判为可疑）
        b.unverified = default_unverified()

    # 陈旧度：**没有来源时也必须记一条**。
    # 默认 `callgraph_fresh=True` 是「未知」的占位，不是「确认新鲜」——
    # 不记录就会让报告说「影响面可靠」，而实际可能基于变更前的索引。
    # 这与「没扫 ≠ 干净」是同一条纪律（见 _collect_security）。
    if bundle_hooks is not None:
        _safe("陈旧度", lambda: _collect_staleness(b, bundle_hooks), b, None)
    else:
        b.collection_errors.append(
            "陈旧度未采集（未提供 freshness provider）；"
            "影响面是否基于过期索引**未知**")

    b.status = "partial"
    b.pending = ["necessity"]
    return b


def _collect_staleness(bundle: EvidenceBundle, hooks) -> None:
    """从 freshness gate 读陈旧度（规范 §1.5 的传播结果）。

    拿不到时保持默认（fresh=True）但**记一条** —— 把未知当新鲜是最坏的选择，
    所以这里只在确实读到 stale 信息时才改，且在拿不到时留下痕迹。
    """
    info = getattr(hooks, "staleness", None)
    if info is None:
        bundle.collection_errors.append(
            "陈旧度未采集（未挂载 freshness gate）；影响面可能基于过期索引")
        return
    bundle.staleness = StalenessInfo(
        stale_files=list(getattr(info, "stale_files", []) or []),
        callgraph_fresh=bool(getattr(info, "callgraph_fresh", True)))


def enrich_bundle(bundle: EvidenceBundle, *, necessity_runner=None,
                  timeout_ms: int = 0, budget=None) -> EvidenceBundle:
    """异步段：补必要性证据，把 `status` 推进到 `complete`。

    ⚠️ 超时/失败时**保持 partial 并记录原因**，不得伪造 necessity 数据
    （规范 §3.6 硬性要求 3）。

    `budget` 是规范 §1.7 的统一预算。A1 的异步段是最该受限的一处 ——
    必要性要跑上百次测试（`reduce/` 明确说了不能在主循环），
    没有上限时会跑很久。默认取 `CAPABILITY_BUDGETS["A1"]`
    （10 分钟 / degrade）。超限按 `on_exceed` 处理：degrade 时**保持 partial**。
    """
    if necessity_runner is None:
        bundle.collection_errors.append(
            "必要性未生成（离线 reduce 未接入）；保持 partial")
        return bundle

    budget = budget if budget is not None else default_budget_for("A1")

    # 前置：预算已耗尽（此前累积的用量）就直接不跑。
    # ⚠️ 这是 `enforce()` 的空调用，只看**已用量** —— 不能用来表达
    # 「这次大约要花多少」，那需要调用方给出预估。所以真正的把关在**后置**。
    if budget is not None:
        try:
            pre = budget.enforce()
        except Exception as e:
            bundle.collection_errors.append(
                f"必要性分析超出预算（{e}）；保持 partial，未伪造数据")
            return bundle
        if not pre.allowed:
            bundle.collection_errors.append(
                f"必要性分析未执行（预算 {pre.action}：{pre.reason}）；保持 partial")
            return bundle

    import time
    t0 = time.monotonic()
    try:
        result = necessity_runner()
    except Exception as e:
        bundle.collection_errors.append(
            f"必要性分析失败：{type(e).__name__}: {e}；保持 partial，数据未伪造")
        return bundle
    elapsed_ms = (time.monotonic() - t0) * 1000.0

    # 后置：用**真实耗时**检查 —— A1 的异步段唯一可观测的成本就是墙钟。
    # 超限时按 `on_exceed` 处理，且**不采纳本次产物**（degrade 的语义是
    # 「降级执行」，而 reduce 没有「跑一半」的形态；保留 partial 更诚实）。
    if budget is not None:
        try:
            post = budget.enforce(elapsed_ms=elapsed_ms)
        except Exception as e:
            bundle.collection_errors.append(
                f"必要性分析耗时 {elapsed_ms:.0f}ms 超出预算（{e}）；"
                "结果未采纳，保持 partial")
            return bundle
        if not post.allowed:
            bundle.collection_errors.append(
                f"必要性分析耗时 {elapsed_ms:.0f}ms 超出预算"
                f"（{post.action}：{post.reason}）；结果未采纳，保持 partial")
            return bundle

    items: list[NecessityItem] = []
    for h in (getattr(result, "hunks", None) or getattr(result, "best", None) or []):
        hid = str(getattr(h, "id", h))
        items.append(NecessityItem(
            hunk_id=hid, path=str(getattr(h, "file", "")),
            necessary=True, reason="在最小必要集内（撤销后测试失败）"))
    bundle.necessity = items
    bundle.status = "complete"
    bundle.pending = []
    return bundle


__all__ = ["build_bundle", "enrich_bundle"]
