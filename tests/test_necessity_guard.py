"""Constraint Guard 测试（第一部分）—— 约束定义 / 编译 / 确定性检查器。

对应任务书：8 种类型各有通过+违反用例、不可验证必须明确拒绝、编译可注入
LLM 但验证路径与 LLM 无关。全部离线。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.necessity.guard.checker import CheckContext, check_constraints  # noqa: E402
from backend.necessity.guard.compiler import compile_constraints, detect_conflicts  # noqa: E402
from backend.necessity.guard.spec import StructuredConstraint, validate  # noqa: E402
from backend.necessity.hooks import FileChange  # noqa: E402



# ── 测试替身 ────────────────────────────────────────────────────

class FakeStore:
    """内存调用图 —— 边是 (src_id -> dst_id, file)。"""

    def __init__(self, edges, symbols=None):
        self.edges = edges
        self.symbols = symbols or {}

    def callers(self, dst_id):
        return [{"src_id": s, "file": f} for s, d, f in self.edges if d == dst_id]

    def callees(self, src_id):
        return [{"dst_id": d, "file": f} for s, d, f in self.edges if s == src_id]

    def get_symbol(self, sid):
        return self.symbols.get(sid)


def sc(cid, ctype, scope, predicate=None, message=""):
    return StructuredConstraint(id=cid, type=ctype, scope=scope,
                                predicate=predicate or {}, message=message)


def change(path, added=0, removed=0, by_agent=True):
    return FileChange(path, "modified", added, removed, by_agent)


# ── 1. 8 种可验证类型：通过 / 违反 ──────────────────────────────

def test_file_scope_pass_and_violate():
    c = sc("c1", "file_scope", {"kind": "path_glob", "patterns": ["src/parser/**"]})
    ctx = CheckContext()
    assert check_constraints([change("src/parser/a.py")], [c], ctx) == []
    v = check_constraints([change("src/utils/b.py")], [c], ctx)
    assert len(v) == 1 and v[0].constraint_id == "c1"
    assert v[0].paths == ["src/utils/b.py"]


def test_symbol_scope_pass_and_violate():
    # 「只准改非 public 符号」→ allowlist + qualifier=private
    c = sc("c2", "symbol_scope",
           {"kind": "symbol", "pattern": "*", "qualifier": "private"})
    ctx = CheckContext(symbols={"a.py": [{"name": "_hidden", "kind": "function"}]})
    assert check_constraints([change("a.py")], [c], ctx) == []
    ctx.symbols = {"a.py": [{"name": "public_api", "kind": "function"}]}
    v = check_constraints([change("a.py")], [c], ctx)
    assert len(v) == 1 and "public_api" in v[0].detail


def test_symbol_scope_no_index_is_unsupported_not_violation():
    """无符号索引 → 不判违反也不假装通过（§8.2 降级）。"""
    c = sc("c2", "symbol_scope", {"kind": "symbol", "pattern": "*"})
    ctx = CheckContext()
    assert check_constraints([change("a.py")], [c], ctx) == []
    assert ctx.unsupported == ["c2"]


def test_signature_stable_pass_and_violate():
    c = sc("c3", "signature_stable",
           {"kind": "symbols", "symbols": [{"file": "a.py", "name": "parse"}]})
    base = {"a.py::parse": "def parse(x: int) -> str"}
    ctx = CheckContext(signatures=dict(base), baseline_signatures=dict(base))
    assert check_constraints([change("a.py")], [c], ctx) == []
    ctx.signatures["a.py::parse"] = "def parse(x: int, y: int) -> str"
    v = check_constraints([change("a.py")], [c], ctx)
    assert len(v) == 1 and v[0].constraint_id == "c3"


def test_call_chain_pass_and_violate():
    store = FakeStore([
        ("app.py::main", "backend.necessity.py::run", "backend.necessity.py"),
        ("backend.necessity.py::run", "util.py::help", "util.py"),
    ], symbols={
        "app.py::main": {"id": "app.py::main", "file": "app.py"},
        "backend.necessity.py::run": {"id": "backend.necessity.py::run", "file": "backend.necessity.py"},
        "util.py::help": {"id": "util.py::help", "file": "util.py"},
    })
    c = sc("c4", "call_chain",
           {"kind": "graph", "root": "app.py::main", "direction": "callees"})
    ctx = CheckContext(store=store)
    assert check_constraints([change("backend.necessity.py")], [c], ctx) == []
    v = check_constraints([change("unrelated.py")], [c], ctx)
    assert len(v) == 1 and v[0].paths == ["unrelated.py"]


def test_call_chain_no_store_is_unsupported():
    c = sc("c4", "call_chain", {"kind": "graph", "root": "main"})
    ctx = CheckContext()
    assert check_constraints([change("x.py")], [c], ctx) == []
    assert ctx.unsupported == ["c4"]


def test_impact_limit_pass_and_violate():
    """analyze 会向上找 callers：被 3 个文件调用 → 影响面超 1。

    FakeStore 第三个字段是「引用方所在文件」（与真实 store 的
    callers().file 语义一致），必须各不相同，否则影响面只算 1 个文件。
    """
    store = FakeStore([
        ("a.py::f", "t.py::f", "a.py"), ("b.py::f", "t.py::f", "b.py"),
        ("c.py::f", "t.py::f", "c.py"),
    ], symbols={"t.py::f": {"id": "t.py::f", "file": "t.py"}})
    c = sc("c5", "impact_limit",
           {"kind": "symbols", "symbols": [{"id": "t.py::f"}], "max_files": 5})
    ctx = CheckContext(store=store)
    assert check_constraints([change("t.py")], [c], ctx) == []
    c.scope["max_files"] = 1
    v = check_constraints([change("t.py")], [c], ctx)
    assert len(v) == 1 and v[0].constraint_id == "c5"


def test_dependency_frozen_pass_and_violate():
    c = sc("c6", "dependency_frozen",
           {"kind": "manifest", "files": ["requirements.txt"]})
    ok = CheckContext(extra={"imports_added": ["os", "json"]})
    assert check_constraints([change("app.py")], [c], ok) == []
    bad = CheckContext(extra={"manifest_added": {"requirements.txt": ["requests"]}})
    v = check_constraints([change("requirements.txt")], [c], bad)
    assert len(v) == 1 and "requests" in v[0].detail
    third = CheckContext(extra={"imports_added": ["numpy"]})
    assert len(check_constraints([change("app.py")], [c], third)) == 1


def test_dependency_frozen_without_evidence_is_unsupported():
    c = sc("c6", "dependency_frozen", {"kind": "manifest", "files": ["requirements.txt"]})
    ctx = CheckContext()
    assert check_constraints([change("app.py")], [c], ctx) == []
    assert ctx.unsupported == ["c6"]


def test_test_preserved_pass_and_violate():
    c = sc("c7", "test_preserved",
           {"kind": "tests", "selectors": ["tests/test_x.py::test_y"]})
    assert check_constraints([change("a.py")], [c],
                             CheckContext(test_results={"tests/test_x.py::test_y": True})) == []
    v = check_constraints([change("a.py")], [c],
                          CheckContext(test_results={"tests/test_x.py::test_y": False}))
    assert len(v) == 1 and v[0].constraint_id == "c7"


def test_size_limit_pass_and_violate():
    c = sc("c8", "size_limit", {"kind": "diff", "max_added": 10})
    assert check_constraints([change("a.py", added=5)], [c], CheckContext()) == []
    v = check_constraints([change("a.py", added=50)], [c], CheckContext())
    assert len(v) == 1 and v[0].lines == 50


# ── 2. 不可验证约束 → 明确拒绝 + 建议 ──────────────────────────

@pytest.mark.parametrize("text", [
    "代码要优雅一些",
    "尽量少改",
    "不要破坏现有功能",
    "保持代码风格一致",
])
def test_unverifiable_constraint_rejected_with_suggestion(text):
    r = compile_constraints([text])
    assert r.accepted == [], f"{text} 不应被接受"
    assert len(r.rejected) == 1
    rej = r.rejected[0]
    assert rej.reason, "拒绝必须给出原因"
    assert rej.suggestion, "拒绝必须给出改写建议（§3.3）"
    assert rej.raw_text == text


def test_unverifiable_is_not_silently_dropped_in_report():
    r = compile_constraints(["只允许修改 src/parser/**", "代码要优雅"])
    assert len(r.accepted) == 1 and len(r.rejected) == 1
    report = r.report()
    assert "✗ 约束不可验证" in report and "建议" in report


def test_rejected_reasons_are_specific_not_generic():
    r = compile_constraints(["代码要优雅"])
    assert "优雅" in r.rejected[0].raw_text
    assert "谓词" in r.rejected[0].reason


def test_llm_can_be_injected_and_is_not_required():
    """编译可注入 LLM（测试用 fake），验证路径与 LLM 无关。"""
    calls = []

    def fake_llm(prompt: str) -> str:
        calls.append(prompt)
        return ('[{"raw_text":"only edit src/parser","type":"file_scope",'
                '"scope":{"kind":"path_glob","patterns":["src/parser/**"]}}]')

    r = compile_constraints(["only edit src/parser"], llm=fake_llm)
    assert calls and r.used_llm
    assert [c.type for c in r.accepted] == ["file_scope"]


def test_llm_returning_unknown_type_is_still_rejected():
    """LLM 幻觉一个类型也不能绕过白名单 —— 边界不由 LLM 决定。"""
    def evil_llm(prompt: str) -> str:
        return '[{"raw_text":"x","type":"vibes_good","scope":{"kind":"x"}}]'

    r = compile_constraints(["x"], llm=evil_llm)
    assert r.accepted == []
    assert r.rejected and "vibes_good" in r.rejected[0].reason


def test_llm_failure_degrades_to_heuristics():
    def boom(prompt: str) -> str:
        raise RuntimeError("network down")

    r = compile_constraints(["只允许修改 src/parser/**"], llm=boom)
    assert [c.type for c in r.accepted] == ["file_scope"]
    assert any("降级" in n for n in r.notes)


def test_conflict_detection_disjoint_allowlists():
    a = sc("c1", "file_scope", {"kind": "path_glob", "patterns": ["src/a/**"]})
    b = sc("c2", "file_scope", {"kind": "path_glob", "patterns": ["src/b/**"]})
    conflicts = detect_conflicts([a, b])
    assert conflicts and conflicts[0]["kind"] == "mutually_exclusive"


def test_validate_rejects_bad_phase_and_scope():
    bad_phase = sc("c", "file_scope", {"kind": "path_glob", "patterns": ["x/**"]})
    bad_phase.phase = "whenever"
    assert not validate(bad_phase).ok
    missing = sc("c", "file_scope", {"kind": "path_glob"})
    assert not validate(missing).ok


# ── 3. 验证路径与 LLM 无关（结构性保证，§4.1）──────────────────

VERIFY_MODULES = ["checker.py", "checks.py", "graph_checks.py", "spec.py",
                  "workspace.py", "intent.py", "rollback.py", "feedback.py"]
_LLM_MARKERS = ("llm", "openai", "anthropic", "completion", "gpt", "claude")


def test_verification_modules_never_import_an_llm_client():
    """§4.1 是**结构性**要求：验证每轮跑，必须确定性。

    检查两层：
      1. 源码里没有任何 LLM 客户端标识符（注释提及 LLM 不算 —— 用
         实际的 import/调用形态匹配）
      2. 模块的命名空间里没有引入任何 LLM 客户端对象
    """
    import importlib
    guard_dir = ROOT / "backend" / "necessity" / "guard"
    for fname in VERIFY_MODULES:
        src = (guard_dir / fname).read_text(encoding="utf-8")
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""'):
                continue          # 注释/文档串里说明「不用 LLM」是合法的
            low = stripped.lower()
            for marker in _LLM_MARKERS:
                assert f"import {marker}" not in low, f"{fname}: {stripped}"
                assert f"{marker}client" not in low.replace("llmclient", ""), \
                    f"{fname} 引入了 LLM 客户端: {stripped}"
    # compiler（唯一允许用 LLM 的模块）不得被验证路径间接拉进命名空间
    checker = importlib.import_module("backend.necessity.guard.checker")
    assert not hasattr(checker, "LLMClient")
    checks = importlib.import_module("backend.necessity.guard.checks")
    assert not hasattr(checks, "LLMClient")
    graph = importlib.import_module("backend.necessity.guard.graph_checks")
    assert not hasattr(graph, "LLMClient")


def test_checker_does_not_import_compiler_at_module_level():
    """checker 若顶层 import compiler，就会把 LLM 依赖带进验证路径。"""
    src = (ROOT / "backend" / "necessity" / "guard" / "checker.py").read_text(encoding="utf-8")
    top_level = [ln for ln in src.splitlines()
                 if ln.startswith(("import ", "from "))]
    assert not any("compiler" in ln for ln in top_level), top_level
