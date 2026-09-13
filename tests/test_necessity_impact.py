"""影响面测试 —— 用真实 probe 数据构造图，验证「会炸谁」的答案。

重点：
  - 分层正确（distance 1 是直接调用方）
  - 测试文件被识别
  - 风险判定依据「调用方数量 + 有无测试」
  - 降级不抛异常（INTEGRATION.md §8.2：任何降级都不得让 Agent 无法工作）
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.necessity.index.callgraph import edges_from_incoming  # noqa: E402
from backend.necessity.index.impact import (  # noqa: E402
    RISK_HIGH,
    RISK_LOW,
    RISK_MEDIUM,
    analyze,
    compute_risk,
    is_test_file,
    is_test_symbol,
    symbol_test_coverage_hint,
)

PROBE06 = ROOT / "probe" / "06_incoming_calls.json"
WS = "D:/codex_Projects/Aurora"


class FakeStore:
    """内存图 —— 让影响面逻辑可以脱离 SQLite 与 LSP 单独被测。"""

    def __init__(self, edges):
        self.edges = edges

    def callers_of(self, node_id):
        return [e for e in self.edges if e["dst_id"] == node_id]

    def callees_of(self, node_id):
        return [e for e in self.edges if e["src_id"] == node_id]


@pytest.fixture(scope="module")
def real_edges():
    """用 probe/06 的真实 pyright 输出构造边。

    返回 (edges, dst_id) —— dst_id 随 id 方案变化，不能硬编码字符串。
    """
    if not PROBE06.exists():
        pytest.skip("probe/06 不存在（Phase 0 未完成）")
    from backend.necessity.index.symbols import make_symbol_id
    d = json.loads(PROBE06.read_text(encoding="utf-8"))
    # dst 必须用与生产一致的 id 方案（带 workspace）
    dst = make_symbol_id("backend/tools/base.py", "safe_resolve_path", WS)
    edges, _ = edges_from_incoming(d["raw_result"], WS, dst)
    return [e.to_dict() for e in edges], dst


# ── 测试识别 ────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "tests/test_foo.py",
    "test/unit/test_bar.py",
    "tests\\test_baz.py",
    "src/parser_test.py",
    "backend/tests/helper.py",
])
def test_is_test_file_positive(path):
    assert is_test_file(path) is True


@pytest.mark.parametrize("path", [
    "backend/tools/base.py",
    "src/testing_utils/parser.py",   # testing_ 不是测试目录
    "src/latest.py",                  # 含 'test' 但不是测试
])
def test_is_test_file_negative(path):
    assert is_test_file(path) is False


@pytest.mark.parametrize("name", ["test_foo", "TestClass"])
def test_is_test_symbol_positive(name):
    assert is_test_symbol(name) is True


@pytest.mark.parametrize("name", ["foo", "contest", "testing_helper"])
def test_is_test_symbol_negative(name):
    assert is_test_symbol(name) is False


# ── 风险判定 ────────────────────────────────────────────────────

def test_risk_low_when_no_callers():
    risk, reason = compute_risk(0, False, 0)
    assert risk == RISK_LOW
    assert "无调用方" in reason


def test_risk_medium_when_tests_exist():
    """有测试 = 改坏了会被发现，风险可控。"""
    risk, reason = compute_risk(3, True, 3)
    assert risk == RISK_MEDIUM
    assert "测试覆盖" in reason


def test_risk_high_when_many_callers_and_no_tests():
    risk, reason = compute_risk(8, False, 8)
    assert risk == RISK_HIGH
    assert "无测试覆盖" in reason


def test_risk_medium_when_few_callers_no_tests():
    risk, _ = compute_risk(2, False, 2)
    assert risk == RISK_MEDIUM


# ── 端到端（真实数据）──────────────────────────────────────────

def test_real_impact_finds_callers(real_edges):
    """safe_resolve_path 实测有 10 个调用方 —— 必须全找到。"""
    edges, dst = real_edges
    imp = analyze(FakeStore(edges), dst)

    assert imp.degraded is False
    # 边数（12）> 调用方数（10）：file_rw_handler 调了 3 次 -> 3 条边、
    # 但只是 1 个调用方。调用方按符号去重后是 10，与 probe meta 一致。
    unique_callers = {e["src_id"] for e in edges}
    assert imp.counts["total_callers"] == len(unique_callers) == 10
    assert imp.counts["direct_callers"] == len(unique_callers)


def test_real_impact_identifies_tests(real_edges):
    """probe 里 6 条边来自测试文件 —— 必须被识别为 tests。"""
    edges, dst = real_edges
    imp = analyze(FakeStore(edges), dst)

    assert imp.counts["tests"] >= 4, f"应识别出测试文件，实际 {imp.tests}"
    for t in imp.tests:
        assert is_test_file(t["file"]) or is_test_symbol(t["test_name"])


def test_real_impact_risk_is_medium_because_tests_exist(real_edges):
    """有测试覆盖时风险应为 medium，不该报 high。"""
    edges, dst = real_edges
    imp = analyze(FakeStore(edges), dst)
    assert imp.risk == RISK_MEDIUM, f"有测试覆盖却报了 {imp.risk}: {imp.note}"


def test_callees_direction(real_edges):
    """反向查询：某调用方调用了什么。"""
    # 从 file_rw_handler 出发查它调用了什么 —— 反向方向
    from backend.necessity.index.symbols import make_symbol_id
    edges, target = real_edges
    caller = make_symbol_id("backend/tools/file_rw.py", "file_rw_handler", WS)
    imp = analyze(FakeStore(edges), caller)
    callee_ids = {c.symbol for c in imp.callees}
    assert target in callee_ids, (
        "file_rw_handler 调用了 safe_resolve_path，反向查询应能命中。"
        f"实际 callees={sorted(callee_ids)}"
    )


# ── 分层与防环 ──────────────────────────────────────────────────

def test_bfs_respects_depth():
    edges = [
        {"src_id": "a", "dst_id": "b", "file": "a.py", "line": 1},
        {"src_id": "b", "dst_id": "c", "file": "b.py", "line": 2},
        {"src_id": "c", "dst_id": "d", "file": "c.py", "line": 3},
    ]
    st = FakeStore(edges)

    imp1 = analyze(st, "d", depth=1)
    assert {c.symbol for c in imp1.callers} == {"c"}

    imp2 = analyze(st, "d", depth=2)
    assert {c.symbol for c in imp2.callers} == {"c", "b"}

    imp3 = analyze(st, "d", depth=3)
    assert {c.symbol for c in imp3.callers} == {"c", "b", "a"}


def test_bfs_survives_cycle():
    """有环时不能死循环 —— A→B→A。"""
    edges = [
        {"src_id": "a", "dst_id": "b", "file": "a.py", "line": 1},
        {"src_id": "b", "dst_id": "a", "file": "b.py", "line": 2},
    ]
    imp = analyze(FakeStore(edges), "a", depth=5)
    assert {c.symbol for c in imp.callers} == {"b"}


def test_distances_are_layered(real_edges):
    """distance 必须从 1 开始递增且有值 —— 它是上下文组装的排序依据。"""
    edges, dst = real_edges
    imp = analyze(FakeStore(edges), dst)
    assert all(c.distance >= 1 for c in imp.callers)


# ── 降级 ────────────────────────────────────────────────────────

def test_degraded_when_no_store():
    """无调用图时必须返回降级结果 + 说明，而不是抛异常。"""
    imp = analyze(None, "x::y")
    assert imp.degraded is True
    assert imp.callers == []
    assert imp.note, "降级必须给出说明，否则调用方无从判断"
    assert "保守" in imp.note


def test_degraded_on_store_error():
    """查询失败同样降级而非抛出。"""

    class Broken:
        def callers_of(self, i): raise RuntimeError("db locked")
        def callees_of(self, i): raise RuntimeError("db locked")

    imp = analyze(Broken(), "x::y")
    assert imp.degraded is True
    assert "db locked" in imp.note


def test_empty_symbol_id_degrades():
    imp = analyze(FakeStore([]), "")
    assert imp.degraded is True


def test_to_dict_shape_matches_doc():
    """返回结构必须匹配 INDEX.md Phase 2 定义的字段。"""
    imp = analyze(FakeStore([
        {"src_id": "a::f", "dst_id": "b::g", "file": "a.py", "line": 3},
    ]), "b::g")
    d = imp.to_dict()
    for key in ("callers", "callees", "tests", "risk", "symbol"):
        assert key in d, f"缺少文档定义的字段 {key}"
    assert set(d["callers"][0]) == {"file", "line", "symbol", "distance"}


# ── 供 Diff Reducer 使用 ────────────────────────────────────────

def test_test_coverage_hint_for_reducer(real_edges):
    """Diff Reducer 用它选测试范围（DIFF_REDUCER.md §6.1）。"""
    edges, dst = real_edges
    st = FakeStore(edges)
    hint = symbol_test_coverage_hint(st, [dst])
    assert hint[dst] is True


def test_test_coverage_hint_handles_errors():
    class Broken:
        def callers_of(self, i): raise RuntimeError("boom")

    hint = symbol_test_coverage_hint(Broken(), ["x"])
    assert hint["x"] is False, "异常时保守返回 False（不声称有覆盖）"
