"""A6 多方案竞争 —— 规范 §8。

把「一个答案」变成「多方案比证据」。

## 重点不在「同时启动多个 Agent」（规范 §8.1 原话）

    「重点不在『同时启动多个 Agent』，而在：多个候选基于**统一指标**竞争，
      由**独立验证结果**决定最终方案。」

所以本包的核心不是并发，是**评分与裁决**。

## 结构

    schema.py      候选与指标的数据结构（统一指标的载体）
    candidates.py  四种策略 → 不同的**硬约束**（区分度的来源）
    arena.py       隔离分支管理（复用 `reduce/sandbox.py`）
    judge.py       硬门禁 + 按序比较 + 独立评审（**不做加权总分**）

## 两条设计纪律

1. **不做加权总分**（§8.4）：权重无法客观标定，会「用一个编出来的数字
   掩盖真实权衡」。改为「硬门禁 + 按序比较」，每步都能说清为什么。
2. **全部候选违反同一约束 → 报告「无可行候选」**（§8.5①），
   **不得**选「违反最少」的那个 —— 那会让用户在接受违规方案时毫不知情。

⚠️ 子模块显式 import：本仓库 `__init__.py` 的约定是列出子模块（踩过两次）。
"""
from . import arena, candidates, judge, schema
from .arena import Arena, ArenaStats, verify_isolation
from .candidates import CandidateSpec, build_specs, worth_competing
from .judge import JUDGE_ID, STALE_IMPACT_PENALTY, diversity, judge, rank_key
from .schema import (
    MIN_DIVERSITY,
    STRATEGIES,
    Candidate,
    CandidateMetrics,
    Strategy,
    Verdict,
)

__all__ = [
    # 子模块
    "arena", "candidates", "judge", "schema",
    # §8.4 数据
    "MIN_DIVERSITY", "STRATEGIES", "Candidate", "CandidateMetrics", "Strategy",
    "Verdict",
    # §8.2 隔离
    "Arena", "ArenaStats", "verify_isolation",
    # §8.4 生成
    "CandidateSpec", "build_specs", "worth_competing",
    # §8.4 裁决
    "JUDGE_ID", "STALE_IMPACT_PENALTY", "diversity", "judge", "rank_key",
]
