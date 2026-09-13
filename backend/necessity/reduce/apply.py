"""hunk 的正向/反向应用。

从 split.py 拆出（该文件超 300 行上限；职责上「解析分组」与「应用补丁」
也是两件事）。

为什么需要反向应用：必要性最小化的语义是「撤销不在子集里的 hunk」
（DIFF_REDUCER.md §1.2：求最小 D'⊆D 使 T(D')=pass）。所以核心操作是
**从「已应用全部改动」的状态出发，反向撤销一部分 hunk**，而不是正向应用。
"""
from __future__ import annotations

from .split import Hunk

__all__ = ["hunk_header", "reverse_hunk_lines", "apply_text_patch"]


def hunk_header(h: Hunk) -> str:
    """重建 `@@ -a,b +c,d @@` 头。"""
    return f"@@ -{h.old_start},{h.old_count} +{h.new_start},{h.new_count} @@"


def reverse_hunk_lines(h: Hunk) -> list[str]:
    """【已废弃】带前缀的反转行序列，保留仅为兼容。

    ⚠️ 不要用它写文件：返回值带 +/- 前缀。用 `_reverse_lines` 拿纯内容。
    """
    out: list[str] = []
    for ln in h.body:
        if not ln:
            # 空行在 diff 里是「空上下文行」—— 保留为空上下文
            out.append(" ")
            continue
        c = ln[0]
        if c == "+":
            continue                      # 撤销新增 = 不写这行
        if c == "-":
            out.append("+" + ln[1:])      # 撤销删除 = 加回
        elif c == " ":
            out.append(ln)
        else:
            out.append(" " + ln)          # 无前缀的行按上下文处理
    return out


def apply_text_patch(original: str, hunks: list[Hunk], reverse: bool = False) -> str:
    """把若干 hunk（或它们的反转）应用到一段文本上。

    用途：必要性最小化的语义是「撤销不在子集里的 hunk」。调用方传
    `reverse=True` 并对要撤销的 hunk 调用即可。

    实现是**按行号从后往前**应用，避免前面的改动导致后面行号偏移。
    不做模糊匹配 —— 沙箱里的文件是精确基线，模糊匹配会掩盖错误。
    """
    if not hunks:
        return original
    lines = original.splitlines(keepends=True)
    # 从后往前，避免行号漂移
    ordered = sorted(hunks, key=lambda h: h.old_start, reverse=True)
    for h in ordered:
        if reverse:
            # 反向 = 撤销该 hunk；按 new 侧定位，内容由 _reverse_lines 给出
            body = _reverse_lines(h)
            start = h.new_start - 1
            count = h.new_count
        else:
            body = _forward_lines(h)
            start = h.old_start - 1
            count = h.old_count

        if start < 0:
            start = 0
        # 替换 [start, start+count) 这段
        end = start + max(count, 0)
        if count == 0:
            end = start
        # 保底裁剪，避免越界（文件比 hunk 声称的短）
        start = min(start, len(lines))
        end = min(end, len(lines))
        lines[start:end] = [b + chr(10) for b in body]
    return "".join(lines)


def _reverse_lines(h: Hunk) -> list[str]:
    """撤销该 hunk 之后的纯内容行（**不带 diff 前缀**）。

    反向 = 保留上下文 + 加回被删的 + 去掉新增的。
    返回纯内容：调用方会直接写入文件，带 +/- 前缀会污染源码。
    """
    out: list[str] = []
    for ln in h.body:
        if not ln:
            out.append("")
            continue
        c = ln[0]
        if c == "+":
            continue
        out.append(ln[1:] if c in "- " else ln)
    return out


def _forward_lines(h: Hunk) -> list[str]:
    """hunk 的正向行内容（去掉 +/-/空格 前缀）。"""
    out: list[str] = []
    for ln in h.body:
        if not ln:
            out.append("")
            continue
        c = ln[0]
        if c == "-":
            continue
        out.append(ln[1:] if c in "+ " else ln)
    return out
