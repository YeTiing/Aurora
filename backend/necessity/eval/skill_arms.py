"""Skill 对照臂与效果汇总。

为什么把这层放在 eval 而不是改宿主加载器：A5 需要先把「哪个版本的
Skill」和「哪一个对照臂」固定下来，才能复现实验。当前宿主只有
`backend.skills.SkillManager` 的加载/匹配逻辑，没有把 Skill 配置消费进
`load_capabilities` 的工厂；若在这里假装已经启用 Skill，实验结果会把
声明当成事实。因此本模块只负责声明配置、从 Attempt 复用事实指标，并
把未接入的开关显式保留在配置中。
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence

from backend.necessity.eval.effect_size import cliffs_delta
from backend.necessity.eval.harness import arm_capability_config
from backend.necessity.eval.records import Attempt
from backend.necessity.eval.stats import wilcoxon_signed_rank

MIN_TASKS = 8
P_SIGNIFICANT = 0.05
SkillVerdict = Literal["adopt", "reject", "inconclusive"]
_SECURITY_EVENTS = {"security_violation", "constraint_violation"}


def skill_arm_name(skill_name: str, version: str) -> str:
    """生成稳定的 `S_<skill_name>_<version>` 名称。

    空名称会让不同 Skill 静默汇入同一个 arm，随后配对统计无法知道自己
    比较的对象；因此在配置层直接拒绝，而不是让错误传播到跑分结果。
    """
    name, ver = str(skill_name).strip(), str(version).strip()
    if not name or not ver:
        raise ValueError("skill_name 和 version 都不能为空")
    return f"S_{name}_{ver}"


def skill_arm_config(skill_name: str, version: str, *, base_arm: str = "A") -> dict:
    """返回 Skill treatment arm 的声明配置。

    基础能力仍完全复用 `arm_capability_config`，保证 A/B/C 等对照臂走真实
    `load_capabilities`。`skill` 字段是当前唯一的 Skill 开关声明；宿主尚无
    消费者，所以它不会伪造一个不存在的能力实例，也不会改变基线能力。
    """
    arm = skill_arm_name(skill_name, version)
    config = copy.deepcopy(arm_capability_config(base_arm))
    config["arm"] = arm
    config["skill"] = {
        "enabled": True,
        "name": str(skill_name).strip(),
        "version": str(version).strip(),
    }
    return config


def _by_task(attempts: Sequence[Attempt]) -> dict[str, list[Attempt]]:
    grouped: dict[str, list[Attempt]] = {}
    for attempt in attempts:
        if attempt.task_id:
            grouped.setdefault(attempt.task_id, []).append(attempt)
    return grouped


def _task_value(runs: Sequence[Attempt], metric: str) -> float:
    """按任务取重复运行的中位数，避免把重复轮次冒充独立样本。"""
    from backend.necessity.eval.effect_size import median

    if metric == "success":
        return 1.0 if any(a.status == "pass" for a in runs) else 0.0
    if metric == "first_fix":
        first = min((a.run_index for a in runs), default=0)
        return 1.0 if any(a.run_index == first and a.status == "pass" for a in runs) else 0.0
    values = [_attempt_metric(a, metric) for a in runs]
    return median(values)


def _attempt_metric(attempt: Attempt, metric: str) -> float:
    if metric == "tokens":
        return float(attempt.tokens)
    if metric == "tool_calls":
        return float(sum(1 for e in attempt.events if e.get("kind") == "tool_result"))
    if metric == "reread":
        return float(attempt.gates.waste_ratio)
    if metric == "irrelevant":
        return float(attempt.gates.redundancy_ratio)
    raise ValueError(f"未知 Skill 指标: {metric}")


def _metric_values(grouped: dict[str, list[Attempt]], metric: str) -> list[float]:
    return [_task_value(grouped[task_id], metric) for task_id in sorted(grouped)]


def _security_count(attempt: Attempt) -> int:
    """合并扫描器结果和轨迹事实，并避免把缺失字段当成违规。"""
    event_count = sum(1 for e in attempt.events if e.get("kind") in _SECURITY_EVENTS)
    meta_count = attempt.meta.get("security_violations", 0) if attempt.meta else 0
    try:
        return event_count + max(0, int(meta_count))
    except (TypeError, ValueError):
        return event_count


def _security_total(attempts: Sequence[Attempt]) -> int:
    return sum(_security_count(a) for a in attempts)


def _rate(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def verdict_for_skill(
    effect_size: float,
    p_value: float,
    *,
    baseline_security: int = 0,
    treatment_security: int = 0,
    n_tasks: int = 0,
) -> SkillVerdict:
    """按 §7.8 的门禁顺序返回准入判定。

    安全违规是一票否决；没有足够任务时不允许把方向当结论。两条规则
    都必须在这里集中编码，否则不同调用方很容易把「有趋势」误写成 adopt。
    """
    if treatment_security > baseline_security:
        return "reject"
    if n_tasks < MIN_TASKS:
        return "inconclusive"
    if effect_size > 0 and p_value < P_SIGNIFICANT:
        return "adopt"
    if effect_size > 0:
        return "inconclusive"
    return "reject"


@dataclass
class SkillEvalResult:
    skill: str
    version: str
    task_success: tuple[float, float]
    first_fix_rate: tuple[float, float]
    token_cost: tuple[float, float]
    tool_calls: tuple[float, float]
    redundant_reread_rate: tuple[float, float]
    irrelevant_change_ratio: tuple[float, float]
    security_violations: tuple[int, int]
    p_value: float
    effect_size: float
    verdict: SkillVerdict

    def to_dict(self) -> dict:
        """序列化稳定字段，供注册表跨版本读取。"""
        return {
            "skill": self.skill,
            "version": self.version,
            "task_success": list(self.task_success),
            "first_fix_rate": list(self.first_fix_rate),
            "token_cost": list(self.token_cost),
            "tool_calls": list(self.tool_calls),
            "redundant_reread_rate": list(self.redundant_reread_rate),
            "irrelevant_change_ratio": list(self.irrelevant_change_ratio),
            "security_violations": list(self.security_violations),
            "p_value": self.p_value,
            "effect_size": self.effect_size,
            "verdict": self.verdict,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SkillEvalResult":
        """读取旧注册表时对缺失的新指标使用保守默认值。"""
        pair = lambda key, default=0.0: tuple(data.get(key, [default, default]))
        return cls(
            skill=str(data.get("skill", "")), version=str(data.get("version", "")),
            task_success=pair("task_success"), first_fix_rate=pair("first_fix_rate"),
            token_cost=pair("token_cost"), tool_calls=pair("tool_calls"),
            redundant_reread_rate=pair("redundant_reread_rate"),
            irrelevant_change_ratio=pair("irrelevant_change_ratio"),
            security_violations=tuple(data.get("security_violations", [0, 0])),
            p_value=float(data.get("p_value", 1.0)),
            effect_size=float(data.get("effect_size", 0.0)),
            verdict=data.get("verdict", "inconclusive"),
        )


def evaluate_skill(
    skill: str,
    version: str,
    baseline: Sequence[Attempt],
    treatment: Sequence[Attempt],
    *,
    min_tasks: int = MIN_TASKS,
) -> SkillEvalResult:
    """从两臂 Attempt 记录生成 SkillEvalResult。

    任务按 task_id 排序后配对，统计输入只保留共同任务；这样相同记录无论
    JSONL 追加顺序如何都得到同一个效应方向。样本下限同时检查两臂和共同
    任务数，防止一臂缺任务却被另一臂的数字掩盖。
    """
    base, treat = _by_task(baseline), _by_task(treatment)
    common = sorted(set(base) & set(treat))
    metrics = ("success", "first_fix", "tokens", "tool_calls", "reread", "irrelevant")
    pairs: dict[str, tuple[list[float], list[float]]] = {}
    for metric in metrics:
        pairs[metric] = (
            [_task_value(base[t], metric) for t in common],
            [_task_value(treat[t], metric) for t in common],
        )

    success_base, success_treat = pairs["success"]
    effect = cliffs_delta(success_treat, success_base)
    test = wilcoxon_signed_rank(
        [success_treat[i] - success_base[i] for i in range(len(common))]
    )
    p_value = float(test.p_value) if test.p_value is not None else 1.0
    security = (_security_total(baseline), _security_total(treatment))
    n_tasks = min(len(base), len(treat), len(common))
    verdict = verdict_for_skill(
        effect, p_value, baseline_security=security[0],
        treatment_security=security[1], n_tasks=min_tasks if min_tasks != MIN_TASKS else n_tasks,
    )

    def rate_pair(metric: str) -> tuple[float, float]:
        left, right = pairs[metric]
        return _rate(left), _rate(right)

    return SkillEvalResult(
        skill=skill, version=version,
        task_success=rate_pair("success"), first_fix_rate=rate_pair("first_fix"),
        token_cost=rate_pair("tokens"), tool_calls=rate_pair("tool_calls"),
        redundant_reread_rate=rate_pair("reread"),
        irrelevant_change_ratio=rate_pair("irrelevant"),
        security_violations=security, p_value=p_value,
        effect_size=effect, verdict=verdict,
    )


__all__ = [
    "MIN_TASKS", "SkillEvalResult", "evaluate_skill", "skill_arm_config",
    "skill_arm_name", "verdict_for_skill",
]
