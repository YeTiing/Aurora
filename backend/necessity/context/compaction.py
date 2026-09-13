"""压缩契约（CONTEXT_PAGING.md §6.5）。

两条不变量：

  不变量 1：压缩过程**不得**读写 `file_content` 表。
  不变量 2：每次压缩后必须向摘要注入当前会话的 file_state 索引。

为什么这是整个设计成立的支点：压缩器碰不到状态表，所以「压缩吞掉文件
内容 → Agent 失忆 → 重读 → 再压缩」这个结构性循环被打断。状态表在对话
之外，压缩只是丢弃对话历史里的副本，不影响它。

本模块只做两件事：注入格式化，以及一份**与压缩无关的版本快照**。
快照的存在是为了可验证：压缩前后同一会话的 file_state 必须逐字一致。
"""
from __future__ import annotations

import hashlib
from typing import Any

from ..index.store_schema import normalize_relpath
from .recall import resolve_symbols

__all__ = ["snapshot", "render_injection"]

DEFAULT_MAX_ENTRIES = 50


def snapshot(table: Any) -> str:
    """压缩前快照 —— 状态表的版本指纹。

    只读 `file_read_log`（会话级），**不读 file_content**，因此满足不变量 1。
    返回的字符串是确定性的：同样的状态表内容 → 同样的指纹。
    """
    if table is None:
        return ""
    rows = table.entries()
    parts = [f"{r.get('path')}|{r.get('validity')}|{r.get('read_count')}" for r in rows]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def render_injection(table: Any, store: Any, *, max_entries: int = DEFAULT_MAX_ENTRIES) -> str:
    """生成注入摘要的 file_state 段。

    只列 `fresh` 和 `dirty`：
      * `stale` 内容不可信，列出来会诱使 Agent 依据过期 hash 行动；
      * `unknown` 等于没记录。
    超 `max_entries` 时按 last_read_at 倒序截断并**显式注明**剩余数量
    （§6.5 边界：不许静默丢弃）。
    """
    if table is None:
        return ""
    rows = [
        r for r in table.entries()
        if str(r.get("validity")) in ("fresh", "dirty")
    ]
    if not rows:
        return ""
    total = len(rows)
    shown = rows[: max(1, int(max_entries))]
    lines = []
    for r in shown:
        path = _display_path(r)
        content_hash = table.cached_hash(r.get("path") or "")
        line_count = _line_count(store, table, r.get("path") or "")
        symbols = resolve_symbols(
            store, table.workspace, r.get("path") or "", content_hash
        )
        summary = f"{path}  @{content_hash or '--------'}  {line_count}L  " + _symbol_list(symbols)
        lines.append(summary)
    header = "## 已读文件（内容在外部状态表，可用 recall 按符号取回）"
    body = "\n".join(lines)
    if total > len(shown):
        body += f"\n[还有 {total - len(shown)} 个文件未列出]"
    return header + "\n" + body


def _symbol_list(symbols: list[dict]) -> str:
    if not symbols:
        return "[无符号索引]"
    picks = []
    for s in symbols[:40]:
        name = s.get("qualified_name") or s.get("name") or "?"
        line = s.get("start_line")
        picks.append(f"{name}:{int(line) + 1 if line is not None else '?'}")
    out = "[" + ", ".join(picks) + "]"
    if len(symbols) > len(picks):
        out += f" (+{len(symbols) - len(picks)} more)"
    return out


def _display_path(row: dict) -> str:
    """展示路径：存储值本就已经是规范的相对路径，这里只做一次防御性归一。"""
    try:
        return normalize_relpath(str(row.get("path") or ""))
    except ValueError:
        return str(row.get("path") or "")


def _line_count(store: Any, table: Any, path: str) -> int:
    """行数从 `file_content` 取 —— 注意：这是**读**，只发生在注入时，
    且压缩本身（snapshot/formatting）不写它。取不到就写 '?'。"""
    if store is None or not path:
        return 0
    try:
        row = store.get_file_content(table.workspace, path)
        if row and row.get("content") is not None:
            return len(str(row["content"]).splitlines())
    except Exception:
        pass
    return 0
