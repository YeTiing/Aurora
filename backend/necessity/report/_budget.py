"""A1 异步段的预算解析 —— 从 bundle.py 拆出（触 300 行上限）。

单独成模块的另一个理由：这段代码**依赖 gate 包但又不该强依赖它**。
A1 是独立的实现，在没有横切模块的环境里也该能工作；把「取默认预算」
隔离在这里，bundle.py 的主体就与 gate 解耦。
"""
from __future__ import annotations


def default_budget_for(capability: str):
    """取规范 §1.7 声明的默认预算。取不到返回 None（不强制）。

    不硬依赖 gate 包：`report` 在没有 `gate` 的环境里也该能独立工作
    （它是 A1 的实现，不该被横切模块的可用性绑住）。
    """
    try:
        from backend.necessity.gate.budget import CAPABILITY_BUDGETS
        b = CAPABILITY_BUDGETS.get(capability)
        if b is None:
            return None
        # 每次取用都要**新建**一份：预算是有状态的（记 used/elapsed），
        # 共享同一个实例会让多次调用的用量累加，第二次必然超限。
        from dataclasses import replace as _replace
        fresh = _replace(b)
        fresh.reset()
        return fresh
    except Exception:
        return None
