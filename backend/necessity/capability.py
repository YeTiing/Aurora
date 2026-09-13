"""能力装配约定 —— 四个能力如何挂到钩子契约上。

为什么需要这个文件：
    adapter/aurora/mount.py 需要按配置决定挂哪些能力，但四个能力是各自
    独立实现的。若每个能力自己发明挂载方式，装配层就得认识四种形态。
    这里约定**唯一形态**：每个能力暴露一个 `build_<name>_hooks(cfg)` 工厂，
    返回一个实现了 NecessityHooks 子集的**部分实现**，由 CompositeHooks
    组合成一个完整实现。

为什么用组合而不是继承：
    四个能力关心的钩子不重叠（Context Paging 只关心 read_file /
    after_compaction；Guard 只关心 before_tool / after_tool / scan_workspace）。
    继承会强迫每个能力实现全部 10 个方法；组合只需声明它关心的那几个。

降级是**结构性的**：某个能力未实现（ImportError）或工厂返回 None，
    CompositeHooks 直接跳过它，其余能力照常工作。
    INTEGRATION.md §8.2：任何降级都不得让 Agent 无法工作。
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from .hooks import (
    Decision,
    FileChange,
    NecessityHooks,
    NullHooks,
    ReadResult,
    TaskResult,
    ToolCall,
    ToolResult,
)

logger = logging.getLogger("necessity.capability")

# 四个能力的工厂函数名（各模块须以此为入口）
FACTORY_NAMES = {
    "context": "build_context_hooks",
    "guard": "build_guard_hooks",
    "reduce": "build_reduce_hooks",
    "attribution": "build_attribution_hooks",
}

# 能力的默认启用状态（INTEGRATION.md §8.1）
DEFAULT_ENABLED = {
    "context": True,      # 风险低，收益直接
    "guard": True,        # 默认 warn 模式（先观察违反频率）
    "reduce": False,      # 耗时，离线手动触发
    "attribution": False, # 分析用，不在主循环
}


class CompositeHooks:
    """把多个能力的部分实现组合成一个完整钩子实现。

    语义要点：
      - `before_tool` 返回**第一个非 allow 的 Decision**（短路）。
        安全优先：任一能力说 block，就 block。若都放行才放行。
      - `read_file` 返回**第一个非 None** 的结果。None 表示「本能力不管」。
      - `after_compaction` 让每个能力**依次**加工 summary（管道）。
      - 其余钩子全部转发给所有能力。
    """

    def __init__(self, capabilities: dict[str, Any]):
        self.caps = capabilities or {}

    # ── 生命周期 ─────────────────────────────────────────────────

    def on_task_start(self, task: dict) -> None:
        for name, cap in self.caps.items():
            fn = getattr(cap, "on_task_start", None)
            if fn:
                fn(task)

    def on_turn_end(self, turn: int) -> None:
        for cap in self.caps.values():
            fn = getattr(cap, "on_turn_end", None)
            if fn:
                fn(turn)

    def on_task_end(self, result: TaskResult) -> dict:
        """收集各能力的报告并合并到同一个 dict。

        合并而非覆盖：每个能力报告自己关心的指标（冗余率 / 约束统计 /
        归因结果），调用方拿到的是完整画像。
        """
        merged: dict[str, Any] = {}
        for name, cap in self.caps.items():
            fn = getattr(cap, "on_task_end", None)
            if not fn:
                continue
            try:
                out = fn(result)
            except Exception as e:
                logger.warning("capability %s on_task_end failed: %s", name, e)
                continue
            if isinstance(out, dict):
                merged[name] = out
        return merged

    # ── 工具调用 ─────────────────────────────────────────────────

    def before_tool(self, call: ToolCall) -> Decision:
        """短路语义：任一能力 block 即 block。

        为什么不是「最后一个赢」：安全组件（Guard）的判断不该被后续能力
        的 allow 覆盖。allow 是默认值，只有明确的 block/modify 才有意义。
        """
        for name, cap in self.caps.items():
            fn = getattr(cap, "before_tool", None)
            if not fn:
                continue
            try:
                d = fn(call)
            except Exception as e:
                # 契约 1：单个能力故障不影响其他能力，也不阻塞任务
                logger.warning("capability %s before_tool failed: %s", name, e)
                continue
            if isinstance(d, Decision) and d.action != "allow":
                return d
        return Decision()

    def after_tool(self, call: ToolCall, result: ToolResult) -> None:
        for cap in self.caps.values():
            fn = getattr(cap, "after_tool", None)
            if fn:
                fn(call, result)

    # ── 文件读写 ─────────────────────────────────────────────────

    def read_file(self, path: str, opts: dict) -> ReadResult | None:
        """第一个非 None 胜出 —— None 是「本能力不管」的约定。"""
        for cap in self.caps.values():
            fn = getattr(cap, "read_file", None)
            if not fn:
                continue
            try:
                r = fn(path, opts)
            except Exception as e:
                logger.warning("capability read_file failed: %s", e)
                continue
            if r is not None:
                return r
        return None

    def after_write(self, path: str, writer: str) -> None:
        for cap in self.caps.values():
            fn = getattr(cap, "after_write", None)
            if fn:
                fn(path, writer)

    # ── 上下文压缩 ───────────────────────────────────────────────

    def before_compaction(self, messages: list) -> None:
        for cap in self.caps.values():
            fn = getattr(cap, "before_compaction", None)
            if fn:
                fn(messages)

    def after_compaction(self, summary: str) -> str:
        """管道语义：每个能力依次加工 summary。"""
        out = summary
        for name, cap in self.caps.items():
            fn = getattr(cap, "after_compaction", None)
            if not fn:
                continue
            try:
                nxt = fn(out)
                if isinstance(nxt, str):
                    out = nxt
            except Exception as e:
                logger.warning("capability %s after_compaction failed: %s", name, e)
        return out

    # ── 工作区 ───────────────────────────────────────────────────

    def scan_workspace(self) -> list[FileChange]:
        """合并各能力扫到的变更，按 path 去重。"""
        seen: dict[str, FileChange] = {}
        for cap in self.caps.values():
            fn = getattr(cap, "scan_workspace", None)
            if not fn:
                continue
            try:
                changes = fn() or []
            except Exception as e:
                logger.warning("capability scan_workspace failed: %s", e)
                continue
            for ch in changes:
                if ch and ch.path:
                    seen[ch.path] = ch
        return list(seen.values())


def load_capabilities(cfg: dict | None = None) -> dict[str, Any]:
    """按配置装配能力。缺失的实现一律跳过（结构性降级）。

    cfg 形如 INTEGRATION.md §8.1：
        {"enabled": true, "context_paging": {"enabled": true, "mode": "index"},
         "guard": {"enabled": true, "default_action": "warn"}, ...}
    """
    import importlib

    cfg = cfg or {}
    out: dict[str, Any] = {}

    # 配置键与模块名的映射（文档用的是 context_paging 而模块是 context）
    cfg_keys = {
        "context": ("context_paging", "context"),
        "guard": ("guard",),
        "reduce": ("reduce",),
        "attribution": ("attribution",),
    }

    for name, modname in FACTORY_NAMES.items():
        try:
            # 合并进 Aurora 后包路径变为 backend.necessity.*
            mod = importlib.import_module(f"backend.necessity.{name}")
        except ImportError:
            logger.info("capability %s not implemented yet, skipping", name)
            continue

        factory: Callable | None = getattr(mod, modname, None)
        if factory is None:
            # 模块存在但没暴露约定的工厂 —— 明确报错而不是静默跳过，
            # 否则会表现为「能力配了但没生效」的诡异现象
            logger.warning(
                "backend.necessity.%s 存在但未暴露 %s，该能力不会被挂载", name, modname
            )
            continue

        # 该能力的启用状态：显式配置 > 默认值
        sub = None
        for k in cfg_keys[name]:
            if isinstance(cfg.get(k), dict):
                sub = cfg[k]
                break
        # 启用判定（顺序很重要）：
        #   1. 总开关 `enabled: False` 明确关闭一切 —— 未显式列出的能力一律不挂。
        #      ⚠️ 这条必须先判。曾写成「能力有 sub 配置时跳过分支」，导致
        #      对照组 B（只配了 context_paging）静默挂上了默认开启的 guard ——
        #      整个对照矩阵因此失效，且不报错。
        #   2. 该能力自己的 `enabled` 覆盖总开关。
        #   3. 都没有时用 DEFAULT_ENABLED。
        master_off = ("enabled" in cfg) and (cfg["enabled"] is False)
        if isinstance(sub, dict) and "enabled" in sub:
            enabled = bool(sub["enabled"])
        elif master_off:
            enabled = False
        else:
            enabled = DEFAULT_ENABLED[name]

        if not enabled:
            logger.info("capability %s disabled by config", name)
            continue

        try:
            impl = factory(sub or cfg)
        except Exception as e:
            # 工厂失败不能拖垮整个装配
            logger.warning("capability %s factory failed: %s", name, e)
            continue
        if impl is not None:
            out[name] = impl

    return out
