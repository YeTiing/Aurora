"""Context Paging 的 MCP 工具 —— **部分能力**，边界必须写在脸上。

INTEGRATION.md §1.3 对 Context Paging 的判定是 ⚠️ 部分可以，理由：
    「需要宿主**不提供**原生 `read_file`，改用 MCP 提供的读写工具；
      依赖 Agent 配合。」

这条限制的工程含义：`context_lookup` 本身能正确返回符号索引，但只要宿主
还挂着原生整文件读取，Agent 就会直接用它 —— 本工具被旁路，收益为零。
所以每次返回都带上 `caveat`，工具描述里也写明，不做「装上就生效」的暗示。

与 core 的关系：不自建索引。读 `file_content`（全文 + 哈希）与 `symbols`
表（唯一真源），断言用 `content_hash` 关联 —— 文件一变旧符号自动查不到，
这是 INTEGRATION.md §6.3 的失效语义，白拿的。
"""

from __future__ import annotations

from pathlib import Path

from .base import Tool, ToolOutcome, ToolError, obj_schema, optional, require

__all__ = ["context_lookup", "CONTEXT_TOOLS"]

_CAVEAT = (
    "能力边界：Context Paging 只在宿主**停用原生整文件 read_file** 后才有价值，"
    "否则 Agent 会直接读全文、旁路本工具。返回的是符号索引，不是全文。"
)
_MODES = ("index", "symbol", "grep", "lines")


def _relpath(hook, path: str) -> str:
    from backend.necessity.context.state import to_relpath

    try:
        return to_relpath(path, hook.workspace)
    except ValueError:
        raise ToolError(f"path 不在 workspace 内: {path!r}") from None


def _gate(params: dict):
    """必需参数 + 模式专属参数的前置校验（在碰存储前先失败，消息更清楚）。"""
    workspace = require(params, "workspace", str)
    path = require(params, "path", str)
    if not Path(workspace).is_dir():
        raise ToolError(f"workspace 不是目录: {workspace}")
    mode = optional(params, "mode", str, "index")
    if mode not in _MODES:
        raise ToolError(f"'mode' 只能是 {' | '.join(_MODES)}，收到 {mode!r}")
    symbol = optional(params, "symbol", str, "")
    pattern = optional(params, "grep", str, "")
    lines_spec = params.get("lines")
    if lines_spec is not None:
        if (not isinstance(lines_spec, list) or len(lines_spec) != 2
                or any(not isinstance(x, int) or isinstance(x, bool) for x in lines_spec)):
            raise ToolError("'lines' 必须是两个整数组成的数组，如 [10, 30]")
    if mode == "symbol" and not symbol:
        raise ToolError("mode=symbol 需要 'symbol' 参数")
    if mode == "grep" and not pattern:
        raise ToolError("mode=grep 需要 'grep' 参数")
    if mode == "lines" and not lines_spec:
        raise ToolError("mode=lines 需要 'lines' 参数")
    return workspace, path, mode, symbol, pattern, lines_spec


def context_lookup(params: dict) -> ToolOutcome:
    """按路径返回符号索引，或按 symbol / grep / lines 取回片段。"""
    from backend.necessity.context import build_context_hooks

    workspace, path, mode, symbol, pattern, lines_spec = _gate(params)
    db = optional(params, "db", str, "") or None
    hook = build_context_hooks({"db_path": db})
    store = getattr(hook, "store", None)
    if store is None:
        return _degraded(path, "no-store",
                         "没有可用的索引库（未给 db 或 SQLite 不可写），已降级。")
    hook.workspace = str(Path(workspace).resolve())
    hook.session_id = optional(params, "session_id", str, "mcp")

    rel = _relpath(hook, path)
    cached = hook._cached(rel)
    if cached is None:
        return _degraded(path, "not-indexed",
                         f"{rel} 在 file_content 里没有记录（尚未建索引）。")
    content, content_hash, _mtime = cached

    symbols = _symbols_of(store, hook.workspace, rel, content_hash)
    if mode == "index":
        from backend.necessity.context.recall import render_index

        body = render_index(rel, content_hash, len(content.splitlines()), symbols)
        note = "[仅索引，未加载内容；用 mode=symbol 取具体实现]"
    else:
        from backend.necessity.context.recall import grep_lines, line_slice, symbol_slice

        if mode == "symbol":
            found = symbol_slice(content, symbols, symbol)
        elif mode == "grep":
            found = grep_lines(content, pattern)
        else:
            found = line_slice(content, lines_spec)
        if found is None:
            raise ToolError(f"在 {rel} 中取不到内容（mode={mode}）", code=-32000)
        body, note = found

    structured = {
        "path": rel, "mode": mode, "content_hash": content_hash,
        "symbols": symbols, "body": body, "note": note,
        "delivered_value_if": "宿主停用原生 whole-file read_file",
        "caveat": _CAVEAT,
    }
    return ToolOutcome(text=f"{body}\n\n{note}\n\n{_CAVEAT}", structured=structured)


def _symbols_of(store, workspace: str, rel: str, content_hash: str) -> list[dict]:
    """查 symbols 表（唯一真源）。失败 -> 空列表（降级为无符号索引）。"""
    try:
        return store.query_symbols(workspace, rel, content_hash=content_hash)
    except Exception:
        return []


def _degraded(path: str, reason: str, text: str) -> ToolOutcome:
    return ToolOutcome(
        text=f"{path}: {text}\n\n{_CAVEAT}",
        structured={"path": path, "degraded": True, "reason": reason,
                    "symbols": [], "caveat": _CAVEAT},
    )


CONTEXT_TOOLS = [
    Tool(
        name="context_lookup",
        description=(
            "Context Paging（部分能力）：按路径返回符号索引，或按 symbol / grep / lines "
            "取回片段，让 Agent 读结构而不是整文件。⚠️ 仅当宿主不提供原生整文件读取时"
            "才有价值（INTEGRATION.md §1.3）。"
        ),
        input_schema=obj_schema({
            "workspace": {"type": "string", "description": "工作区根（须为目录）"},
            "path": {"type": "string", "description": "目标文件（workspace 相对或绝对）"},
            "mode": {"type": "string", "enum": list(_MODES), "default": "index",
                     "description": "index=符号索引；symbol/grep/lines=按需取回片段"},
            "symbol": {"type": "string", "description": "mode=symbol 时要取的符号名"},
            "grep": {"type": "string", "description": "mode=grep 时的正则"},
            "lines": {"type": "array", "items": {"type": "integer"},
                      "description": "mode=lines 时的 [start, end]"},
            "session_id": {"type": "string", "default": "mcp"},
            "db": {"type": "string", "description": "索引 SQLite 库路径"},
        }, ["workspace", "path"]),
        fn=context_lookup,
        annotations={"readOnlyHint": True},
    ),
]
