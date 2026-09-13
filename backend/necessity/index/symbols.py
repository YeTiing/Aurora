"""symbols.py —— documentSymbol → 符号表。

采点规则（Phase 0 实测确认，不是推测）：

    必须用 `selectionRange.start`，**不是** `range.start`。

    实测数据（probe/04a vs 04b，目标 safe_resolve_path）：
        range.start         -> (45, 0)  -> references 返回 **0** 条
        selectionRange.start-> (45, 4)  -> references 返回 **25** 条

    类方法上差距更明显（probe/08，目标 BaseProvider.chat_stream）：
        range.start          = (206, 4)   <- 指向 def 行的缩进
        selectionRange.start = (207, 14)  <- 指向方法名

    采错点不会报错，只会**静默返回空列表** —— 这是本项目最隐蔽的陷阱。

symbol id 设计（INDEX.md §3.2，必须现在定，Phase 4 增量更新依赖它）：

    id = {相对路径}::{限定名}      例：backend/tools/base.py::safe_resolve_path

    为什么不用行号：行号随编辑漂移，增量更新时 id 不稳定。
    为什么用限定名：重命名后旧符号自然消失 —— 这是**正确行为**
    （重命名 = 旧符号没了 + 新符号出现），其余情况稳定。
    位置信息作为属性存储，不参与主键。
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

# LSP SymbolKind → 我们图里用的三类（INDEX.md §4 只保留 function|method|class）
#
# ⚠️ 这里踩过一次坑，记录实测事实（probe/03，backend/tools/base.py）：
#   pyright 把**类方法**报成 kind=6 (Method)，把**模块级函数**报成 kind=12 (Function)。
#   初版只映射了 12，导致 ToolRegistry 的 15 个方法**全部被过滤掉**，
#   整个类看起来没有方法 —— 而且不报错，只是静默少解析。
#   所以 6 和 12 都必须映射，且靠 `kind=6` 直接判定为 method，
#   不依赖父节点类型（模块级函数在类外，方法在类内，两者本就互斥）。
LSP_KIND_METHOD = 6
LSP_KIND_FUNCTION = 12
LSP_KIND_CLASS = 5
LSP_KIND_VARIABLE = 13
LSP_KIND_CONSTANT = 14
LSP_KIND_INTERFACE = 11
LSP_KIND_MODULE = 2

# 第一版只建这三类节点（INDEX.md §3.2：lambda/匿名函数不建节点，无法稳定命名）
KIND_FUNCTION = "function"
KIND_METHOD = "method"
KIND_CLASS = "class"

_KIND_MAP = {
    LSP_KIND_CLASS: KIND_CLASS,
    LSP_KIND_INTERFACE: KIND_CLASS,
    LSP_KIND_METHOD: KIND_METHOD,      # ← 实测：类方法由 pyright 报为 6
    LSP_KIND_FUNCTION: KIND_FUNCTION,
    LSP_KIND_VARIABLE: None,    # 不在第一版范围
    LSP_KIND_CONSTANT: None,
    LSP_KIND_MODULE: None,
}


@dataclass
class Symbol:
    """一个符号。id 是稳定主键；位置是属性。"""
    id: str
    workspace: str
    file: str                 # 相对路径（POSIX 风格，统一分隔符）
    qualified_name: str
    name: str
    kind: str
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    signature: str = ""
    content_hash: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id, "workspace": self.workspace, "file": self.file,
            "qualified_name": self.qualified_name, "name": self.name,
            "kind": self.kind, "start_line": self.start_line,
            "start_col": self.start_col, "end_line": self.end_line,
            "end_col": self.end_col, "signature": self.signature,
            "content_hash": self.content_hash,
        }


def uri_to_relpath(uri: str, workspace: str) -> str:
    """`file:///d%3A/.../a.py` → workspace 下的相对 POSIX 路径。

    URI 里的 drive letter 是百分号编码的（`d%3A`），且大小写可能与真实
    路径不一致（Windows）。用 resolve() 对齐后再求相对路径，避免
    `D:\\...` 与 `d:\\...` 被当成两个文件。
    """
    raw = urlparse(uri)
    path = unquote(raw.path)
    # `/d:/x/y` → `d:/x/y`
    if re.match(r"^/[A-Za-z]:", path):
        path = path[1:]
    try:
        p = Path(path).resolve()
        ws = Path(workspace).resolve()
        rel = p.relative_to(ws)
    except (ValueError, OSError):
        # 不在 workspace 内（如标准库 / site-packages）：用文件名兜底，
        # 并标注成外部符号，避免污染图的主键空间
        p = Path(path)
        rel = Path("__external__") / p.name
    return rel.as_posix()


def make_symbol_id(relpath: str, qualified_name: str, workspace: str = "") -> str:
    """符号主键。

    ⚠️ 曾出现**两套不兼容的 id 方案**导致整张图无法关联（真实缺陷）：
        symbols.id   = {workspace}::{relpath}::{qname}   （store 侧）
        edges.*_id   = {relpath}::{qname}                （本模块初版）
    结果是 edges.dst_id 在 symbols 表里**一条都查不到**，影响面分析全错。

    文档本身也不一致：INDEX.md §4 说带 workspace，§3.2 说不带。以 §4 与
    INTEGRATION.md §6.2 为准 —— **必须带 workspace**，因为 id 是 symbols 表的
    PRIMARY KEY，不带会让两个工作区里的同名符号合并成一行，破坏多工作区隔离；
    且 delete_edges_for_file 就没有可用于 scope 的前缀。

    实现上直接复用 store_schema.symbol_id，避免两处各自实现再次漂移。
    """
    from .store_schema import symbol_id as _sid
    return _sid(workspace, relpath, qualified_name) if workspace else f"{relpath}::{qualified_name}"


def file_content_hash(text: str) -> str:
    """内容哈希 —— 符号有效性的判据（INTEGRATION.md §6.3）。

    改文件 → 哈希变 → 旧符号查不到 → 自动失效，不会出现索引与内容不匹配。
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _pick_position(node: dict) -> tuple[int, int, int, int, int, int]:
    """从 documentSymbol 节点提取采点位置与范围。

    返回 (sel_line, sel_col, end_line, end_col, range_start_line, range_start_col)。

    ⚠️ 采点用 selectionRange，范围用 range —— 两者用途不同，混用会出事：
       selectionRange 指向符号名（用于 references/callHierarchy 查询）
       range 覆盖整个定义（用于判断某行属于哪个符号）
    """
    sel = node.get("selectionRange") or node.get("range") or {}
    rng = node.get("range") or sel
    s = sel.get("start") or {}
    e = rng.get("end") or {}
    rs = rng.get("start") or {}
    return (
        int(s.get("line", 0)), int(s.get("character", 0)),
        int(e.get("line", 0)), int(e.get("character", 0)),
        int(rs.get("line", 0)), int(rs.get("character", 0)),
    )


def walk_document_symbols(
    nodes: list[dict],
    workspace: str,
    relpath: str,
    content_hash: str,
    source_lines: list[str] | None = None,
) -> list[Symbol]:
    """递归遍历 documentSymbol 结果，产出符号表。

    限定名规则（INDEX.md §3.2 的边界情况）：
      - 模块级函数 → `func`
      - 类方法     → `Class.method`（注意：documentSymbol 里方法的所在类是
                     父节点，但方法名本身**不含**类名 —— 必须自己拼）
      - 嵌套函数   → `outer.inner`
      - 重名       → 追加位置后缀消歧
    """
    out: list[Symbol] = []
    seen_ids: dict[str, int] = {}

    def visit(node: dict, parent: str, parent_kind: str) -> None:
        name = str(node.get("name") or "").strip()
        lsp_kind = int(node.get("kind") or 0)
        kind = _KIND_MAP.get(lsp_kind, None)
        children = node.get("children") or []

        if not name:
            # 匿名（如 lambda）：第一版不建节点，但继续下探子节点
            for c in children:
                visit(c, parent, parent_kind)
            return

        if kind is None:
            # 不在第一版范围的符号类型：不建节点，但要继续下探（类里的方法
            # 可能挂在被过滤的节点下）
            for c in children:
                visit(c, parent, parent_kind)
            return

        # 限定名：方法用 `Class.method`，函数用 `outer.inner`
        qualified = f"{parent}.{name}" if parent else name

        sel_line, sel_col, end_line, end_col, rng_line, rng_col = _pick_position(node)

        sid = make_symbol_id(relpath, qualified, workspace)
        # 同名消歧：Python 里少见但存在（如条件定义的重复函数）
        if sid in seen_ids:
            seen_ids[sid] += 1
            sid = f"{sid}#{seen_ids[sid]}"
        else:
            seen_ids[sid] = 0

        sig = _extract_signature(source_lines, rng_line) if source_lines else ""

        out.append(Symbol(
            id=sid, workspace=workspace, file=relpath,
            qualified_name=qualified, name=name,
            # kind 直接来自 LSP（6=method / 12=function），不再用父节点类型推断 ——
            # pyright 已经准确区分了，推断反而会误判（模块级函数无父节点）。
            kind=kind,
            start_line=sel_line, start_col=sel_col,
            end_line=end_line, end_col=end_col,
            signature=sig, content_hash=content_hash,
        ))

        for c in children:
            visit(c, qualified, kind)

    for n in nodes or []:
        visit(n, "", "")
    return out


def _extract_signature(source_lines: list[str], start_line: int) -> str:
    """从源码取函数/类定义那一行的签名。

    仅在定义行本身取（不跨行拼接）—— 保证便宜且可预测；跨行签名第一版
    不处理（`INDEX.md` 明确 scope 控制）。
    """
    if not source_lines or start_line < 0 or start_line >= len(source_lines):
        return ""
    line = source_lines[start_line].strip()
    return line[:300]


def extract_file_symbols(
    doc_symbols: list[dict],
    workspace: str,
    relpath: str,
    content: str,
) -> list[Symbol]:
    """从一次 documentSymbol 的返回提取整个文件的符号表（含哈希）。"""
    return walk_document_symbols(
        doc_symbols, workspace, relpath,
        file_content_hash(content), content.splitlines(),
    )
