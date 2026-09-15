"""横切机制 —— 规范 §1.4 / §1.5 / §1.7。

这三项必须先于六项能力实现（规范 §9 原话：「否则六项能力各自为政，
后面返工代价更大」）：

    runtime_gate  §1.4 三段式运行时闭环（观测 / 判定 / 动作）
    freshness     §1.5 索引新鲜度（检测 / 传播 / 重建 / 分能力降级）
    budget        §1.7 统一资源预算（skip / degrade / fail）

⚠️ 子模块要显式 import：本项目的 `__init__.py` 约定是列出子模块，
不列会导致 `from backend.necessity.gate import runtime_gate` 失败
（这个坑在本仓库踩过两次）。
"""
from . import budget, freshness, runtime_gate
from .budget import Budget, BudgetExceeded, BudgetResult, CAPABILITY_BUDGETS
from .freshness import (
    CAPABILITY_POLICIES,
    FreshnessGate,
    FreshnessResult,
    PolicyDecision,
    StaleSnapshot,
    apply_capability_policy,
)
from .runtime_gate import (
    POLICIES,
    SECURITY_CAPABILITIES,
    CapabilityPolicy,
    CapabilityState,
    RuntimeGate,
    RuntimeVerdict,
    UpdateResult,
)

__all__ = [
    # 子模块
    "budget", "freshness", "runtime_gate",
    # §1.7
    "Budget", "BudgetExceeded", "BudgetResult", "CAPABILITY_BUDGETS",
    # §1.5
    "CAPABILITY_POLICIES", "FreshnessGate", "FreshnessResult",
    "PolicyDecision", "StaleSnapshot", "apply_capability_policy",
    # §1.4
    "POLICIES", "SECURITY_CAPABILITIES", "CapabilityPolicy", "CapabilityState",
    "RuntimeGate", "RuntimeVerdict", "UpdateResult",
]
