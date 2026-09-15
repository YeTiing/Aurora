"""A1 可验证代码补丁 —— 规范 §3。

把「我改了代码」变成「我改了代码，**这是证据**」。

与 `reduce/report.py` 的区别（两者都叫 report，别混）：
    reduce/report.py   Diff Reducer 的**冗余率报告**（离线最小化的产物）
    report/            证据包（同步段立即交付 + 异步段补必要性）

⚠️ 子模块显式 import：本仓库 `__init__.py` 的约定是列出子模块，
不列会让 `from backend.necessity.report import bundle` 失败（踩过两次）。
"""
from . import bundle, hooks, render, schema
from .bundle import build_bundle, enrich_bundle
from .hooks import BUNDLE_DIRNAME, ReportHooks, build_report_hooks
from .render import render_markdown, write_report
from .schema import (
    SYNC_FIELDS,
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

__all__ = [
    # ⚠️ `hooks` 与 `build_report_hooks` 必须在这里导出：
    # `load_capabilities` 是按 `getattr(backend.necessity.report, "build_report_hooks")`
    # 取工厂的。模块存在但不导出工厂会让它静默跳过该能力
    # （日志只有一行 warning），这正是本仓库踩过两次的坑。
    "bundle", "hooks", "render", "schema",
    "BUNDLE_DIRNAME", "ReportHooks", "build_report_hooks",
    "build_bundle", "enrich_bundle", "render_markdown", "write_report",
    "SYNC_FIELDS", "ChangeItem", "CoverageItem", "EvidenceBundle", "ImpactItem",
    "NecessityItem", "SecurityEvidence", "StalenessInfo", "VerifyItem",
    "default_unverified",
]
