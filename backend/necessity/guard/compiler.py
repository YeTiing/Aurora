"""compiler.py —— 自然语言约束 → 结构化约束（GUARD.md §3.3 / §4）。

核心设计（§4.1）：
    **编译可以用 LLM（一次性、容错、便宜），验证绝不能用。**
    本模块是全系统唯一允许出现 LLM 客户端类型的地方；checker.py /
    checks.py 不得导入任何 LLM 客户端（结构上保证验证路径确定性）。

为什么拒绝必须显式（§3.3）：
    静默忽略一个不可验证的约束，比不设这条约束更危险 —— 用户以为有
    保护，实际没有。所以 CompileResult 把「没被保护的约束」单独列成
    `rejected`，每条带 reason + suggestion，并供 §8 的
    `constraint_rejected` 审计表使用。

LLM 只负责「切候选 + 标注类型」，**是否能验证由白名单判定，不由 LLM
决定** —— 否则 LLM 幻觉一个类型就能绕过边界。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Callable

from .heuristics import from_heuristics, reject_unverifiable
from .spec import (
    CONSTRAINT_TYPES,
    CompiledConstraint,
    CompileResult,
    StructuredConstraint,
    validate,
)

logger = logging.getLogger("necessity.guard.compiler")

#: LLM 调用签名：prompt -> 文本（JSON）。由宿主注入，测试用 fake。
LLMClient = Callable[[str], str]

__all__ = ["LLMClient", "compile_constraints", "compile_from_task", "detect_conflicts"]


def compile_constraints(
    texts: list[str],
    *,
    llm: LLMClient | None = None,
    workspace: str = "",
    default_action: str | None = None,
) -> CompileResult:
    """把一批自然语言约束编译成结构化约束集。

    llm=None 时走**纯启发式**路径（离线可测、可复现）；给了 llm 则先让
    其产出候选 JSON，启发式兜底。无论哪条路径，spec.validate 都是最终
    裁决者。

    default_action：未显式声明 on_violation 的约束继承它。为 None 时留空，
    由互操作层用全局 default_action 决定（不在编译期硬编码策略）。
    """
    result = CompileResult(used_llm=llm is not None)
    items = [t for t in (texts or []) if str(t).strip()]
    if not items:
        result.notes.append("未提供任何约束 —— Guard 将不做任何限制（不是出错）")
        return result

    candidates: list[dict] = []
    if llm is not None:
        candidates = _from_llm(llm, items, result)
    if not candidates:
        candidates = from_heuristics(items)

    seen: dict[str, int] = {}
    for i, cand in enumerate(candidates, start=1):
        raw = str(cand.get("raw_text") or cand.get("message") or "")
        hit = reject_unverifiable(raw)
        if hit is not None:
            result.rejected.append(CompiledConstraint(
                False, raw_text=raw, index=i, reason=hit[0], suggestion=hit[1]))
            continue
        cr = validate(_to_structured(cand, i, default_action), i)
        if not cr.ok:
            result.rejected.append(cr)
            continue
        fp = _fingerprint(cr.constraint)
        if fp in seen:
            result.notes.append(
                f"第 {i} 条与第 {seen[fp]} 条重复，已合并为 {cr.constraint.id}")
            continue
        seen[fp] = i
        result.accepted.append(cr.constraint)

    result.conflicts = detect_conflicts(result.accepted)
    return result


# ── LLM 路径 ────────────────────────────────────────────────────

_PROMPT = """你是约束编译器。把用户的自然语言约束编译为 JSON 数组，每项形如：
{{"raw_text": "<原句>", "type": "<类型>", "scope": {{...}}, "on_violation": "warn"}}

可用的 type 只有这 8 种（超出的一律不要输出，交由系统拒绝）：
{types}

scope 约定：
  file_scope -> {{"kind":"path_glob","patterns":["src/parser/**"]}}
  symbol_scope -> {{"kind":"symbol","pattern":"_foo*","qualifier":"public"}}
  signature_stable -> {{"kind":"symbols","symbols":[{{"file":"a.py","name":"parse"}}]}}
  call_chain -> {{"kind":"graph","root":"main","direction":"callees"}}
  impact_limit -> {{"kind":"symbols","symbols":[...],"max_files":5}}
  dependency_frozen -> {{"kind":"manifest","files":["requirements.txt","pyproject.toml"]}}
  test_preserved -> {{"kind":"tests","selectors":["tests/test_x.py::test_y"]}}
  size_limit -> {{"kind":"diff","max_added":50}}

只输出 JSON 数组，不要解释。约束：
{items}"""


def _from_llm(llm: LLMClient, items: list[str], result: CompileResult) -> list[dict]:
    """调 LLM 拿候选。失败即降级到启发式（编译是一次性容错步骤，不抛）。"""
    prompt = _PROMPT.format(
        types=", ".join(sorted(CONSTRAINT_TYPES)),
        items="\n".join(f"{i}. {t}" for i, t in enumerate(items, 1)),
    )
    try:
        raw = llm(prompt)
    except Exception as e:
        logger.warning("LLM 编译失败，降级为启发式: %s", e)
        result.notes.append(f"LLM 编译失败，已降级为启发式解析: {type(e).__name__}")
        return []
    parsed = _parse_json_array(raw)
    if not parsed:
        result.notes.append("LLM 输出无法解析为 JSON 数组，已降级为启发式解析")
        return []
    for i, cand in enumerate(parsed):     # 回填原句：LLM 可能改写，审计要原文
        if i < len(items):
            cand.setdefault("raw_text", items[i])
    return parsed


def _parse_json_array(raw: str) -> list[dict]:
    """容错解析：容忍 ```json 围栏与前后废话。"""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    data = None
    try:
        data = json.loads(text)
    except Exception:
        m = re.search(r"\[.*\]", text, re.S)
        if m:
            try:
                data = json.loads(m.group(0))
            except Exception:
                return []
    if data is None:
        return []
    if isinstance(data, dict):
        data = [data]
    return [d for d in data if isinstance(d, dict)]


def _to_structured(cand: dict, index: int,
                   default_action: str | None = None) -> StructuredConstraint:
    return StructuredConstraint(
        id=str(cand.get("id") or f"c{index}"),
        type=str(cand.get("type") or ""),
        scope=cand.get("scope") if isinstance(cand.get("scope"), dict) else {},
        predicate=cand.get("predicate") if isinstance(cand.get("predicate"), dict) else {},
        phase=str(cand.get("phase") or "both"),
        # 空串 = 未指定；interceptor 会回落到全局 default_action
        on_violation=str(cand.get("on_violation") or default_action or ""),
        message=str(cand.get("message") or cand.get("raw_text") or ""),
        raw_text=str(cand.get("raw_text") or ""),
    )


def _fingerprint(sc: StructuredConstraint) -> str:
    return json.dumps([sc.type, sc.scope], sort_keys=True, ensure_ascii=False)


# ── 冲突检测（§4.3）─────────────────────────────────────────────

def detect_conflicts(cs: list[StructuredConstraint]) -> list[dict]:
    """只做能确定性判定的两类冲突：

      1. **互斥**：多条 file_scope 白名单交集为空 → 等于禁止所有改动，
         任务不可完成。
      2. **自相矛盾**：同一路径同时在 allow 与 exclude 列表里。

    循环依赖（「改 A 前先改 B」）需要改动顺序信息，Guard 拿不到，
    故不做 —— 诚实标注，不用猜测填补（§9 第 2 条）。
    """
    conflicts: list[dict] = []
    globs = [c for c in cs if c.type == "file_scope"]
    if len(globs) >= 2:
        sets = [set(c.scope.get("patterns") or []) for c in globs]
        if all(sets) and not set.intersection(*sets):
            conflicts.append({
                "ids": [c.id for c in globs],
                "kind": "mutually_exclusive",
                "reason": "多条 file_scope 白名单交集为空，等于禁止所有改动，任务不可完成",
            })
    for c in cs:
        dup = set(c.scope.get("exclude") or []) & set(c.scope.get("patterns") or [])
        if dup:
            conflicts.append({
                "ids": [c.id], "kind": "self_contradiction",
                "reason": f"同一路径同时出现在允许与排除列表: {sorted(dup)}",
            })
    return conflicts


def compile_from_task(task: dict, *, llm: LLMClient | None = None) -> CompileResult:
    """从任务定义编译约束。

    兼容两种输入形态：
      - task["constraints"] 是字符串列表（自然语言，GUARD.md 主路径）
      - 已经是结构化 dict 列表（跳过 NL 编译，直接校验）
    """
    raw = (task or {}).get("constraints")
    if raw is None and isinstance((task or {}).get("guard"), dict):
        raw = task["guard"].get("constraints")
    if not raw:
        res = CompileResult()
        res.notes.append("任务未定义约束 —— Guard 不做任何限制（不是出错）")
        return res
    if isinstance(raw, str):
        raw = [raw]
    if isinstance(raw[0], dict):
        res = CompileResult()
        for i, d in enumerate(raw, 1):
            cr = validate(_to_structured(d, i), i)
            (res.accepted if cr.ok else res.rejected).append(
                cr.constraint if cr.ok else cr)
        res.conflicts = detect_conflicts(res.accepted)
        return res
    return compile_constraints([str(t) for t in raw], llm=llm)
