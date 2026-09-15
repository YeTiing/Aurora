"""统一预算测试：锁住 skip、degrade、fail 三种超限语义。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.necessity.gate.budget import Budget, BudgetExceeded, CAPABILITY_BUDGETS


def test_budget_declarations_cover_all_capabilities():
    """六项能力都必须声明统一形状的预算，防止只有 A6 被限流。"""
    assert set(CAPABILITY_BUDGETS) == {"A1", "A2", "A3", "A4", "A5", "A6"}
    assert all(isinstance(value, Budget) for value in CAPABILITY_BUDGETS.values())


def test_skip_returns_false_when_budget_is_exceeded():
    """skip 超限时跳过工作，并保留原因供上层记录。"""
    budget = Budget(max_wall_time_ms=1, max_tokens=10, max_tool_calls=1, on_exceed="skip")

    result = budget.enforce(tokens=11)

    assert result.allowed is False
    assert result.action == "skip"
    assert "token" in result.reason


def test_degrade_returns_degrade_action():
    """degrade 超限时不误报成功，而是明确要求调用方降级。"""
    budget = Budget(max_wall_time_ms=10_000, max_tokens=10, max_tool_calls=1, on_exceed="degrade")

    result = budget.enforce(tool_calls=2)

    assert result.allowed is False
    assert result.action == "degrade"


def test_fail_raises_on_exceed():
    """fail 超限必须抛出可识别异常，不能静默继续执行。"""
    budget = Budget(max_wall_time_ms=10_000, max_tokens=10, max_tool_calls=1, on_exceed="fail")

    with pytest.raises(BudgetExceeded, match="tokens"):
        budget.enforce(tokens=11)


def test_budget_tracks_elapsed_time_and_usage():
    """预算检查同时覆盖 wall time、token 与工具调用三类资源。"""
    budget = Budget(max_wall_time_ms=10_000, max_tokens=10, max_tool_calls=2)
    budget.record(tokens=3, tool_calls=1)

    result = budget.enforce(tokens=7, tool_calls=1)

    assert result.allowed is True
    assert budget.used_tokens == 10
    assert budget.used_tool_calls == 2
    assert budget.elapsed_ms >= 0


def test_invalid_on_exceed_is_rejected():
    """未知超限动作必须在边界处失败，避免配置拼写导致无保护运行。"""
    with pytest.raises(ValueError):
        Budget(max_wall_time_ms=1, max_tokens=1, max_tool_calls=1, on_exceed="ignore")
