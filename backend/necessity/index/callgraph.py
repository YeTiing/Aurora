"""callgraph.py —— callHierarchy → 图上的边。

依赖的实测结构（probe/06，**已用真实 pyright 输出核验，不是按文档假设**）：

    incomingCalls(item) 返回：
        [
          {
            "from": {                     # 调用方的 CallHierarchyItem
              "name": "apply_patch_handler",
              "kind": 12,
              "uri":  "file:///d%3A/codex_Projects/Aurora/backend/tools/apply_patch.py",
              "range": {...}, "selectionRange": {...}, "detail": "..."
            },
            "fromRanges": [ {"start": {"line":723,"character":24}, ...} ]
          },
          ...
        ]

要点：
  - **有 `fromRanges`** —— 它给出调用发生的具体位置。一条 `from` 可能带多个
    fromRanges（同一函数里调了多次），所以边数与 fromRanges 数对应，不是
    与 from 数对应。
  - `from` 里有 `name` / `kind` / `uri` / `selectionRange`，足以直接生成
    src_id（`{relpath}::{限定名}`）—— 不需要再回查符号表。
  - 实测：`safe_resolve_path` 有 10 个 incoming，来自 8 个文件（含 4 个测试
    文件）。**跨文件占比很高**，这正是本项目的价值所在。

设计决策：本模块**只做结构到边的映射**，不做可达性/影响面计算（那是 impact.py）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .symbols import make_symbol_id, uri_to_relpath

# 边类型（INDEX.md §4：calls | references | inherits）
EDGE_CALLS = "calls"
EDGE_REFERENCES = "references"
EDGE_INHERITS = "inherits"


@dataclass
class Edge:
    """一条边。src 调用/引用 dst。"""
    src_id: str
    dst_id: str
    kind: str = EDGE_CALLS
    file: str = ""
    line: int = 0

    def to_dict(self) -> dict:
        return {
            "src_id": self.src_id, "dst_id": self.dst_id,
            "kind": self.kind, "file": self.file, "line": self.line,
        }


@dataclass
class CallGraphStats:
    """采集统计 —— 用于判断图是否可信。"""
    items: int = 0            # incomingCalls 返回的条目数
    edges: int = 0            # 生成的边数（= fromRanges 总数）
    unmapped_from: int = 0    # from 无法映射到符号的条数（置信度损失）
    self_loops: int = 0       # 自环（includeDeclaration 没关掉时会出现）
    external: int = 0         # 来自 workspace 外的调用方
    files: set[str] = field(default_factory=set)


def _qualified_name_from_item(item: dict, relpath: str) -> str:
    """从 CallHierarchyItem 推导限定名。

    实测：`from.name` 给的是**裸名**（`apply_patch_handler` / `chat_stream`），
    不含类名。所以类方法的限定名需要靠符号表补全 —— 本函数先给出裸名，
    由调用方（build 阶段）用符号表在 (file, name) 上消歧。
    """
    return str(item.get("name") or "").strip()


def edges_from_incoming(
    incoming: list[dict],
    workspace: str,
    dst_symbol_id: str,
    symbol_index: dict[tuple[str, str], str] | None = None,
) -> tuple[list[Edge], CallGraphStats]:
    """把一次 incomingCalls 的返回转成边。

    symbol_index: {(relpath, bare_name) -> symbol_id}，用于把 from 的裸名补成
                  带类名的限定名。缺失时退回裸名（会降低精确度但不会崩）。

    为什么需要它：实测 `from.name` 是裸名，而 `BaseProvider.chat_stream` 与
    `SomeOther.chat_stream` 的裸名都是 `chat_stream` —— 不补全就会把两个不同
    符号混成一个（同名符号混淆正是本项目要解决的场景，不能自己制造它）。
    """
    edges: list[Edge] = []
    stats = CallGraphStats()

    for entry in incoming or []:
        frm = entry.get("from") or {}
        if not frm:
            continue
        stats.items += 1

        uri = str(frm.get("uri") or "")
        if not uri:
            stats.unmapped_from += 1
            continue

        relpath = uri_to_relpath(uri, workspace)
        if relpath.startswith("__external__/"):
            # 丢弃而不是保留：实测 outgoingCalls 的 7 条**全部**指向 pyright
            # 内置的 typeshed-fallback/stdlib/*.pyi。保留它们会让图被标准库
            # 节点淹没，「谁调用了我」的答案里混进 os/pathlib 等无关项。
            stats.external += 1
            continue

        bare = _qualified_name_from_item(frm, relpath)
        if not bare:
            stats.unmapped_from += 1
            continue

        # 用符号表补全限定名（方法需要 Class.method）
        src_qname = bare
        if symbol_index is not None:
            src_qname = symbol_index.get((relpath, bare), bare)

        src_id = make_symbol_id(relpath, src_qname, workspace)

        ranges = entry.get("fromRanges") or []
        if not ranges:
            # 无 fromRanges 时仍建一条边（宁可少信息，也不丢关系）
            ranges = [{}]

        for r in ranges:
            start = (r or {}).get("start") or {}
            line = int(start.get("line", 0))
            if src_id == dst_symbol_id:
                stats.self_loops += 1
                # 自环直接丢弃：它只可能来自 includeDeclaration=True 的误配，
                # 对「谁调用了我」这个问题毫无信息量，留着会污染影响面计算
                continue
            edges.append(Edge(
                src_id=src_id, dst_id=dst_symbol_id, kind=EDGE_CALLS,
                file=relpath, line=line,
            ))
            stats.edges += 1
            stats.files.add(relpath)

    return edges, stats


def edges_from_references(
    refs: list[dict],
    workspace: str,
    dst_symbol_id: str,
    symbol_index: dict[tuple[str, str], str] | None = None,
) -> tuple[list[Edge], CallGraphStats]:
    """把 textDocument/references 的返回转成 references 边。

    与 calls 的区别：references 是**文本引用**（含 import、类型注解、字符串
    引用等），calls 是**调用**。两者互补：
      - 找「改签名会炸谁」→ references 更全（import 也要改）
      - 找「运行时谁会真的执行到」→ calls 更准

    实测结构：`[{"uri": ..., "range": {...}}, ...]` —— 比 incomingCalls 扁平。
    """
    edges: list[Edge] = []
    stats = CallGraphStats()

    for ref in refs or []:
        uri = str((ref or {}).get("uri") or "")
        if not uri:
            stats.unmapped_from += 1
            continue
        relpath = uri_to_relpath(uri, workspace)
        if relpath.startswith("__external__/"):
            # 丢弃而不是保留：实测 outgoingCalls 的 7 条**全部**指向 pyright
            # 内置的 typeshed-fallback/stdlib/*.pyi。保留它们会让图被标准库
            # 节点淹没，「谁调用了我」的答案里混进 os/pathlib 等无关项。
            stats.external += 1
            continue

        start = ((ref.get("range") or {}).get("start") or {})
        line = int(start.get("line", 0))

        # references 只给位置，不给符号名 —— 需要靠 (file, line) 反查符号表。
        # 查不到时用占位 id，保留「某处引用了它」这个事实，但标记为未解析。
        src_id = ""
        if symbol_index is not None:
            src_id = symbol_index.get((relpath, f"@line{line}"), "")
        if not src_id:
            src_id = make_symbol_id(relpath, f"@line{line}", workspace)
            stats.unmapped_from += 1

        if src_id == dst_symbol_id:
            stats.self_loops += 1
            continue

        edges.append(Edge(
            src_id=src_id, dst_id=dst_symbol_id, kind=EDGE_REFERENCES,
            file=relpath, line=line,
        ))
        stats.edges += 1
        stats.files.add(relpath)

    return edges, stats


def build_symbol_index(symbols: list) -> dict[tuple[str, str], str]:
    """构造 {(relpath, bare_name) -> qualified_name}，供 from 裸名补全。

    同名冲突处理：**保留第一个并跳过后续**。理由：这里只用于把 callHierarchy
    的裸名映射回符号，映射错会产生假的调用关系 —— 而本项目的主指标就建立在
    「同名符号不能混」之上（INDEX.md Phase 3 的 B 类任务）。宁可漏，不可错：
    漏了只是少一条边（可在 P/R 里体现），错了会把影响面算到无关文件上。

    需要更精确的解析应走 (file, selectionRange) 精确匹配，那是 build 阶段的
    职责；本函数是廉价的兜底。
    """
    index: dict[tuple[str, str], str] = {}
    ambiguous: set[tuple[str, str]] = set()
    for s in symbols:
        key = (s.file, s.name)
        if key in index:
            ambiguous.add(key)
            continue
        # ⚠️ 存**限定名**，不是 id。
        # 调用方拿到它之后要交给 make_symbol_id(relpath, qname, workspace) 去
        # 拼 id；若这里存 id，就会拼成 id 套 id（曾真实发生：
        # `repo::a.py::repo::a.py::f`），导致所有 src_id 都悬空。
        index[key] = s.qualified_name
    # 有歧义的键整个移除 —— 宁可映射不到（退回裸名）也不映射错
    for key in ambiguous:
        index.pop(key, None)
    return index
