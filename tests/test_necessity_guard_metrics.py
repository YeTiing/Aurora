"""Constraint Guard 测试（第三部分）—— 指标 ρ/s、降级开关、工作区扫描器。

对应任务书：on_task_end 同时上报 ρ 与 s、无 store/无约束全部放行不崩、
扫描器正确区分 added/modified/deleted 且不把 mtime 触及误判为改动。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.necessity.guard import build_guard_hooks  # noqa: E402
from backend.necessity.guard.compiler import compile_from_task  # noqa: E402
from backend.necessity.guard.workspace import WorkspaceScanner  # noqa: E402
from backend.necessity.hooks import TaskResult, ToolCall  # noqa: E402


@pytest.fixture()
def ws(tmp_path):
    (tmp_path / "src" / "parser").mkdir(parents=True)
    (tmp_path / "src" / "utils").mkdir(parents=True)
    (tmp_path / "src" / "parser" / "p.py").write_text("def parse(): pass\n")
    (tmp_path / "src" / "utils" / "u.py").write_text("def helper(): pass\n")
    return tmp_path


def make_guard(ws, constraints, **cfg):
    g = build_guard_hooks({"workspace": str(ws), "use_git": False,
                           "session_id": "s1", **cfg})
    g.on_task_start({"id": "task-1", "constraints": constraints})
    return g


# ── 5. 指标 ρ / s ───────────────────────────────────────────────

def test_on_task_end_reports_rho_and_s(ws):
    g = make_guard(ws, ["只允许修改 src/parser/**"], default_action="warn")
    (ws / "src" / "utils" / "u.py").write_text("violate at turn 2\n")
    g.on_turn_end(2)
    rep = g.on_task_end(TaskResult("task-1", True, turns=10))
    assert rep["first_violation_turn"] == 2
    assert rep["survival_turns"] == 2          # s：绝对值
    assert rep["retention"] == pytest.approx(0.2)   # ρ = min(2,10)/10
    assert rep["turns"] == 10


def test_rho_is_one_when_never_violated(ws):
    g = make_guard(ws, ["只允许修改 src/parser/**"], default_action="warn")
    (ws / "src" / "parser" / "p.py").write_text("in scope\n")
    g.on_turn_end(1)
    rep = g.on_task_end(TaskResult("task-1", True, turns=4))
    assert rep["first_violation_turn"] is None
    assert rep["survival_turns"] == 5      # T + 1
    assert rep["retention"] == 1.0


def test_rho_normalizes_across_different_task_lengths(ws):
    """同样第 5 轮违反：15 轮任务与 40 轮任务的 ρ 不同 —— 这正是修正原指标的意义。"""
    def run(turns):
        g = make_guard(ws, ["只允许修改 src/parser/**"], default_action="warn")
        (ws / "src" / "utils" / "u.py").write_text(f"v{turns}\n")
        g.on_turn_end(5)
        return g.on_task_end(TaskResult("t", True, turns=turns))["retention"]
    assert run(15) == pytest.approx(5 / 15)
    assert run(40) == pytest.approx(5 / 40)


def test_task_start_reports_rejections(ws):
    g = build_guard_hooks({"workspace": str(ws), "use_git": False})
    g.on_task_start({"id": "t", "constraints": ["只允许修改 src/parser/**", "代码要优雅"]})
    rep = g.on_task_end(TaskResult("t", True, turns=1))
    assert len(rep["constraints_accepted"]) == 1
    assert len(rep["constraints_rejected"]) == 1
    assert rep["constraints_rejected"][0]["suggestion"]


# ── 6. 降级 / 开关 ──────────────────────────────────────────────

def test_no_constraints_allows_everything(ws):
    g = make_guard(ws, [], default_action="rollback")
    d = g.before_tool(ToolCall("file_write", {"path": "totally/elsewhere.py"}, 1))
    assert d.action == "allow"
    (ws / "src" / "utils" / "u.py").write_text("anything\n")
    g.on_turn_end(1)
    assert g.violations == []
    assert g.on_task_end(TaskResult("t", True, turns=1))["retention"] == 1.0


def test_disabled_guard_is_noop(ws):
    g = build_guard_hooks({"workspace": str(ws), "enabled": False})
    g.on_task_start({"id": "t", "constraints": ["只允许修改 src/parser/**"]})
    assert g.before_tool(ToolCall("file_write", {"path": "src/utils/u.py"}, 1)).action == "allow"
    assert g.on_task_end(TaskResult("t", True, turns=1)) == {}
    assert g.scan_workspace() == []


def test_no_store_does_not_crash_structural_constraints(ws):
    """无调用图 → 结构类约束进 unsupported，其余照常，绝不崩（§8.2）。"""
    g = make_guard(ws, ["只允许修改 src/parser/**",
                        "只准改 main() 可达的文件"], default_action="warn")
    assert any(c.type == "call_chain" for c in g.constraints)
    g.on_turn_end(1)
    assert g.violations == []


def test_checker_exception_allows_through(ws, monkeypatch):
    """检查器自身抛异常 → 放行 + 记录（§9 第 7 条）。"""
    import backend.necessity.guard.checks as checks

    def boom(*a, **k):
        raise RuntimeError("checker exploded")

    monkeypatch.setitem(checks.HANDLERS, "file_scope", boom)
    g = make_guard(ws, ["只允许修改 src/parser/**"], default_action="rollback")
    d = g.before_tool(ToolCall("file_write", {"path": "src/utils/u.py"}, 1))
    assert d.action == "allow"
    (ws / "src" / "utils" / "u.py").write_text("x\n")
    g.on_turn_end(1)
    assert g.rollback_actions == []


def test_workspace_missing_directory_degrades(tmp_path):
    g = build_guard_hooks({"workspace": str(tmp_path / "nope")})
    g.on_task_start({"id": "t", "constraints": ["只允许修改 src/**"]})
    assert g.before_tool(ToolCall("file_write", {"path": "src/a.py"}, 1)).action in ("allow", "block")
    g.on_turn_end(1)


def test_compile_from_task_accepts_structured_constraints(ws):
    r = compile_from_task({"id": "t", "constraints": [
        {"id": "x", "type": "file_scope",
         "scope": {"kind": "path_glob", "patterns": ["src/**"]},
         "on_violation": "rollback"}]})
    assert [c.type for c in r.accepted] == ["file_scope"]
    assert r.accepted[0].on_violation == "rollback"


def test_compile_from_task_empty_is_not_an_error():
    r = compile_from_task({"id": "t"})
    assert r.accepted == [] and r.ok and r.notes


# ── 7. 工作区扫描器 ─────────────────────────────────────────────

def test_scanner_detects_added_modified_deleted(ws):
    scanner = WorkspaceScanner(str(ws), use_git=False)
    before = scanner.scan()
    (ws / "src" / "parser" / "new.py").write_text("new\n")
    (ws / "src" / "utils" / "u.py").write_text("mod\n")
    (ws / "src" / "parser" / "p.py").unlink()
    changes, _ = scanner.diff(before, scanner.scan())
    kinds = {c.path: c.kind for c in changes}
    assert kinds["src/parser/new.py"] == "added"
    assert kinds["src/utils/u.py"] == "modified"
    assert kinds["src/parser/p.py"] == "deleted"


def test_scanner_ignores_mtime_only_touch(ws):
    import os
    scanner = WorkspaceScanner(str(ws), use_git=False)
    before = scanner.scan()
    os.utime(ws / "src" / "utils" / "u.py", (9999999999, 9999999999))
    changes, _ = scanner.diff(before, scanner.scan())
    assert changes == []


def test_scanner_bytes_are_not_a_change_when_hash_same(ws):
    scanner = WorkspaceScanner(str(ws), use_git=False)
    before = scanner.scan()
    (ws / "src" / "utils" / "u.py").write_text("def helper(): pass\n")
    changes, _ = scanner.diff(before, scanner.scan())
    assert changes == []
