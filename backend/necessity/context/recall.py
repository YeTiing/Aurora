"""按符号 / 行范围取回内容（CONTEXT_PAGING.md §6.3）。

寻址单位是**符号**而不是行号：Agent 的意图是「我要看 parse 函数」，
按行寻址会迫使它先知道行号，从而退化回整文件重读。

符号索引本身不在这里存 —— 唯一真源是 `symbols` 表，按
`(workspace, file, content_hash)` 查询（INTEGRATION.md §6.1）。本模块
只做「从状态表已有的全文里切出请求的那一段」。

输出格式是**行号前缀**（`  12| def parse(...)`）：Agent 拿到后可以直接
做行级编辑，而不必再读一遍去数行号。
"""
from __future__ import annotations

from typing import Any

__all__ = [
    "render_index", "symbol_slice", "line_slice", "grep_lines",
    "truncate_note", "resolve_symbols",
]

# 默认符号索引里最多列几个符号（超出的用 +N 概括，避免索引本身膨胀）。
MAX_INDEX_SYMBOLS = 40


def _fenced(content: str, note: str = "") -> str:
    banner = "```python\n" + content.rstrip("\n") + "\n```"
    return f"{banner}\n{note}" if note else banner


def render_index(path: str, content_hash: str, line_count: int,
                 symbols: list[dict], *, note: str = "") -> str:
    """L1 符号索引 —— 对话里放这个，而不是全文。

    必须显式标注「仅索引，未加载内容」（CONTEXT_PAGING.md §9 高风险项：
    Agent 误以为索引就是全文，直接改代码）。
    """
    head = f"{path}  @{content_hash or '--------'}  {line_count}L"
    picks = symbols[:MAX_INDEX_SYMBOLS]
    parts = []
    for s in picks:
        name = s.get("qualified_name") or s.get("name") or "?"
        line = s.get("start_line")
        # LSP 是 0-based，展示与编辑都用 1-based
        parts.append(f"{name}:{int(line) + 1 if line is not None else '?'}")
    listing = "[" + ", ".join(parts) + "]"
    if len(symbols) > len(picks):
        listing += f" (+{len(symbols) - len(picks)} more)"
    body = f"<file_state>\n{head}\n{listing}\n</file_state>"
    detail = note or "[仅索引，未加载内容；用 recall(symbol=...) 取具体实现]"
    return f"{body}\n{detail}"


def symbol_slice(content: str, symbols: list[dict], symbol: str) -> tuple[str, str]:
    """按符号名取实现。返回 (代码段, 说明)。

    匹配优先级：qualified_name 精确 → name 精确 → 后缀匹配（`parse` 命中
    `Tokenizer.parse`）。歧义不做猜测，返回全部候选的清单让 Agent 自己选
    —— 猜错符号比让 Agent 多问一句的代价高得多。
    """
    lines = content.splitlines()
    exact, short, suffix = [], [], []
    for s in symbols:
        qn = str(s.get("qualified_name") or "")
        nm = str(s.get("name") or "")
        if qn == symbol:
            exact.append(s)
        elif nm == symbol:
            short.append(s)
        elif qn.endswith("." + symbol) or qn.split("#")[0].endswith("." + symbol):
            suffix.append(s)
    matches = exact or short or suffix
    if not matches:
        return "", f"[no symbol named {symbol!r} in this file]"
    if len(matches) > 1 and not exact:
        names = ", ".join(str(m.get("qualified_name")) for m in matches)
        return "", f"[ambiguous symbol {symbol!r}: {names} —— 请用限定名]"
    s = matches[0]
    body = _extract(lines, s)
    qn = s.get("qualified_name") or symbol
    return body, f"[recall {qn} @ {_range_label(s, lines)}]"


def line_slice(content: str, lines: list[Any], *, context: int = 3) -> tuple[str, str]:
    """按行取回。接受 [12, 45] / [(12, 45)] / ["12-45"] 三种写法。

    为什么容忍三种写法：宿主/模型对「行范围」的心智模型不统一，解析失败
    就报错会让 Agent 多花一轮；这里把常见写法都归一化。
    """
    total = len(content.splitlines())
    segments: list[tuple[int, int]] = []
    try:
        for item in lines or []:
            if isinstance(item, str):
                lo, _, hi = item.partition("-")
                segments.append((int(lo), int(hi or lo)))
            elif isinstance(item, (list, tuple)):
                segments.append((int(item[0]), int(item[-1])))
            else:
                n = int(item)
                segments.append((n, n))
    except (TypeError, ValueError, IndexError):
        return "", f"[bad lines spec: {lines!r}]"
    if not segments:
        return "", "[empty lines spec]"

    out: list[str] = []
    for lo, hi in segments:
        start = max(1, lo - context)
        end = min(total, hi + context)
        out.append(_render(content, start, end))
    label = ", ".join(f"{lo}-{hi}" for lo, hi in segments)
    return "\n...\n".join(out), f"[recall lines {label} (含 {context} 行上下文)]"


def grep_lines(content: str, pattern: str, *, context: int = 2, limit: int = 50) -> tuple[str, str]:
    """文件内搜索。刻意只做字面匹配（大小写不敏感）：正则由宿主工具负责，
    这里的目标是「改完代码后确认某处在哪一行」，不需要表达式能力。"""
    if not pattern:
        return "", "[empty grep pattern]"
    needle = pattern.lower()
    src = content.splitlines()
    hits = [i for i, line in enumerate(src) if needle in line.lower()]
    if not hits:
        return "", f"[no match for {pattern!r}]"
    blocks, merged = [], []
    for i in hits:
        lo, hi = max(0, i - context), min(len(src), i + context + 1)
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    for lo, hi in merged[:limit]:
        blocks.append(_render(content, lo + 1, hi, zero_based=True))
    note = f"[grep {pattern!r}: {len(hits)} hit(s)]"
    if len(merged) > limit:
        note += f" [仅显示前 {limit} 段]"
    return "\n...\n".join(blocks), note


def truncate_note(content: str, token_budget: int, est_tokens: int) -> str:
    """超预算时的显式提示 —— 绝不静默截断（§6.3）。

    ``(lambda count, budget)`` 显式说明超了多少；静默截断会让 Agent
    以为已经看全，是比报错更糟的失败模式。
    """
    return (
        f"[内容约 {est_tokens} tokens，超过 recall_token_budget={token_budget}；"
        "用 lines=[a,b] 或 symbol=... 缩小范围再取]"
    )


# ── 内部 ─────────────────────────────────────────────────────────────

def _extract(lines: list[str], sym: dict) -> str:
    # LSP 的 start/end 是 0-based；end 是包含性的（符号定义的最后一行）。
    start = int(sym.get("start_line") or 0)
    end = int(sym.get("end_line") or start)
    start = max(0, min(start, max(0, len(lines))))
    end = max(start, min(end, max(0, len(lines) - 1)))
    return _render("\n".join(lines), start, end + 1, zero_based=True)


def _range_label(sym: dict, lines: list[str]) -> str:
    start = int(sym.get("start_line") or 0) + 1
    end = int(sym.get("end_line") or 0) + 1
    end = min(max(end, start), max(1, len(lines)))
    return f"L{start}-{end}"


def _render(content: str, start: int, end: int, *, zero_based: bool = False) -> str:
    """带行号渲染。行号是 1-based，前缀宽度固定 4，便于对齐。"""
    src = content.splitlines()
    if zero_based:
        lo, hi = max(0, start), min(len(src), end)
        chunk = list(enumerate(src[lo:hi], start=lo + 1))
    else:
        lo, hi = max(1, start), min(len(src), end)
        chunk = list(enumerate(src[lo - 1:hi], start=lo))
    return "\n".join(f"{n:>4}| {text}" for n, text in chunk)


def resolve_symbols(store: Any, workspace: str, path: str, content_hash: str) -> list[dict]:
    """查 `symbols` 表（唯一真源）。任何失败 → 空列表（降级为无索引）。

    这里刻意不自己存符号：CONTEXT_PAGING.md 初稿曾把 `symbols_json` 放进
    file_content，INTEGRATION.md §6.1 修正为「符号唯一真源是 symbols 表」。
    按 content_hash 查询还白拿了失效语义 —— 文件变了就查不到旧符号。
    """
    if store is None or not content_hash:
        return []
    try:
        return store.query_symbols(workspace, path, content_hash=content_hash)
    except Exception:
        return []
