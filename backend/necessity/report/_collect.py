"""A1 的证据采集器（安全 / 未验证项）—— 从 bundle.py 拆出（触 300 行上限）。

这两个采集器与「改动/影响面」那几个的区别：它们**各自有独立的失败语义**
（安全是「没扫 ≠ 干净」，未验证项是「不得为空」），所以单独成文件便于
把这两条纪律写在函数旁边，而不是埋在长文件中间。
"""
from __future__ import annotations

import logging

from .schema import SecurityEvidence, default_unverified

logger = logging.getLogger("necessity.report.collect")


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
