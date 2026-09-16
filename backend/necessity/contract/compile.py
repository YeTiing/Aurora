"""契约 → guard 约束 —— 规范 §4.3 的核心洞察：「**执行层白送**」。

## 为什么这层很短

规范 §4.3 的原话：「**不新建执行机制。** 挖出的契约编译成 `guard` 已有的
8 种类型」。所以本模块的工作只是**形状转换**，不涉及任何拦截/回滚逻辑 ——
那些 `guard/interceptor.py` 已经有了。

## 一条硬要求（规范 §4.9）

    「编译成功率 **100%**（硬要求）：强证据契约必须能编译成 8 类型之一」

注意它的措辞是**强证据**契约。所以本模块的行为是：
    · 强证据（guard_type 非空且 scope 合法）-> 必须成功编译，否则是**我们的 bug**
    · 弱证据（guard_type 为空）-> 如实返回「无法编译」并说明原因，
      不硬塞进某个类型（硬塞会产出一条错误的约束，那比没有更糟）

## scope 形状必须精确匹配

`guard/spec.py` 的 `StructuredConstraint` 注释里逐类型写了 scope 约定，
`checker` 按同一约定读取。形状不对不会报错 —— 只会**静默不生效**
（checker 读不到它要的键就跳过）。所以每种类型在这里都显式构造。
"""
from __future__ import annotations

import logging

from backend.necessity.guard.spec import StructuredConstraint, validate

from .schema import ContractCandidate

logger = logging.getLogger("necessity.contract.compile")

# 契约产出时统一用的默认违反动作。
#
# 规范 §4.7 的关键设计：**自动注入的契约首次被违反时降级为 warn 而非 block**
# ——「用户还没确认过它」。所以默认动作是 `warn`，用户确认后才升级为 block
# （由 `review.py` 的确认流程负责）。
DEFAULT_ON_VIOLATION = "warn"


class CompileError(Exception):
    """强证据契约**应当**能编译却失败了 —— 那是我们的 bug，不是数据问题。"""


def to_guard_constraints(cands: list[ContractCandidate], *,
                         on_violation: str = DEFAULT_ON_VIOLATION,
                         strict: bool = False) -> tuple[list[StructuredConstraint], list[str]]:
    """把候选编译成 `guard` 约束。

    返回 `(constraints, problems)`。

    `strict=True` 时，**强证据**契约编译失败会抛 `CompileError` ——
    规范 §4.9 把「强证据 100% 可编译」列为硬要求，静默跳过会让这条
    要求无从验证。默认 `strict=False`（生产路径不该因为一条契约崩掉），
    但问题都会记进 `problems`。
    """
    out: list[StructuredConstraint] = []
    problems: list[str] = []

    for c in (cands or []):
        if not c.guard_type:
            # 弱证据：候选本身没给出可编译的目标类型（如异常契约）。
            # 如实记录，**不硬塞** —— 硬塞会产出错误约束。
            problems.append(
                f"{c.id}: 无对应的 guard 类型（{c.statement[:40]}…）—— 不注入")
            continue

        try:
            sc = _build(c, on_violation)
        except Exception as e:
            msg = f"{c.id}: 编译失败 {type(e).__name__}: {e}"
            problems.append(msg)
            if strict and c.confidence >= 0.8:
                raise CompileError(msg) from e
            continue

        # 用 guard 自己的校验器验证 —— 这是「形状对不对」的权威判据，
        # 而不是我自己写一套（两套判据必然会分叉）。
        cr = validate(sc, index=len(out) + 1)
        if not cr.ok:
            msg = f"{c.id}: 编译出的约束未通过 guard 校验：{cr.reason}"
            problems.append(msg)
            if strict and c.confidence >= 0.8:
                raise CompileError(msg)
            continue

        out.append(cr.constraint)

    return out, problems


def _build(c: ContractCandidate, on_violation: str) -> StructuredConstraint:
    """按 `guard_type` 构造约束（scope 形状逐类型精确匹配 spec 的约定）。"""
    t = c.guard_type
    scope = dict(c.guard_scope or {})

    if t == "test_preserved":
        # spec: {"kind":"tests","selectors":[...]}
        if "selectors" not in scope:
            raise ValueError("test_preserved 需要 scope.selectors")
        scope.setdefault("kind", "tests")

    elif t == "call_chain":
        # spec: {"kind":"graph","root":...,"direction":"callees"}
        if "root" not in scope:
            raise ValueError("call_chain 需要 scope.root")
        scope.setdefault("kind", "graph")
        scope.setdefault("direction", "callees")

    elif t == "symbol_scope":
        # spec: {"kind":"symbol","pattern":...,"qualifier":"public"|"private"}
        if "pattern" not in scope:
            raise ValueError("symbol_scope 需要 scope.pattern")
        scope.setdefault("kind", "symbol")

    elif t == "signature_stable":
        # spec: {"kind":"symbols","symbols":[{"file":..,"name":..}]}
        syms = scope.get("symbols")
        if not syms:
            raise ValueError("signature_stable 需要 scope.symbols")
        scope.setdefault("kind", "symbols")

    elif t == "impact_limit":
        # spec: {"kind":"symbols","symbols":[...],"max_files":N}
        if "max_files" not in scope:
            raise ValueError("impact_limit 需要 scope.max_files")
        scope.setdefault("kind", "symbols")

    elif t in ("file_scope", "dependency_frozen", "size_limit"):
        # 这三类目前不由契约挖掘产出（没有对应的隐式约定来源）。
        # 显式拒绝而不是放任 —— 一个形状不对的约束会被 checker 静默跳过。
        raise ValueError(f"{t} 不由契约挖掘产出，缺 scope 约定")

    else:
        raise ValueError(f"未知的 guard 类型 {t!r}")

    return StructuredConstraint(
        id=c.id, type=t, scope=scope,
        predicate={}, phase="both", on_violation=on_violation,
        message=c.statement,
        raw_text=c.statement,
    )


__all__ = ["CompileError", "DEFAULT_ON_VIOLATION", "to_guard_constraints"]
