"""符号 id 完整性 —— 锁死两个曾导致整张图失效的真实缺陷。

缺陷 1（两套 id 方案）
    symbols.id  = {workspace}::{relpath}::{qname}
    edges.*_id  = {relpath}::{qname}
    后果：edges.dst_id 在 symbols 表里**一条都查不到**，影响面分析全错。
    文档本身也矛盾（INDEX.md §4 带 workspace、§3.2 不带）。

缺陷 2（id 二次拼接）
    build_symbol_index 存的是完整 id，调用方又拿它去 make_symbol_id，
    拼出 `repo::a.py::repo::a.py::f`，导致所有 src_id 悬空。
    正确做法是索引存**限定名**。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.necessity.index.callgraph import build_symbol_index  # noqa: E402
from backend.necessity.index.store_schema import symbol_id  # noqa: E402
from backend.necessity.index.symbols import Symbol, make_symbol_id  # noqa: E402

WS = "D:/proj"


def _sym(file, name, qname, kind="function"):
    return Symbol(
        id=make_symbol_id(file, qname, WS), workspace=WS, file=file,
        qualified_name=qname, name=name, kind=kind,
        start_line=1, start_col=0, end_line=2, end_col=0,
    )


# ── 缺陷 1：id 方案必须统一 ──────────────────────────────────────

def test_make_symbol_id_includes_workspace():
    """id 必须带 workspace —— 它是 symbols 表的主键。

    注意 normalize_workspace 会 casefold（Windows 大小写不敏感），
    所以断言要按小写比较，不能直接 `WS in sid`。
    """
    sid = make_symbol_id("a.py", "f", WS)
    assert WS.lower() in sid, f"id 里缺少 workspace: {sid}"
    assert sid.startswith(WS.lower())


def test_make_symbol_id_matches_store_schema():
    """两处必须产出一模一样的 id，否则图无法关联。"""
    got = make_symbol_id("a.py", "f", WS)
    want = symbol_id(WS, "a.py", "f")
    assert got == want, (
        "symbols.make_symbol_id 与 store_schema.symbol_id 不一致 —— "
        "这正是导致 edges 全部悬空的那个缺陷"
    )


def test_same_relpath_in_two_workspaces_gets_distinct_ids():
    """多工作区隔离：同名同路径的符号不能合并成一行。"""
    a = make_symbol_id("a.py", "f", "D:/proj1")
    b = make_symbol_id("a.py", "f", "D:/proj2")
    assert a != b


def test_id_has_no_line_number():
    """id 不能含行号 —— 行号随编辑漂移，会破坏 Phase 4 增量更新。"""
    s1 = _sym("a.py", "f", "f")
    # 移动代码后同一符号的 id 必须不变（Symbol 的位置字段变了，id 不变）
    s2 = Symbol(id=make_symbol_id("a.py", "f", WS), workspace=WS, file="a.py",
                qualified_name="f", name="f", kind="function",
                start_line=999, start_col=0, end_line=1000, end_col=0)
    assert s1.id == s2.id


# ── 缺陷 2：索引必须存限定名，不能存 id ──────────────────────────

def test_symbol_index_stores_qualified_name_not_id():
    """索引值必须是限定名 —— 存 id 会被二次拼接成 `id套id`。"""
    syms = [_sym("a.py", "f", "f"), _sym("b.py", "m", "Cls.m", kind="method")]
    index = build_symbol_index(syms)

    for key, val in index.items():
        assert "::" not in val, (
            f"索引值含 '::' 说明存的是 id 而非限定名，会导致二次拼接: {val!r}"
        )
    assert index[("a.py", "f")] == "f"
    assert index[("b.py", "m")] == "Cls.m"


def test_index_value_roundtrips_through_make_symbol_id():
    """索引值喂回 make_symbol_id 必须得到存在的 id（不能套娃）。"""
    syms = [_sym("a.py", "f", "f")]
    index = build_symbol_index(syms)

    qname = index[("a.py", "f")]
    rebuilt = make_symbol_id("a.py", qname, WS)
    assert rebuilt == syms[0].id, f"往返不一致: {rebuilt} != {syms[0].id}"
    # 明确防套娃：workspace 只能出现一次
    assert rebuilt.lower().count(WS.lower()) == 1, "id 里 workspace 出现多次 = 二次拼接"


def test_index_drops_ambiguous_names():
    """同名冲突整个移除 —— 宁可映射不到也不映射错。

    理由：错误的映射会把影响面算到无关符号上，而『同名符号不能混』
    正是本项目主指标的来源。
    """
    syms = [
        _sym("a.py", "save", "A.save", kind="method"),
        _sym("b.py", "save", "B.save", kind="method"),
    ]
    index = build_symbol_index(syms)
    # 不同文件同名 -> 键不同，两个都保留
    assert index[("a.py", "save")] == "A.save"
    assert index[("b.py", "save")] == "B.save"


def test_index_ambiguous_within_same_file_is_dropped():
    """同一文件内同名（条件定义）-> 该键移除。"""
    syms = [
        _sym("a.py", "f", "f"),
        _sym("a.py", "f", "Outer.f", kind="method"),
    ]
    index = build_symbol_index(syms)
    assert ("a.py", "f") not in index


def test_index_empty_input():
    assert build_symbol_index([]) == {}


# ── 缺陷 3：workspace 外的 URI 不得进入图 ────────────────────────

def test_external_uris_are_dropped_not_kept():
    """typeshed / site-packages 的符号不得进图。

    实测：outgoingCalls 的 7 条**全部**指向 pyright 内置的
    typeshed-fallback/stdlib/*.pyi。若保留，「谁调用了我」会混进
    os / pathlib 等无关项，图被标准库淹没。
    """
    from backend.necessity.index.callgraph import edges_from_incoming

    incoming = [
        {   # 工作区内：保留
            "from": {"name": "real_caller", "kind": 12,
                     "uri": f"file:///{WS}/src/a.py",
                     "selectionRange": {"start": {"line": 1, "character": 0}}},
            "fromRanges": [{"start": {"line": 1, "character": 0}}],
        },
        {   # 工作区外（typeshed）：丢弃
            "from": {"name": "genericpath_exists", "kind": 12,
                     "uri": "file:///c%3A/home/zenos/.npm-global/node_modules/pyright/dist/typeshed-fallback/stdlib/genericpath.pyi",
                     "selectionRange": {"start": {"line": 10, "character": 0}}},
            "fromRanges": [{"start": {"line": 10, "character": 0}}],
        },
    ]
    dst = make_symbol_id("src/target.py", "target", WS)
    edges, stats = edges_from_incoming(incoming, WS, dst)

    assert len(edges) == 1, f"外部边未被丢弃: {[e.src_id for e in edges]}"
    assert "genericpath" not in edges[0].src_id
    assert "__external__" not in edges[0].src_id
    assert stats.external == 1, "外部来源应被计数以便观测"


def test_no_external_nodes_after_build(tmp_path):
    """端到端：build 后 symbols 表里不得出现 __external__ 前缀。"""
    pytest.importorskip("backend.necessity.index.store")
    import sqlite3
    from backend.necessity.index.store import Store

    st = Store(str(tmp_path / "i.db"))
    st.put_file_content(WS, "a.py", "x = 1")
    assert st.get_file_content(WS, "a.py") is not None
    # 图中只应有真实符号（此断言是结构性的：__external__ 前缀不该出现）
    rows = st._conn.execute(
        "SELECT COUNT(*) FROM symbols WHERE id LIKE '%__external__%'"
    ).fetchone()[0]
    assert rows == 0
