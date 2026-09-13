"""能力 3：Minimal Diff Reducer —— 改动必要性最小化与致败定位。

对应 DIFF_REDUCER.md。三个产出：
    1. 最小 diff —— 只留必要的，review 负担下降
    2. 冗余清单 —— 列出多余改动及其证明（「删掉它测试仍通过」）
    3. 致败定位 —— 测试挂了，直接指出是哪一块

⚠️ 本能力默认**关闭**（INTEGRATION.md §8.1）：它耗时（要跑上百次测试），
适合离线跑而非主循环。但它是**零风险**的 —— 只产报告，不改 Agent 行为
（DIFF_REDUCER.md §9 回退路径）。

钩子只负责采集（任务结束时抓 diff），真正的分析由 CLI 离线触发
（§6.4 推荐的「离线工具」方式，零侵入且最容易出实验数字）。
"""
from __future__ import annotations

from .report import (
    ReduceReport,
    build_culprit_report,
    build_report,
    format_text,
)
from .sandbox import Sandbox, SandboxInfo, user_tree_is_clean
from .search import (
    Budget,
    Direction,
    SearchResult,
    minimize,
    minimize_culprit,
    minimize_necessary,
    redundancy_ratio,
)
from .split import (
    CoherenceGroups,
    DiffFile,
    Hunk,
    all_hunks,
    apply_text_patch,
    build_coherence_groups,
    hunk_header,
    parse_unified_diff,
    split_large_hunk,
)

__all__ = [
    "build_reduce_hooks",
    # 切分与分组
    "Hunk", "DiffFile", "CoherenceGroups",
    "parse_unified_diff", "all_hunks", "build_coherence_groups",
    "split_large_hunk", "apply_text_patch", "hunk_header",
    # 搜索
    "Budget", "Direction", "SearchResult",
    "minimize", "minimize_necessary", "minimize_culprit", "redundancy_ratio",
    # 隔离
    "Sandbox", "SandboxInfo", "user_tree_is_clean",
    # 报告
    "ReduceReport", "build_report", "build_culprit_report", "format_text",
]


def build_reduce_hooks(cfg: dict | None = None):
    """工厂 —— core/capability.py::FACTORY_NAMES 依赖这个名字。"""
    from .hooks import ReduceHooks

    return ReduceHooks(cfg or {})
