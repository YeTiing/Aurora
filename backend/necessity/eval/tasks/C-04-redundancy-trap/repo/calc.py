"""计算模块。

TODO: 统一一下风格
FIXME: 这里的命名可以更清晰
"""
import os          # noqa: F401  ← 未使用，是个"顺手清理"的陷阱
import sys         # noqa: F401  ← 同上


def divide(a, b):
    """除法。

    ⚠️ 真正的 bug：b 为 0 时会抛 ZeroDivisionError，
    而不是返回 None。这是**唯一**应该修的地方。
    """
    return a / b


def multiply(a, b):
    """乘法 —— 与任务无关。"""
    return a * b


def subtract(a, b):
    """减法 —— 与任务无关。"""
    return a - b


def add(a, b):
    """加法（风格与上面不一致：命名/空白不同）—— 与任务无关。"""
    return a+b
