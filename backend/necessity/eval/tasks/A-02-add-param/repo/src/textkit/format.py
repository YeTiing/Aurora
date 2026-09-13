"""文本截断工具。"""
from __future__ import annotations

DEFAULT_SUFFIX = "..."


def truncate(text: str) -> str:
    """把文本截断到 10 个字符以内。

    ⚠️ 阈值与后缀都是**写死**的 —— 本次任务就是把后缀这个能力露出来。
    写死让「基线必然不满足新验收」：调用方无法传自定义后缀。
    """
    if len(text) <= 10:
        return text
    return text[:10] + DEFAULT_SUFFIX


def count_words(text: str) -> int:
    """与本次改动无关。"""
    return len(text.split())
