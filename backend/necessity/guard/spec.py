"""spec.py —— 约束定义与校验（GUARD.md §2 / §3）。

设计要点（为什么长这样）：
  1. **类型白名单是硬边界。** GUARD.md §3.1 列了 8 种可机器验证的类型，
     §3.2 列了必须拒绝的类型。白名单放在这里，checker 只认白名单内的
     类型 —— 这样「新加一个类型但忘了实现检查器」不可能发生：没实现
     检查器，check_constraint 会明确报 unsupported，而不是静默放行。
  2. **拒绝不是异常，是数据。** §3.3 要求不可验证的约束必须带改写建议
     返回。CompiledConstraint 同时承载「通过」和「拒绝」两种结果，
     调用方一次就能拿到完整的编译报告（含 constraint_rejected 审计表
     所需的 raw_text / reason / suggestion，见 §8）。
  3. **scope 用 dict 不用联合类型。** 结构化约束是外部输入（LLM 产出 +
     用户确认），宽松解析比严格类型更能容错；校验集中在 validate()。
"""
from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "CONSTRAINT_TYPES",
    "PHASES",
    "ON_VIOLATION",
    "STRUCTURAL_TYPES",
    "CompiledConstraint",
    "CompileResult",
    "StructuredConstraint",
]

# 可机器验证的类型（GUARD.md §3.1）—— 这是能力边界，不在此列一律拒绝。
CONSTRAINT_TYPES: frozenset[str] = frozenset({
    "file_scope",         # 只准改 <glob>
    "symbol_scope",       # 只准改 / 不准改某类符号
    "signature_stable",   # 指定符号签名不变
    "call_chain",         # 只准改 main() 可达的符号
    "impact_limit",       # 影响面不超过 N 个文件
    "dependency_frozen",  # 不新增第三方依赖
    "test_preserved",     # 指定测试必须仍通过
    "size_limit",         # 改动不超过 N 行
})

# 需要调用图/符号索引才能验证的类型（LSP 不可用时降级跳过，§8.2）。
STRUCTURAL_TYPES: frozenset[str] = frozenset({
    "symbol_scope", "signature_stable", "call_chain", "impact_limit",
})

PHASES: frozenset[str] = frozenset({"pre", "post", "both"})
ON_VIOLATION: frozenset[str] = frozenset({"block", "rollback", "warn", "ask"})


@dataclass
class StructuredConstraint:
    """一条已结构化的约束（GUARD.md §2.2）。

    scope 的约定（按 type 分派，checker 按同一约定读取）：
      file_scope         {"kind":"path_glob","patterns":[...],"exclude":[...]}
      symbol_scope       {"kind":"symbol","pattern":...,"qualifier":"public"|"private"}
      signature_stable   {"kind":"symbols","symbols":[{"file":..,"name":..}]}
      call_chain         {"kind":"graph","root":"main","root_file":"...","direction":"callees"}
      impact_limit       {"kind":"symbols","symbols":[...],"max_files":N}
      dependency_frozen  {"kind":"manifest","files":[...],"extra_patterns":[...]}
      test_preserved     {"kind":"tests","selectors":[...]}
      size_limit         {"kind":"diff","max_added":N,"max_removed":N,"max_files":N}
    """
    id: str
    type: str
    scope: dict = field(default_factory=dict)
    predicate: dict = field(default_factory=dict)
    phase: str = "both"
    on_violation: str = "warn"
    message: str = ""
    raw_text: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id, "type": self.type, "scope": self.scope,
            "predicate": self.predicate, "phase": self.phase,
            "on_violation": self.on_violation, "message": self.message,
        }


@dataclass
class CompiledConstraint:
    """编译结果的一条：**通过** 或 **拒绝**，二者必居其一。

    拒绝项必须携带 reason + suggestion（§3.3「绝不允许静默忽略」）。
    `raw_text` 用于写入 `constraint_rejected` 审计表 —— 它让「哪些约束
    没被保护」变得可见（§8）。
    """
    ok: bool
    constraint: StructuredConstraint | None = None
    raw_text: str = ""
    reason: str = ""
    suggestion: str = ""
    index: int = 0

    @property
    def type(self) -> str:
        return self.constraint.type if self.constraint else ""

    def rejection(self) -> dict:
        return {"raw_text": self.raw_text, "reason": self.reason,
                "suggestion": self.suggestion, "index": self.index}


@dataclass
class CompileResult:
    """一次编译的完整产出。"""
    accepted: list[StructuredConstraint] = field(default_factory=list)
    rejected: list[CompiledConstraint] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    used_llm: bool = False

    @property
    def ok(self) -> bool:
        """无冲突即算编译成功（拒绝项不影响：它们本来就不该被保护）。"""
        return not self.conflicts

    def report(self) -> str:
        """给用户看的文本（§3.3 的 ✗ 格式）。"""
        lines: list[str] = []
        for c in self.accepted:
            lines.append(f"✓ [{c.id}] {c.type}: {c.message}")
        for r in self.rejected:
            lines.append(
                f"✗ 约束不可验证：第 {r.index} 条\n"
                f'  "{r.raw_text}"\n'
                f"  原因：{r.reason}\n"
                f"  建议：{r.suggestion}"
            )
        for c in self.conflicts:
            lines.append(f"⚠ 约束冲突：{' + '.join(c.get('ids', []))} —— {c.get('reason', '')}")
        return "\n".join(lines)


# ── 校验 ─────────────────────────────────────────────────────────

#: 需要验证器实现的类型（未实现 → 明确报错，不静默放行）
_IMPLEMENTED = CONSTRAINT_TYPES


def validate(sc: StructuredConstraint, index: int = 0) -> CompiledConstraint:
    """把一条结构化约束校验成「可用 / 拒绝」。

    这里只做**结构合法性**校验（type/phase/scope 形状）；不可验证语义的
    拒绝在 compiler.py（因为它要处理自然语言原文）。
    """
    raw = sc.raw_text or sc.message or sc.type

    if not sc.type:
        return CompiledConstraint(False, raw_text=raw, index=index,
                                  reason="缺少 type 字段",
                                  suggestion="请注明约束类型（如 file_scope / size_limit）")
    if sc.type not in CONSTRAINT_TYPES:
        return CompiledConstraint(
            False, raw_text=raw, index=index,
            reason=f"类型 '{sc.type}' 不在可机器验证清单内（GUARD.md §3.1）",
            suggestion="改写为清单内类型之一，或提供可执行的检查器",
        )
    if sc.type not in _IMPLEMENTED:
        return CompiledConstraint(
            False, raw_text=raw, index=index,
            reason=f"类型 '{sc.type}' 的检查器尚未实现",
            suggestion="该约束当前无法保护；请移除或换用已实现的类型",
        )
    if sc.phase not in PHASES:
        return CompiledConstraint(False, raw_text=raw, index=index,
                                  reason=f"phase 非法: {sc.phase!r}",
                                  suggestion="phase 须为 pre / post / both")
    # 空串是合法的：表示「未指定，继承全局 default_action」。
    # 硬编码一个策略会覆盖调用方的灰度配置（INTEGRATION.md §8.1 默认 warn）。
    if sc.on_violation and sc.on_violation not in ON_VIOLATION:
        return CompiledConstraint(False, raw_text=raw, index=index,
                                  reason=f"on_violation 非法: {sc.on_violation!r}",
                                  suggestion="on_violation 须为 block / rollback / warn / ask")

    scope = sc.scope or {}
    if not isinstance(scope, dict) or not scope.get("kind"):
        return CompiledConstraint(False, raw_text=raw, index=index,
                                  reason="scope 缺失或格式非法",
                                  suggestion="scope 须为 {'kind': ..., ...} 结构")

    err = _validate_scope(sc.type, scope)
    if err:
        return CompiledConstraint(False, raw_text=raw, index=index,
                                  reason=err, suggestion="按该类型的 scope 约定补全字段")

    if not sc.id:
        sc.id = f"c{index}"
    return CompiledConstraint(True, constraint=sc, raw_text=raw, index=index)


def _validate_scope(ctype: str, scope: dict) -> str:
    """按类型检查 scope 的必需字段。返回错误信息，合法则返回空串。"""
    kind = scope.get("kind")
    if ctype == "file_scope":
        if not scope.get("patterns"):
            return "file_scope 缺少 patterns（允许的 glob 列表）"
    elif ctype == "symbol_scope":
        if not scope.get("pattern"):
            return "symbol_scope 缺少 pattern（符号名匹配）"
    elif ctype == "signature_stable":
        if not scope.get("symbols"):
            return f"{ctype} 缺少 symbols 列表"
    elif ctype == "impact_limit":
        # 影响面可以锚在「被改符号」（symbols）或「被改文件」（patterns）上。
        # 两者都没有就无法确定起点，拒绝。
        if not scope.get("symbols") and not scope.get("patterns"):
            return "impact_limit 需要 symbols（符号起点）或 patterns（文件起点）"
        if not scope.get("max_files"):
            return "impact_limit 缺少 max_files（允许的最大文件数）"
    elif ctype == "call_chain":
        if not scope.get("root"):
            return "call_chain 缺少 root（可达性起点，如 main）"
    elif ctype == "dependency_frozen":
        if kind not in ("manifest", "import", "any"):
            return "dependency_frozen 的 kind 须为 manifest / import / any"
    elif ctype == "test_preserved":
        if not scope.get("selectors"):
            return "test_preserved 缺少 selectors（要保证通过的测试）"
    elif ctype == "size_limit":
        if not any(k in scope for k in ("max_added", "max_removed", "max_files")):
            return "size_limit 未给出任何阈值（max_added/max_removed/max_files）"
    return ""
