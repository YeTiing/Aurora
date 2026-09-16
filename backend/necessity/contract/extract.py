"""契约候选提取 —— 规范 §4.5 的 7 个来源。

## 设计原则：**挖不到可接受，挖错不可接受**（规范 §4.12）

它把这条列为「非目标」的第一条。理由很直接：一条错误的契约会拦住
**正确的改动**，比没有契约更糟。所以本模块的每个提取器都偏向保守 ——
只在证据明确时产出候选，宁缺勿滥。

## 各来源的证据强度（规范 §4.5）

    3 星  tests       同一符号在多个测试里被断言特定行为
    3 星  callgraph   某函数被调用后必被另一函数调用（顺序约束）
    3 星  history     被 revert / 带 fix 的提交
    2 星  exceptions  调用点普遍 try/except 同一异常
    2 星  types       返回 Optional 但从不返回 None
    2 星  similar     同类模块中 3+ 个采用同一模式
    1 星  trace       重复行为模式

本模块实现前 5 个基于**静态代码/索引**的来源；`history` 需要 git
（在 `judge.py` 里做裁判用）；`trace` 需要运行时轨迹（没数据即不产出）。
"""
from __future__ import annotations

import ast
import logging
import re
from pathlib import Path

from .schema import ContractCandidate, compute_confidence
from .similar import SIMILAR_MIN_OCCURRENCES, from_similar

logger = logging.getLogger("necessity.contract.extract")

# 相似实现被判为「同一模式」所需的最少重复次数（规范 §4.5：「3+ 个」）
SIMILAR_MIN_OCCURRENCES = 3

# 调用点普遍捕获同一异常所需的最少次数（「普遍」的量化）
EXCEPTION_MIN_OCCURRENCES = 3

# 判为「软删除」的命名模式。规范 §4.3 的例子是「删除必须软删除」。
_SOFT_DELETE_HINT = re.compile(r"(delete|remove|destroy)", re.I)
_HARD_DELETE_CALL = re.compile(
    r"\.delete\s*\(|DELETE\s+FROM|rmtree|unlink\s*\(|os\.remove\s*\(", re.I)


def _cand(kind: str, statement: str, sources: list[str], *,
          evidence_count: int = 1, guard_type: str = "",
          guard_scope: dict | None = None, polluted: bool = False) -> ContractCandidate:
    """统一的候选构造 —— 置信度由 `compute_confidence` 折算，不手工填。"""
    cid = f"c-{kind}-{abs(hash(statement)) % 10**8}"
    conf = compute_confidence(sources, evidence_count)
    return ContractCandidate(
        id=cid, statement=statement, sources=list(sources),
        confidence=conf, evidence_count=evidence_count,
        guard_type=guard_type, guard_scope=dict(guard_scope or {}),
        needs_review=conf < 0.8, polluted_by_stale=polluted,
    )


# ── 来源 1：测试断言（⭐⭐⭐）────────────────────────────────────

def from_tests(test_files: dict[str, str]) -> list[ContractCandidate]:
    """从测试文件里找「同一符号被多次断言」的行为契约。

    判据：某符号在 **≥2 个测试函数** 里被断言。单次断言可能只是巧合，
    多次说明它是被有意保证的行为。

    `test_files` 形如 `{"tests/test_x.py": "<源码>"}`。
    """
    out: list[ContractCandidate] = []
    per_symbol: dict[str, set[str]] = {}
    for path, src in (test_files or {}).items():
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            fn = getattr(node, "name", "")
            if not fn.startswith("test_"):
                continue
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Assert):
                    continue
                # 断言里出现的属性访问/名字 -> 被断言的符号
                for sub in ast.walk(inner.test):
                    name = _symbol_of(sub)
                    if name:
                        per_symbol.setdefault(name, set()).add(fn)

    for name, tests in sorted(per_symbol.items()):
        if len(tests) < 2:
            continue
        out.append(_cand(
            "tests_asserted",
            f"`{name}` 的行为被 {len(tests)} 个测试断言（{', '.join(sorted(tests)[:3])}）"
            "—— 该行为属于必须保持的契约",
            ["tests"], evidence_count=len(tests),
            guard_type="test_preserved",
            guard_scope={"kind": "tests", "selectors": sorted(tests)[:5]},
        ))
    return out


def _symbol_of(node) -> str:
    """从 AST 节点取出「被断言的符号名」。取不出返回空串。"""
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _symbol_of(node.func)
    if isinstance(node, ast.Name):
        # 排除常见的 pytest 断言辅助名
        return "" if node.id in ("pytest", "raises", "True", "False", "None") else node.id
    return ""


# ── 来源 2：调用关系（⭐⭐⭐）───────────────────────────────────

def from_callgraph(pairs: list[tuple[str, str]],
                   *, min_occurrences: int = SIMILAR_MIN_OCCURRENCES
                   ) -> list[ContractCandidate]:
    """从调用图找「固定先后顺序」的契约。

    `pairs` 是 `(前驱, 后继)` 调用对。若同一组合在 **≥min_occurrences**
    个不同位置出现，说明这不是偶然 —— 而是「必须按此顺序」的隐式约定。

    ⚠️ 判据刻意要求**多处重复**：单次调用关系太常见，把它当契约会让
    候选爆量且几乎全是噪声（规范 §4.12：「挖不到可接受，挖错不可接受」）。
    """
    from collections import Counter

    counts = Counter((a, b) for a, b in (pairs or []) if a and b and a != b)
    out: list[ContractCandidate] = []
    for (a, b), n in counts.items():
        if n < min_occurrences:
            continue
        out.append(_cand(
            "call_order",
            f"`{a}` 之后常常需要调用 `{b}`（{n} 处）—— 可能是顺序约定",
            ["callgraph"], evidence_count=n,
            guard_type="call_chain",
            guard_scope={"kind": "graph", "root": a, "direction": "callees"},
        ))
    return out


# ── 来源 3：异常处理（⭐⭐）─────────────────────────────────────

def from_exceptions(sources: dict[str, str]) -> list[ContractCandidate]:
    """调用点**普遍**捕获同一异常 -> 该异常是接口契约的一部分。

    判据：某异常名在 ≥3 个不同文件里被 `except` 捕获。这说明调用方
    普遍预期它会抛出 —— 那么「这个函数会抛这个异常」就是约定，
    改成返回 None 会破坏所有调用方。
    """
    from collections import defaultdict

    files_of: dict[str, set[str]] = defaultdict(set)
    for path, src in (sources or {}).items():
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler) or node.type is None:
                continue
            for name in _exception_names(node.type):
                files_of[name].add(str(path))

    out: list[ContractCandidate] = []
    for name, files in sorted(files_of.items()):
        if len(files) < EXCEPTION_MIN_OCCURRENCES:
            continue
        out.append(_cand(
            "exception_contract",
            f"`{name}` 在 {len(files)} 个文件里被捕获 —— "
            "调用方普遍预期它会被抛出，不应改成返回 None",
            ["exceptions"], evidence_count=len(files),
            # 异常契约没有直接对应的 guard 类型 —— 如实留空，
            # 由 `compile.py` 决定能否落进 8 类之一（不能就不注入）。
            guard_type="",
        ))
    return out


def _exception_names(node) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        return [node.attr]
    if isinstance(node, ast.Tuple):
        out: list[str] = []
        for e in node.elts:
            out.extend(_exception_names(e))
        return out
    return []


# ── 来源 4：类型声明（⭐⭐）────────────────────────────────────

def from_types(sources: dict[str, str]) -> list[ContractCandidate]:
    """返回 `Optional` 但**从不** return None -> 实际契约是「不会返回空」。

    判据：函数注解含 `Optional`/`| None`，但函数体内没有任何 `return None`。
    这类不一致是真实的隐式约定（调用方可能已经按「不返回空」写代码）。
    """
    out: list[ContractCandidate] = []
    for path, src in (sources or {}).items():
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _returns_optional(node):
                continue
            if _returns_none(node):
                continue
            out.append(_cand(
                "optional_never_none",
                f"`{node.name}` 声明可能返回空（Optional），但实现里从不返回 None"
                " —— 实际契约可能是「一定返回有效值」",
                ["types"], evidence_count=1,
                guard_type="signature_stable",
                guard_scope={"kind": "symbols",
                             "symbols": [{"file": str(path), "name": node.name}]},
            ))
    return out


def _returns_optional(fn) -> bool:
    ann = getattr(fn, "returns", None)
    if ann is None:
        return False
    text = ast.unparse(ann) if hasattr(ast, "unparse") else ""
    return "Optional" in text or "None" in text


def _returns_none(fn) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Return):
            if node.value is None:
                return True
            if isinstance(node.value, ast.Constant) and node.value.value is None:
                return True
    return False


def extract_all(*, test_files=None, call_pairs=None, sources=None,
                module_sources=None) -> list[ContractCandidate]:
    """跑所有可用的提取器。**每个失败都独立**，不影响其它来源。

    这是「挖不到可接受」在代码层的体现：某个来源的数据没给或解析失败，
    只是少几条候选，不是整个挖掘失败。
    """
    out: list[ContractCandidate] = []
    for label, fn in (
        ("tests", lambda: from_tests(test_files or {})),
        ("callgraph", lambda: from_callgraph(call_pairs or [])),
        ("exceptions", lambda: from_exceptions(sources or {})),
        ("types", lambda: from_types(sources or {})),
        ("similar", lambda: from_similar(module_sources or sources or {})),
    ):
        try:
            out.extend(fn())
        except Exception as e:
            logger.warning("契约提取器 %s 失败（其余来源继续）: %s", label, e)
    return out


__all__ = [
    "EXCEPTION_MIN_OCCURRENCES", "SIMILAR_MIN_OCCURRENCES", "extract_all",
    "from_callgraph", "from_exceptions", "from_similar", "from_tests", "from_types",
]
