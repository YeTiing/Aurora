"""lookup.py —— 按位置反查符号。

为什么单独一个模块而不是加进 store：
    store 是**存储层**（schema + 事务），按位置匹配是**查询语义**
    （行范围包含判断），职责不同。store 的 `query_symbols` 必须带
    content_hash（那是有效性机制），而「CLI 给了 file:line:col」这个
    场景需要先解析哈希再匹配范围 —— 这层编排放在这里。

位置语义（与 symbols.py 的采点规则配套）：
    存的是 selectionRange（指向符号名）。所以反查时给的 (line, col)
    也应指向符号名。CLI 层会提示这一点 —— 指向 `def` 行会查不到，
    而这是本项目最容易踩的坑（probe 实测：range.start 查 references 得 0 条）。
"""
from __future__ import annotations

from pathlib import Path

from .symbols import file_content_hash


def find_symbol_at(store, workspace: str, file: str, line: int, col: int) -> dict | None:
    """在 (file, line, col) 处找符号。

    匹配优先级：
      1. 精确匹配 selectionRange 起点（最常见的正确用法）
      2. 匹配 selectionRange 起点所在行、且列在最接近的位置（容错：列差几格）
      3. 该行任一符号（容错：只给了行号）

    为什么要容错：调用方拿到的位置可能来自编辑器光标（列不精确）。
    **不做「落在 range 内」的宽松匹配** —— range 覆盖整个函数体，那会把
    「函数体里某行」误判成「这个函数」，与你想要的答案不是一回事。
    """
    if not file:
        return None

    rel = file.replace("\\", "/")
    # 若给的是绝对路径，转成相对
    try:
        ws = Path(workspace).resolve()
        p = Path(file)
        if p.is_absolute():
            rel = p.resolve().relative_to(ws).as_posix()
    except (ValueError, OSError):
        rel = file.replace("\\", "/")

    # 取当前文件内容的哈希 —— 这是 store 的有效性判据，必须带上。
    # 取不到内容说明文件已被改动或未索引，直接返回 None（不猜）。
    content = _read_content(store, workspace, rel)
    if content is None:
        return None
    try:
        h = file_content_hash(content)
    except Exception:
        return None

    syms = store.query_symbols(workspace, rel, content_hash=h)
    if not syms:
        return None

    exact = [s for s in syms if s["start_line"] == line and s["start_col"] == col]
    if exact:
        return exact[0]

    same_line = [s for s in syms if s["start_line"] == line]
    if same_line:
        same_line.sort(key=lambda s: abs(int(s["start_col"]) - col))
        return same_line[0]

    return None


def _read_content(store, workspace: str, rel: str) -> str | None:
    """从 store 取文件内容；取不到再从磁盘读（保持与索引一致）。

    先查 store 是为了让「索引记录的内容」与「当前磁盘内容」不一致时能被
    发现 —— 若两者哈希不同，说明文件在索引之后被改过，此时应返回 None
    而不是用新内容去查旧符号（那正是 content_hash 机制要防的事）。
    """
    try:
        rec = store.get_file_content(workspace, rel)
        if rec and rec.get("content") is not None:
            return rec["content"]
    except Exception:
        pass
    return None
