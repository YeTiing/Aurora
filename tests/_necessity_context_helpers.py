"""Context Paging 测试的共享夹具。

单独一个文件是为了让两个测试模块都控制在 300 行内（项目文件预算），
同时复用同一份「已读过大文件」的状态，避免两处各自构造导致漂移。
"""
from __future__ import annotations

import os

import pytest

from backend.necessity.context import ContextPaging
from backend.necessity.index.store import Store
from backend.necessity.index.symbols import file_content_hash

WS_REL = "ws"
BIG = "src/big.py"
BIG_CONTENT = "\n".join(
    ["def parse(x):", "    return x + 1", ""]
    + [pair for i in range(60) for pair in (f"def helper_{i}(a, b):", f"    return a * {i}", "")]
)


def content_hash(text: str) -> str:
    """必须与生产代码同源 —— 第二套哈希会静默破坏所有有效性判定。"""
    return file_content_hash(text)


def register_symbols(store, workspace, path, content):
    """把符号写进唯一真源（symbols 表），按 content_hash 关联。"""
    lines = content.splitlines()
    syms = [
        {"qualified_name": "parse", "name": "parse", "kind": "function",
         "start_line": 0, "end_line": 1, "signature": lines[0]},
        {"qualified_name": "helper_0", "name": "helper_0", "kind": "function",
         "start_line": 3, "end_line": 4, "signature": lines[3]},
    ]
    store.upsert_symbols(workspace, path, syms, content_hash=content_hash(content))


def store_row(store, workspace, path):
    """file_content 的 (content, hash) 快照，用于断言压缩没碰它。"""
    row = store.get_file_content(workspace, path)
    return None if row is None else (row["content"], row["content_hash"])


@pytest.fixture()
def store(tmp_path):
    st = Store(str(tmp_path / "idx.db"))
    yield st
    st.close()


def write_workspace_file(workspace: str, relpath: str, content: str) -> str:
    """在 workspace 下真实落一个文件，并返回它的 mtime。

    测试必须打真实磁盘：失效判定包含一次 stat（CONTEXT_PAGING.md §6.4），
    只用内存假路径会让「外部改动」这条路径根本走不到。
    """
    abs_path = os.path.join(workspace, relpath)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)
    return os.stat(abs_path).st_mtime


@pytest.fixture()
def cp(tmp_path, store):
    """已读过大文件的 ContextPaging（状态 = fresh，索引可用）。

    文件真实存在于磁盘，mtime 取真实值 —— 否则 `read_file` 的
    `_disk_unchanged` 会（正确地）判定缓存与磁盘不一致而退回宿主。
    """
    ws = str(tmp_path / "ws")
    mtime = write_workspace_file(ws, BIG, BIG_CONTENT)
    hook = ContextPaging({
        "workspace": ws, "session_id": "s1", "store": store,
        "min_lines_for_index": 10,
    })
    hook.table.note_read(BIG, BIG_CONTENT, mtime=mtime)
    register_symbols(store, ws, BIG, BIG_CONTENT)
    return hook
