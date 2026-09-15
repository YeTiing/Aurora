"""Skill 版本管理与准入登记 —— 规范 §7.5 / §7.9。

## 它解决的问题

规范 §7.1：Skill 不应在创建或导入后立即启用，而应先**证明**它能改善
Agent 表现。所以需要一个地方记录「哪个版本被评测过、结论是什么、
现在允不允许用」。

## `adopted` 是唯一开关（规范 §7.9）

    回滚 = 把 `adopted` 设为 false。**Skill 本身不变**，回滚无副作用。

所以本模块不存 Skill 的实现、不移动文件、不碰任何执行路径 ——
它只是一份登记表。这样「回滚」是一个纯数据操作，不需要撤销副作用。

## 退化回滚

规范 §7.5：观测 `skill_effect_size` / `skill_p_value`，若 effect_size 转负
且 p < 0.05，就把 `adopted` 置 false 并告警。

⚠️ 这与 A3/A4 的**安全类例外不同** —— Skill 评测不是安全功能，
所以它的恢复是**自动化**的（重新评测通过即可），不需要人工确认
（规范 §1.4 表格：A5 的恢复条件写的是「重新评测通过」，而 A3/A4 写的是
「需人工确认」）。这个差异是刻意的，别顺手统一。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from .skill_arms import SkillEvalResult

logger = logging.getLogger("necessity.eval.skill_registry")

# 退化判定阈值（规范 §7.5 直接给的，不是拍数）
DEGRADE_EFFECT_MAX = 0.0
DEGRADE_P_MAX = 0.05

# 版本变更后必须重新评测；这两项构成「同一个 Skill 的同一版本」的身份
DEFAULT_REGISTRY = ".necessity/skill_registry.json"


@dataclass
class SkillEntry:
    """一个 Skill 的当前状态 + 版本历史。

    `history` 保留每个版本的评测结果：只看最新版会丢掉「上一个版本更好」
    这个信息，而回滚恰恰需要它。
    """

    skill: str = ""
    version: str = ""
    fingerprint: str = ""
    adopted: bool = False
    adopted_at: float = 0.0
    eval_result: dict | None = None
    history: list[dict] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "skill": self.skill, "version": self.version,
            "fingerprint": self.fingerprint, "adopted": self.adopted,
            "adopted_at": self.adopted_at, "eval_result": self.eval_result,
            "history": self.history, "alerts": self.alerts,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SkillEntry":
        """容忍缺失字段 —— 旧版本写的注册表必须还能读。"""
        return cls(
            skill=str(d.get("skill", "")), version=str(d.get("version", "")),
            fingerprint=str(d.get("fingerprint", "")),
            adopted=bool(d.get("adopted", False)),
            adopted_at=float(d.get("adopted_at", 0.0) or 0.0),
            eval_result=d.get("eval_result"),
            history=list(d.get("history") or []),
            alerts=list(d.get("alerts") or []),
        )


class SkillRegistry:
    """`skill_registry.json` 的读写与准入判定。"""

    def __init__(self, path: str | Path = DEFAULT_REGISTRY) -> None:
        self.path = Path(path)
        self._entries: dict[str, SkillEntry] = {}
        self.load()

    # ── 持久化 ───────────────────────────────────────────────────

    def load(self) -> None:
        """读注册表。文件缺失/损坏都不抛 —— 当作「空注册表」。

        理由：注册表是**准入记录**而不是真相源。它坏了应当表现为
        「所有 Skill 都未准入」（保守），而不是让调用方起不来。
        """
        self._entries = {}
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("skill_registry 读取失败，按空注册表处理: %s", e)
            return
        for name, d in (raw or {}).items():
            if isinstance(d, dict):
                self._entries[str(name)] = SkillEntry.from_dict(d)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {k: v.to_dict() for k, v in self._entries.items()}
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 查询 ─────────────────────────────────────────────────────

    def get(self, skill: str) -> SkillEntry | None:
        return self._entries.get(skill)

    def all(self) -> dict[str, SkillEntry]:
        return dict(self._entries)

    def is_adopted(self, skill: str) -> bool:
        """唯一开关（规范 §7.9）。未登记 = 未准入（保守默认）。"""
        e = self._entries.get(skill)
        return bool(e and e.adopted)

    # ── 准入 ─────────────────────────────────────────────────────

    def record(self, result: SkillEvalResult, *, fingerprint: str = "",
               now: float = 0.0) -> SkillEntry:
        """登记一次评测结果，并按 verdict 更新准入状态。

        只有 `verdict == "adopt"` 才置 `adopted=True`。
        `inconclusive` **不得**准入 —— 规范 §7.8：「effect_size > 0 但
        p ≥ 0.05 → inconclusive —— 样本不足，不得准入」。
        `reject` 会**撤销**已准入状态（评测发现它变差了）。
        """
        e = self._entries.get(result.skill) or SkillEntry(skill=result.skill)
        e.version = result.version or e.version
        if fingerprint:
            e.fingerprint = fingerprint
        e.eval_result = result.to_dict()
        e.history.append(result.to_dict())

        if result.verdict == "adopt":
            e.adopted = True
            e.adopted_at = now or e.adopted_at
        elif result.verdict == "reject":
            # 拒收要撤销准入。否则「先准入、后发现变差」会一直开着。
            if e.adopted:
                e.alerts.append(
                    f"{result.skill}@{result.version} 重评测为 reject，已撤销准入")
            e.adopted = False
        # inconclusive：不动 adopted —— 样本不足既不该准入，也不该撤销
        # 一个已通过的版本（那是「证据不足」而非「证据为负」）。

        self._entries[result.skill] = e
        self.save()
        return e

    def set_adopted(self, skill: str, adopted: bool, *, now: float = 0.0) -> bool:
        """人工开关（规范 §7.9：回滚 = 设 `adopted=false`）。

        返回是否真的改了。未登记的 Skill 不能开启 —— 那等于绕过评测。
        """
        e = self._entries.get(skill)
        if e is None:
            return False
        if e.adopted == adopted:
            return False
        e.adopted = adopted
        if adopted:
            e.adopted_at = now or e.adopted_at
        self.save()
        return True

    # ── 退化回滚（规范 §7.5，接入 §1.4 的闭环）───────────────────

    def check_degraded(self, skill: str, effect_size: float,
                       p_value: float) -> dict:
        """按最新观测判断是否退化，必要时自动撤销准入。

        判据（规范 §7.5）：`effect_size 转负 且 p < 0.05`。
        两个条件都要 —— 只看 effect_size < 0 会把噪声当退化，
        只看 p 值则会在效应为正时误撤。

        与 A3/A4 不同：这里**自动回滚**，恢复也是自动的（重评测通过即可）。
        """
        triggered = effect_size < DEGRADE_EFFECT_MAX and p_value < DEGRADE_P_MAX
        out = {"skill": skill, "degraded": triggered,
               "effect_size": effect_size, "p_value": p_value, "action": ""}
        if not triggered:
            return out
        e = self._entries.get(skill)
        if e is None or not e.adopted:
            return out
        e.adopted = False
        e.alerts.append(
            f"{skill} 退化（effect_size={effect_size:.3f}, p={p_value:.3f}），"
            "已自动回滚准入；重新评测通过后可自动恢复")
        self.save()
        out["action"] = "adopted=false"
        return out

    def alerts(self) -> list[str]:
        return [a for e in self._entries.values() for a in e.alerts]


__all__ = [
    "DEGRADE_EFFECT_MAX", "DEGRADE_P_MAX", "SkillEntry", "SkillRegistry",
]
