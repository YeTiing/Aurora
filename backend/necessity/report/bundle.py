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


def _collect_security(bundle: EvidenceBundle, *, paths, scanner=None) -> None:
    """安全证据。**「没扫」与「扫了没发现」必须区分**。

    ⚠️ 这里只用 `scan_secrets`（**同步**）与逐文件遍历。

    实测踩过：`SecurityScanner.scan()` 是 `async def`，且签名是**单个
    filepath**（不是文件列表）。此前的写法同步调用它、拿回一个协程对象，
    却把 `scanned=True` 写进报告 —— **未扫描却声称已扫描**。
    这正是本模块最不该犯的错（「降低风险 ≠ 消除风险」，更不能假装扫过）。
    同步段不能用它：`on_task_end` 在主循环内，起 event loop 会阻塞任务。
    """
    if not paths:
        bundle.security = SecurityEvidence(
            scanned=False, note="没有可扫描的改动路径")
        return

    try:
        from backend.security_scanner import get_scanner
        scanner = scanner or get_scanner()
    except Exception as e:
        bundle.security = SecurityEvidence(
            scanned=False, note=f"安全扫描器不可用：{type(e).__name__}: {e}")
        return

    secrets_fn = getattr(scanner, "scan_secrets", None)
    if not callable(secrets_fn):
        bundle.security = SecurityEvidence(
            scanned=False, note="扫描器没有同步 scan_secrets 接口，"
                                "同步段不做安全扫描（不伪称已扫）")
        return

    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    scanned = 0
    for p in paths:
        try:
            for f in (secrets_fn(str(p)) or []):
                sev = str(getattr(f, "severity", "")).lower()
                if sev in counts:
                    counts[sev] += 1
            scanned += 1
        except Exception as e:
            bundle.collection_errors.append(f"密钥扫描失败 {p}: {type(e).__name__}: {e}")

    if scanned == 0:
        bundle.security = SecurityEvidence(
            scanned=False, note="所有改动路径的扫描均失败（见采集失败项）")
        return
    bundle.security = SecurityEvidence(
        scanned=True,
        note=(f"仅密钥层（{scanned}/{len(paths)} 个文件）；"
              "bandit/semgrep 层是异步的，未在同步段运行"),
        **counts)


def _collect_unverified(bundle: EvidenceBundle, *, requirements, verified) -> None:
    """**「本次没验证什么」—— 规范标为核心的一条。**

    算法（规范 §3.5）：需求项 − 验证项的差集。
    没有需求项时**不能**返回空列表（那会被门禁判为可疑），
    而是显式声明「本次没有可拆分的验收项」。
    """
    verified_names = {v.command for v in (verified or []) if v.passed}
    if not requirements:
        bundle.unverified = [
            "（本次任务没有可机器判定的验收项 —— 不是「全部已验证」，"
            "而是「无法判定覆盖」；人工需自行确认）"
        ]
        return
    missing = [r for r in requirements if r not in verified_names]
    bundle.unverified = missing or ["（无：全部验收项均有对应验证）"]


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

    if bundle_hooks is not None:
        _safe("陈旧度", lambda: _collect_staleness(b, bundle_hooks), b, None)
    if not b.staleness.stale_files and not b.staleness.callgraph_fresh:
        pass  # 已由 hook 设置

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
                  timeout_ms: int = 0) -> EvidenceBundle:
    """异步段：补必要性证据，把 `status` 推进到 `complete`。

    ⚠️ 超时/失败时**保持 partial 并记录原因**，不得伪造 necessity 数据
    （规范 §3.6 硬性要求 3）。
    """
    if necessity_runner is None:
        bundle.collection_errors.append(
            "必要性未生成（离线 reduce 未接入）；保持 partial")
        return bundle
    try:
        result = necessity_runner()
    except Exception as e:
        bundle.collection_errors.append(
            f"必要性分析失败：{type(e).__name__}: {e}；保持 partial，数据未伪造")
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
