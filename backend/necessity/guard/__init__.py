"""core.guard —— 能力 2：Constraint Guard（GUARD.md）。

对外唯一入口是 `build_guard_hooks(cfg)`（core/capability.py::FACTORY_NAMES
依赖这个名字）。其余模块是内部实现：

    spec.py        约束定义 + 类型白名单 + 校验
    compiler.py    NL → 结构化（唯一允许用 LLM 的模块）+ 显式拒绝
    checker.py     确定性验证器（**不得导入 LLM 客户端**）
    workspace.py   工作区变更扫描（与工具无关的权威数据源）
    interceptor.py 预检 / 后检 / 回滚 + 钩子实现

延迟导入：`core.guard.spec/checker/workspace` 不依赖任何 LLM 或宿主，
可以单独使用（离线实验只跑 checker，不需要把 interceptor 拉起来）。
"""
from __future__ import annotations

__all__ = ["build_guard_hooks", "GuardHooks", "compile_constraints"]


def build_guard_hooks(cfg: dict | None = None):
    """工厂 —— 硬契约，不可改名。"""
    from .interceptor import GuardHooks
    return GuardHooks(cfg)


def __getattr__(name: str):
    # PEP 562：避免 import backend.necessity.guard 时就把 interceptor 的一切都拉进来
    if name == "GuardHooks":
        from .interceptor import GuardHooks
        return GuardHooks
    if name == "compile_constraints":
        from .compiler import compile_constraints
        return compile_constraints
    raise AttributeError(name)
