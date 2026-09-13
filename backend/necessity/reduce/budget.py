"""搜索预算 —— DIFF_REDUCER.md §6.3 的硬上限。

从 search.py 拆出（接近 300 行上限）。

为什么预算是独立概念而不是搜索的附属参数：
    文档 §7 总原则是「宁可返回未收敛的最优解，也不要返回错误结论」。
    预算决定了哪个先发生 —— 它必须能被单独传递、观测、并在报告里体现，
    而不是埋在搜索实现里。
"""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class Budget:
    max_test_runs: int = 200
    max_wall_time: float = 15 * 60.0
    max_hunk_count: int = 60
    _t0: float = 0.0
    test_runs: int = 0

    def start(self) -> None:
        self._t0 = time.monotonic()

    def exhausted(self) -> bool:
        if self.test_runs >= self.max_test_runs:
            return True
        if self._t0 and (time.monotonic() - self._t0) >= self.max_wall_time:
            return True
        return False

    def remaining_runs(self) -> int:
        return max(0, self.max_test_runs - self.test_runs)

    def elapsed(self) -> float:
        return (time.monotonic() - self._t0) if self._t0 else 0.0
