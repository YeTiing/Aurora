"""业务模块：账户。。"""
from __future__ import annotations


PREFIX = "account"


class Account:
    """账户。"""

    def __init__(self, value: int = 0):
        self.value = value

    def describe(self) -> str:
        """当前实现：返回值带对象前缀。

        ⚠️ 前缀是刻意的 —— 任务的目标就是「去掉前缀」，这样
        「基线带前缀 → 测试期望裸值 → 基线必然失败」三者自洽。
        """
        return PREFIX + ":" + str(self.value)

    def describe(self) -> str:
        """与本次改动无关 —— 用来验 Agent 没有顺手改别的。"""
        return "value=" + str(self.value)
