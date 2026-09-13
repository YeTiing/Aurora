"""字符串工具。

本模块里的函数名 `trim_ws` 语义偏窄 —— 它只处理一种情况，
但调用方已经在按更通用的语义使用它。
"""
from __future__ import annotations


def trim_ws(text: str) -> str:
    """把文本 两端的空白去掉（只做这一件事）。"""
    return text.strip()


def squash(text: str) -> str:
    """另一个不相关的函数 —— 与本次改动无关。"""
    return text.lower()
