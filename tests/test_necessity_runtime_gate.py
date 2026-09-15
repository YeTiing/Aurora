"""运行时闭环测试：锁住退化可见、降级可逆以及安全能力不可自动恢复。"""
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.necessity.eval.records import GateMetrics, read_attempts
from backend.necessity.gate.runtime_gate import RuntimeGate


def metrics(**values):
    return GateMetrics(**values)


def test_degrading_window_is_significant_and_degraded():
    """指标持续低于标定线时必须有 p 值，不能凭单次样本静默降级。"""
    gate = RuntimeGate(thresholds={"A1": {"bundle_impact_accuracy": 0.8}})
    window = [metrics(bundle_impact_accuracy=value) for value in [0.30] * 20]

    verdicts = gate.judge("A1", window)

    assert verdicts[0].degraded is True
    assert verdicts[0].p_value is not None
    assert verdicts[0].current == pytest.approx(0.30)


def test_non_security_capability_recovers_after_two_good_windows():
    """普通能力连续两个达标窗口后自动恢复，避免一次偶然好转抖动。"""
    gate = RuntimeGate(thresholds={"A1": {"bundle_impact_accuracy": 0.8}})
    bad = [metrics(bundle_impact_accuracy=0.2)] * 20
    good = [metrics(bundle_impact_accuracy=0.95)] * 20

    gate.update("A1", bad)
    first = gate.update("A1", good)
    second = gate.update("A1", good)

    assert first.state.degraded is True
    assert second.state.degraded is False
    assert second.state.effective_action == "report"


def test_a3_and_a4_never_auto_restore():
    """A3/A4 是安全类：自动降级可以，恢复必须显式人工确认。"""
    for capability, field, threshold in (
        ("A3", "autonomy_missed_risk", 0.1),
        ("A4", "admissions_rejected", 0.2),
    ):
        gate = RuntimeGate(thresholds={capability: {field: threshold}})
        bad = [metrics(**{field: 0.9})] * gate.policy(capability).window_size
        good = [metrics(**{field: 0.0})] * gate.policy(capability).window_size

        gate.update(capability, bad)
        result = gate.update(capability, good)

        assert result.state.degraded is True
        assert result.state.requires_human_restore is True
        assert gate.restore(capability) is False
        assert gate.restore(capability, human_confirmed=True) is True
        assert gate.state(capability).degraded is False


def test_old_jsonl_without_extended_gate_metrics_still_loads(tmp_path):
    """旧记录缺少新增字段时仍可读取，历史评测不能因 schema 扩展失效。"""
    path = tmp_path / "attempts.jsonl"
    old = {"attempt_id": "old", "status": "pass", "gates": {"reads_total": 1}}
    path.write_text(json.dumps(old) + "\n", encoding="utf-8")

    attempts = read_attempts(path)

    assert len(attempts) == 1
    assert attempts[0].gates.reads_total == 1
    assert attempts[0].gates.bundle_fields_filled == 0.0
    assert attempts[0].gates.skill_p_value == 0.0
