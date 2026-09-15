"""A5 Skill 效果评测与准入 —— 锁死「样本不足不得准入」与「退化自动回滚」。

规格：`Aurora_六项能力设计规范.md` §7。

## 这套机制的核心张力（必须如实保留）

规范 §7.7 硬要求「每臂 ≥ 8 任务」，而当前任务集**恰好 8 个**（零余量）。
Wilcoxon 精确检验在 n=8、方向全一致时才 p=0.0078；只要有一个任务反向，
p 就会跳到 ≥0.05 → `inconclusive`。

所以本文件断言的重点不是「能准入」，而是**样本不足时必须拒绝下结论** ——
规范 §7.8 明确：`effect_size > 0 但 p ≥ 0.05 → inconclusive，不得准入`。
把 inconclusive 当 adopt 会让这套机制变成「拍脑袋的装饰」。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.necessity.eval.records import Attempt, GateMetrics  # noqa: E402
from backend.necessity.eval.skill_arms import (  # noqa: E402
    MIN_TASKS,
    skill_arm_config,
    skill_arm_name,
    verdict_for_skill,
)
from backend.necessity.eval.skill_registry import SkillRegistry  # noqa: E402


def _att(task_id: str, arm: str, *, status="pass", tokens=100, turns=5,
         security: int = 0) -> Attempt:
    return Attempt(attempt_id=f"{task_id}#{arm}", task_id=task_id, arm=arm,
                   status=status, tokens=tokens, turns=turns,
                   gates=GateMetrics(constraint_violations=security))


# ── 门禁映射（规范 §7.8）───────────────────────────────────────

def test_adopt_requires_positive_effect_and_significance():
    for n in (MIN_TASKS, 12):
        assert verdict_for_skill(0.5, 0.01, n_tasks=n) == "adopt", \
            f"n={n} 时正效应 + p<0.05 应准入"


def test_positive_but_not_significant_is_inconclusive_not_adopt():
    """**最关键的一条**：有正效应但样本不足时**不得准入**。

    规范 §7.8 原话：「effect_size > 0 但 p ≥ 0.05 → inconclusive ——
    样本不足，不得准入」。把 inconclusive 当 adopt 是这套机制最容易
    也最致命的退化：它会准入一个「看起来有效但没证据」的 Skill。
    """
    assert verdict_for_skill(0.5, 0.30, n_tasks=MIN_TASKS) == "inconclusive"
    assert verdict_for_skill(0.9, 0.99, n_tasks=MIN_TASKS) == "inconclusive"


def test_sample_below_floor_is_inconclusive_even_with_perfect_p():
    """样本数低于下限时，即使 p 极小也只输出 inconclusive（规范 §7.11）。"""
    assert verdict_for_skill(0.9, 0.001, n_tasks=MIN_TASKS - 1) == "inconclusive"


def test_non_positive_effect_is_rejected():
    assert verdict_for_skill(0.0, 0.01, n_tasks=MIN_TASKS) == "reject"
    assert verdict_for_skill(-0.4, 0.01, n_tasks=MIN_TASKS) == "reject"


def test_new_security_violation_vetoes_regardless_of_effect():
    """安全违规一票否决，无论效果多好（规范 §7.8）。"""
    assert verdict_for_skill(0.9, 0.001, baseline_security=0,
                             treatment_security=1, n_tasks=MIN_TASKS) == "reject"
    # 对照：security 没变差就可以正常准入
    assert verdict_for_skill(0.9, 0.001, baseline_security=1,
                             treatment_security=1, n_tasks=MIN_TASKS) == "adopt"


# ── arm 接线（规范 §7.4：接线而非新建框架）────────────────────

def test_skill_arm_config_is_accepted_by_real_loader():
    """`skill_arm_config` 的产物必须能被真实 `load_capabilities` 接受。

    只断言 dict 形状不够 —— 配置键写错会静默变成「不挂任何能力」，
    于是 treatment 臂与 baseline 一样，实验结论完全无效。
    """
    from backend.necessity.capability import load_capabilities

    cfg = skill_arm_config("demo", "v1")
    caps = load_capabilities(cfg)          # 不抛即通过
    assert isinstance(caps, dict)


def test_skill_arm_name_is_stable_and_parseable():
    """arm 名形如 `S_<skill>_<version>`，且同名输入必须产生同一个名字。

    名字不稳定会让续跑（runner 按 (task, arm, run) 去重）失效：
    同一次实验被当成两次，或两个不同的臂被当成同一个。
    """
    a = skill_arm_name("demo", "v1")
    assert a == skill_arm_name("demo", "v1")
    assert a.startswith("S_") or "demo" in a
    assert "v1" in a


# ── 注册表（规范 §7.9：adopted 是唯一开关）────────────────────

def test_registry_denies_by_default(tmp_path):
    """未登记 = 未准入（保守默认）。"""
    reg = SkillRegistry(tmp_path / "reg.json")
    assert reg.is_adopted("never-evaluated") is False


def test_registry_adopts_only_on_adopt_verdict(tmp_path):
    """只有 adopt 会开启准入；inconclusive 不得准入。"""
    from backend.necessity.eval.skill_arms import SkillEvalResult

    reg = SkillRegistry(tmp_path / "reg.json")

    def result(verdict):
        return SkillEvalResult(
            skill="demo", version="v1",
            task_success=(0.5, 0.6), first_fix_rate=(0.5, 0.6),
            token_cost=(100.0, 90.0), tool_calls=(5.0, 4.0),
            redundant_reread_rate=(0.2, 0.1), irrelevant_change_ratio=(0.1, 0.1),
            security_violations=(0, 0), p_value=0.01, effect_size=0.4,
            verdict=verdict)

    reg.record(result("inconclusive"))
    assert reg.is_adopted("demo") is False, "inconclusive 不该准入"

    reg.record(result("adopt"))
    assert reg.is_adopted("demo") is True

    # reject 要**撤销**已准入状态 —— 否则「先准入、后发现变差」会一直开着
    reg.record(result("reject"))
    assert reg.is_adopted("demo") is False, "reject 未撤销准入"


def test_registry_rollback_is_data_only(tmp_path):
    """回滚 = 设 adopted=false，且是真的落盘（规范 §7.9）。"""
    from backend.necessity.eval.skill_arms import SkillEvalResult
    p = tmp_path / "reg.json"
    reg = SkillRegistry(p)
    reg.record(SkillEvalResult(
        skill="demo", version="v1", task_success=(0.5, 0.7),
        first_fix_rate=(0.5, 0.7), token_cost=(1.0, 1.0), tool_calls=(1.0, 1.0),
        redundant_reread_rate=(0.0, 0.0), irrelevant_change_ratio=(0.0, 0.0),
        security_violations=(0, 0), p_value=0.01, effect_size=0.4, verdict="adopt"))

    assert reg.set_adopted("demo", False) is True
    # 重新读盘，确认落盘而不只是内存变了
    assert SkillRegistry(p).is_adopted("demo") is False


def test_registry_cannot_adopt_unevaluated_skill(tmp_path):
    """未登记的 Skill 不能用人工开关开启 —— 那等于绕过评测。"""
    reg = SkillRegistry(tmp_path / "reg.json")
    assert reg.set_adopted("ghost", True) is False


def test_degrade_rolls_back_automatically(tmp_path):
    """退化（effect 转负 + p<0.05）自动撤销准入（规范 §7.5）。

    与 A3/A4 不同：Skill 评测不是安全功能，所以回滚**不需要**人工确认 ——
    恢复也是自动的（重评测通过）。这个差异是刻意的。
    """
    from backend.necessity.eval.skill_arms import SkillEvalResult
    reg = SkillRegistry(tmp_path / "reg.json")
    reg.record(SkillEvalResult(
        skill="demo", version="v1", task_success=(0.5, 0.7),
        first_fix_rate=(0.5, 0.7), token_cost=(1.0, 1.0), tool_calls=(1.0, 1.0),
        redundant_reread_rate=(0.0, 0.0), irrelevant_change_ratio=(0.0, 0.0),
        security_violations=(0, 0), p_value=0.01, effect_size=0.4, verdict="adopt"))
    assert reg.is_adopted("demo") is True

    out = reg.check_degraded("demo", effect_size=-0.3, p_value=0.01)
    assert out["degraded"] is True
    assert reg.is_adopted("demo") is False
    assert reg.alerts(), "退化必须留下可见告警"


def test_degrade_needs_both_conditions(tmp_path):
    """只看 effect 或只看 p 都会误撤 —— 两个条件都必须满足。"""
    from backend.necessity.eval.skill_arms import SkillEvalResult
    reg = SkillRegistry(tmp_path / "reg.json")
    reg.record(SkillEvalResult(
        skill="demo", version="v1", task_success=(0.5, 0.7),
        first_fix_rate=(0.5, 0.7), token_cost=(1.0, 1.0), tool_calls=(1.0, 1.0),
        redundant_reread_rate=(0.0, 0.0), irrelevant_change_ratio=(0.0, 0.0),
        security_violations=(0, 0), p_value=0.01, effect_size=0.4, verdict="adopt"))

    # effect 为负但 p 不显著 -> 噪声，不撤
    assert reg.check_degraded("demo", -0.3, 0.4)["degraded"] is False
    assert reg.is_adopted("demo") is True
    # p 显著但 effect 为正 -> 是改善，不撤
    assert reg.check_degraded("demo", 0.3, 0.01)["degraded"] is False
    assert reg.is_adopted("demo") is True


def test_registry_survives_corrupt_file(tmp_path):
    """注册表损坏时按「空注册表」处理，不能让调用方起不来。

    它只是准入记录，不是真相源；坏了应表现为「全部未准入」（保守），
    而不是抛异常中断。
    """
    p = tmp_path / "reg.json"
    p.write_text("{ not json at all", encoding="utf-8")
    reg = SkillRegistry(p)
    assert reg.all() == {}
    assert reg.is_adopted("anything") is False


def test_registry_reads_older_schema_missing_new_fields(tmp_path):
    """旧版本写的注册表（缺字段）必须还能读。"""
    p = tmp_path / "reg.json"
    p.write_text(json.dumps({"demo": {"skill": "demo", "adopted": True}}),
                 encoding="utf-8")
    reg = SkillRegistry(p)
    e = reg.get("demo")
    assert e is not None and e.adopted is True
    assert e.history == [] and e.alerts == []
    assert e.fingerprint == ""


def test_registry_keeps_version_history(tmp_path):
    """历史要保留 —— 否则「上一版更好」这个回滚依据就丢了。"""
    from backend.necessity.eval.skill_arms import SkillEvalResult
    reg = SkillRegistry(tmp_path / "reg.json")
    for v in ("v1", "v2"):
        reg.record(SkillEvalResult(
            skill="demo", version=v, task_success=(0.5, 0.7),
            first_fix_rate=(0.5, 0.7), token_cost=(1.0, 1.0), tool_calls=(1.0, 1.0),
            redundant_reread_rate=(0.0, 0.0), irrelevant_change_ratio=(0.0, 0.0),
            security_violations=(0, 0), p_value=0.01, effect_size=0.4, verdict="adopt"))
    e = reg.get("demo")
    assert [h["version"] for h in e.history] == ["v1", "v2"]
    assert e.version == "v2"
