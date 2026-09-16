"""候选生成策略 —— 规范 §8.4「四种候选策略（★ 这是『有区分度』的关键）」。

## 为什么不是「换 prompt 措辞」

规范把四种策略定义成**不同的硬约束**，而不是四种说话方式：

    minimal               只做最小必要改动
    backward_compatible   公共接口签名不变
    structural            允许重构
    security_first        不新增依赖、不扩大权限

约束不同，产出**才可能真的不同**。规范把「候选多样性 ≥75%」列为硬门禁，
理由（§8.6 主要风险）：「不同 prompt 可能产出一样的补丁，导致『竞争』是假的」。
换措辞那种做法必然趋同 —— 因为底层任务没变。

## 本模块的职责

它生成的是**候选的约束声明**（喂给 guard 的 `StructuredConstraint`），
以及每个候选的提示词片段。真正的补丁由 Agent 在隔离环境里产出 ——
本模块不调用任何 Agent，保证可离线测试。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from backend.necessity.guard.spec import StructuredConstraint

from .schema import STRATEGIES


@dataclass
class CandidateSpec:
    """一个候选的生成规格：约束 + 提示词片段。"""

    strategy: str
    constraints: list[StructuredConstraint] = field(default_factory=list)
    prompt_hint: str = ""
    # 触发条件（规范 §1.7 的成本预算）：影响面 > 3 文件 或 用户显式要求
    min_impact_files: int = 0

    def to_dict(self) -> dict:
        return {"strategy": self.strategy,
                "prompt_hint": self.prompt_hint,
                "constraints": [c.id for c in self.constraints]}


# 各策略的提示词片段。**只描述倾向，不描述做法** ——
# 给出做法会让所有候选都照抄同一段实现，反而消灭多样性。
_PROMPT_HINTS: dict[str, str] = {
    "minimal": (
        "只做完成本任务所必需的改动。不要顺手改动无关代码、"
        "不要顺便重构、不要添加未被要求的参数或配置。"
    ),
    "backward_compatible": (
        "保持所有公共接口的签名不变。如果必须改变行为，"
        "通过新增可选参数或适配层实现，不要修改既有调用方。"
    ),
    "structural": (
        "允许在必要时重构以让改动更一致（例如提取公共逻辑、"
        "调整模块边界），但不要改变对外行为。"
    ),
    "security_first": (
        "不新增任何依赖、不扩大权限、不执行外部命令。"
        "若某个实现方式需要上述任一项，改用更保守的方式。"
    ),
}


def build_specs(*, impact_files: int = 0, strategies: list[str] | None = None,
                max_files: int = 3) -> list[CandidateSpec]:
    """按策略构造候选规格。

    `impact_files` 用于决定是否值得开竞争（规范 §1.7：A6 是 4× 成本，
    只在「影响面 > 3 文件或用户显式要求」时启用）。
    """
    names = strategies or list(STRATEGIES)
    out: list[CandidateSpec] = []
    for name in names:
        if name not in STRATEGIES:
            continue
        out.append(CandidateSpec(
            strategy=name,
            constraints=_constraints_for(name, max_files=max_files),
            prompt_hint=_PROMPT_HINTS.get(name, ""),
            min_impact_files=0,
        ))
    return out


def _constraints_for(strategy: str, *, max_files: int) -> list[StructuredConstraint]:
    """策略 → 实际约束（编译成 guard 的 8 类型，于是**用现成的检查器**）。

    ⚠️ 这里的关键选择：把策略约束编译成 guard 约束，而不是在竞争模块里
    自己写一套检查逻辑。理由与 A2 的「执行层白送」同源 ——
    两套检查逻辑必然分叉，而分叉后「这里过了、那里没过」无法解释。
    """
    if strategy == "backward_compatible":
        return [StructuredConstraint(
            id="compete-backward-compatible", type="signature_stable",
            scope={"kind": "symbols", "symbols": []},   # 由调用方填入具体符号
            predicate={}, phase="both", on_violation="block",
            message="公共接口签名必须保持不变", raw_text="competition:backward_compatible")]

    if strategy == "minimal":
        return [StructuredConstraint(
            id="compete-minimal", type="impact_limit",
            scope={"kind": "symbols", "symbols": [], "max_files": max_files},
            predicate={}, phase="both", on_violation="warn",
            message=f"最小改动：影响面不超过 {max_files} 个文件",
            raw_text="competition:minimal")]

    if strategy == "security_first":
        # 安全优先没有对应的 guard 类型（8 类型里没有「不新增依赖」之外的
        # 安全类约束）—— 所以它的约束由 **security_scanner 的评分**承担，
        # 而不是 guard。这里如实返回空，不硬塞一个不相关的类型。
        return []

    # structural：允许重构，所以**不施加**额外约束 —— 这是它与其他三个的
    # 区别所在。给它也加上约束的话，四种策略就会趋同（多样性门禁会失败）。
    return []


def worth_competing(impact_files: int, *, user_requested: bool = False,
                    threshold: int = 3) -> tuple[bool, str]:
    """是否值得开竞争（规范 §1.7 的成本预算）。

    A6 是 **4× token / 4× 时间**，所以不能默认开。规范的触发条件是
    「影响面 > 3 文件 **或** 用户显式要求」。

    返回 `(是否启用, 原因)` —— 原因要能进日志，否则用户不知道为什么
    没开竞争（那会让「功能没生效」与「情况不适用」分不开）。
    """
    if user_requested:
        return True, "用户显式要求"
    if impact_files > threshold:
        return True, f"影响面 {impact_files} 个文件 > 阈值 {threshold}"
    return False, (f"影响面 {impact_files} 个文件未超过阈值 {threshold}"
                   f"（A6 是 4× 成本，仅在大改动时启用）")


__all__ = ["CandidateSpec", "build_specs", "worth_competing"]
