"""Core Paging（能力 1）—— 把文件内容从对话历史搬到对话外的状态表。

入口契约（core/capability.py::FACTORY_NAMES）：
    build_context_hooks(cfg) -> object

设计文档：CONTEXT_PAGING.md。本模块只负责编排 state / recall / compaction
三个纯逻辑部件，并把它们接到 `core.hooks.NecessityHooks` 的四个相关钩子上：

    read_file        命中状态表且 fresh → 返回 L1 索引；否则 None（宿主自己读）
    after_write      writer="agent"→dirty；其它→stale
    before_compaction 快照状态表版本（不碰 file_content）
    after_compaction  向摘要注入 file_state 索引

降级是结构性的（INTEGRATION.md §8.2）：没有 Store / 配置关闭 / 路径非法时，
`read_file` 一律返回 None，宿主按原逻辑读，**不报错**。
"""
from __future__ import annotations

import logging
import os
from typing import Any

from backend.necessity.hooks import ReadResult
from .compaction import DEFAULT_MAX_ENTRIES, render_injection, snapshot
from .recall import (
    grep_lines,
    line_slice,
    render_index,
    resolve_symbols,
    symbol_slice,
    truncate_note,
)
from .state import FileState, FileStateTable, to_relpath

logger = logging.getLogger("necessity.context")

__all__ = ["ContextPaging", "build_context_hooks"]

DEFAULT_TOKEN_BUDGET = 1500
# 小文件直接给全文：索引本身有固定开销，给 20 行的文件建索引反而更贵
DEFAULT_MIN_LINES_FOR_INDEX = 50
ROUGH_CHARS_PER_TOKEN = 4


class ContextPaging:
    """实现 NecessityHooks 中与 Context Paging 相关的子集。

    刻意**只实现 4 个方法**：其余钩子由 CompositeHooks 分发给别的能力。
    多实现一个空方法就会让「谁负责什么」变得模糊。
    """

    def __init__(self, cfg: dict | None = None):
        # 整个构造过程**不允许抛异常**：宿主装配时最坏情况必须退化为现状
        # （INTEGRATION.md §8.2），而不是让能力挂载失败拖垮任务。
        cfg = cfg or {}
        self.cfg = cfg
        self.token_budget = _int_cfg(cfg, "recall_token_budget", DEFAULT_TOKEN_BUDGET)
        self.max_entries = _int_cfg(cfg, "max_index_entries", DEFAULT_MAX_ENTRIES)
        self.min_lines = _int_cfg(cfg, "min_lines_for_index", DEFAULT_MIN_LINES_FOR_INDEX)
        self.workspace = str(cfg.get("workspace") or cfg.get("root") or ".")
        self.session_id = str(cfg.get("session_id") or "default")
        try:
            # 允许传 db 路径 —— 但工厂默认**不**自己建库：SQLite 不可写时
            # 应当关闭能力，而不是带着半截存储硬跑。
            self.store = cfg.get("store") or _open_store(cfg.get("db_path"))
        except Exception as e:
            logger.warning("context paging: store unavailable (%s)", e)
            self.store = None
        self.table = FileStateTable(self.store, self.workspace, self.session_id)

    # ── read_file ───────────────────────────────────────────────────

    def read_file(self, path: str, opts: dict | None = None) -> ReadResult | None:
        """接管读文件。**只有状态表命中才返回 ReadResult。**

        返回 None = 「本层不管」，宿主按原逻辑真读。这是默认关闭能力时
        宿主行为完全不变的保证（I1 空操作挂载）。
        """
        opts = opts or {}
        if not self._usable(path):
            return None
        try:
            rel = to_relpath(path, self.workspace)
        except ValueError:
            return None

        state = self.table.state(rel)
        if state is FileState.UNKNOWN:
            return None                      # 无记录 → 必须真读
        cached = self._cached(rel)
        if cached is None:
            return None

        content, content_hash, mtime = cached

        # (size, mtime) 只作快速排除；不一致 → 让宿主真读覆盖缓存（§6.4）。
        if state is FileState.FRESH and not self._probe(content, mtime, opts):
            self.table.mark_missing(rel)     # 状态表不可信了，下次必须真读
            return None

        # 宿主没给 stat 时，唯一能发现「外部工具改了文件」的途径是自己 stat
        # 一次磁盘；不做就等于放弃 §6.4 的判定。stat 失败（文件已删）→ missing。
        if state is FileState.FRESH and not (
            self._probe(content, mtime, opts) and self._disk_unchanged(rel, mtime)
        ):
            self.table.mark_missing(rel)
            return None

        # dirty = Agent 自己刚写；stale = 外部改动。两者都不能静默服务缓存：
        # 交给宿主真读，真读结果经 note_read 归位 fresh（合法重读）。
        if state in (FileState.DIRTY, FileState.STALE) and not opts.get("force"):
            return None

        symbols = resolve_symbols(self.store, self.workspace, rel, content_hash)
        lines = content.splitlines()
        self._record_read(rel)
        if opts.get("mode") == "full" or len(lines) < self.min_lines:
            # 逃生口 / 小文件：给全文（等价现状），但仍然记住这次真实读取
            return ReadResult(
                content=content, mode="full", path=rel,
                content_hash=content_hash, symbols=symbols,
                token_count=_estimate_tokens(content), note="[full]",
            )
        index = render_index(rel, content_hash, len(lines), symbols)
        return ReadResult(
            content=index, mode="index", path=rel,
            content_hash=content_hash, symbols=symbols,
            token_count=_estimate_tokens(index),
        )

    # ── 写：区分 writer ─────────────────────────────────────────────

    def after_write(self, path: str, writer: str) -> None:
        """写后迁移状态。`writer` 的取值见 INTEGRATION.md §3。

        agent → dirty（自己改的，别为了「确认一下」重读）
        其它  → stale（外部改动，必须重读）
        """
        if self.store is None or not path:
            return
        try:
            self.table.mark_write(path, writer)
        except Exception as e:
            logger.debug("context after_write failed for %s: %s", path, e)

    # ── 压缩契约 ────────────────────────────────────────────────────

    def before_compaction(self, messages: list) -> None:
        """快照状态表版本。**不读也不写 file_content**（不变量 1）。"""
        self._snapshot = snapshot(self.table)

    def after_compaction(self, summary: str) -> str:
        """注入 file_state 索引（不变量 2）。

        注入块只由状态表决定，与压缩内容无关 —— 所以压缩前后必须一致。
        """
        try:
            block = render_injection(self.table, self.store, max_entries=self.max_entries)
        except Exception as e:
            logger.debug("context after_compaction failed: %s", e)
            return summary
        if not block:
            return summary
        return f"{summary}\n\n{block}" if summary else block

    # ── recall（供工具层调用；不计入 R，EVAL.md §2.4）────────────────

    def recall(self, path: str, opts: dict | None = None) -> ReadResult | None:
        """按符号 / 行 / grep 取回。未命中或降级 → None（宿主真读）。"""
        opts = opts or {}
        if not self._usable(path):
            return None
        try:
            rel = to_relpath(path, self.workspace)
        except ValueError:
            return None
        cached = self._cached(rel)
        if cached is None:
            return None
        content, content_hash, _ = cached
        symbols = resolve_symbols(self.store, self.workspace, rel, content_hash)

        if opts.get("symbol"):
            body, note = symbol_slice(content, symbols, str(opts["symbol"]))
        elif opts.get("lines"):
            body, note = line_slice(content, opts["lines"])
        elif opts.get("grep"):
            body, note = grep_lines(content, str(opts["grep"]))
        else:
            body = render_index(rel, content_hash, len(content.splitlines()), symbols)
            note = "[仅索引，未加载内容]"

        tokens = _estimate_tokens(body)
        if tokens > self.token_budget:
            note = (note + " " + truncate_note(body, self.token_budget, tokens)).strip()
        return ReadResult(
            content=body, mode="recall", path=rel,
            content_hash=content_hash, symbols=symbols,
            token_count=tokens, note=note,
        )

    # ── 内部 ────────────────────────────────────────────────────────

    def _usable(self, path: str) -> bool:
        return bool(path) and self.store is not None

    def _cached(self, rel: str) -> tuple[str, str, float] | None:
        try:
            row = self.store.get_file_content(self.workspace, rel)
        except Exception:
            return None
        if not row or row.get("content") is None:
            return None
        mtime = float(row.get("mtime") or 0.0)
        return str(row["content"]), str(row.get("content_hash") or ""), mtime

    def _disk_unchanged(self, rel: str, cached_mtime: float) -> bool:
        """直接 stat 磁盘，确认缓存对应的版本还在（§6.4 快速排除）。

        只在 mtime 变化时判失效；mtime 没变就放行 —— 每次读都算 hash 就等于
        每次读都读盘，收益归零。mtime 精度不足导致的漏检由下一次真读兜住。
        """
        try:
            st = os.stat(os.path.join(self.workspace, rel))
        except OSError:
            return False
        return not (cached_mtime and abs(st.st_mtime - cached_mtime) > 1e-6)

    def _probe(self, content: str, mtime: float, opts: dict) -> bool:
        """宿主给了 (size, mtime) 就用它做快速排除；没给就自己去 stat 磁盘。"""
        size, cur_mtime = opts.get("size"), opts.get("mtime")
        if size is not None and int(size) != len(content.encode("utf-8")):
            return False
        if cur_mtime is not None:
            return abs(float(cur_mtime) - mtime) < 1e-6
        return True

    def note_read(self, path: str, content: str, *, mtime: float | None = None) -> None:
        """宿主真读后回报内容 —— read_file 返回 None 的那条路靠这里闭环。

        没有这个入口状态表永远不会被填充：宿主自己读完不会告诉我们内容。
        """
        if self.store is None or not path or content is None:
            return
        try:
            self.table.note_read(path, content, mtime=mtime)
        except Exception as e:
            logger.debug("context note_read failed for %s: %s", path, e)

    def _record_read(self, rel: str) -> None:
        """记录一次真实读取（R 的分子）。读失败不得影响返回。"""
        try:
            self.store.log_file_read(self.session_id, self.workspace, rel)
        except Exception:
            pass


# ── 工厂 ────────────────────────────────────────────────────────────

def build_context_hooks(cfg: dict | None = None):
    """硬契约入口（core/capability.py::FACTORY_NAMES）。

    **永不抛异常**：配置再离谱也返回一个可用对象。两段降级：
      1. 构造函数已内联兜异常（store 拿不到 → read_file 恒 None）
      2. 万一还有意外（例如 cfg 是不可下标的类型）→ 返回 NullHooks，
         宿主行为完全等于现状。
    """
    try:
        return ContextPaging(cfg)
    except Exception as e:
        logger.warning("context paging init failed, degrading to null hooks: %s", e)
        from backend.necessity.hooks import NullHooks
        return NullHooks()


def _int_cfg(cfg: dict, key: str, default: int) -> int:
    """宽容地读一个整数配置：类型不对就退回默认值，绝不因为配置崩掉装配。"""
    try:
        return int(cfg.get(key, default))
    except (TypeError, ValueError):
        return default


def _open_store(db_path: Any):
    """可选地在给定路径上打开 Store。任何失败 → None（降级）。"""
    if not db_path:
        return None
    try:
        from ..index.store import Store
        return Store(str(db_path))
    except Exception as e:
        logger.warning("context paging: store unavailable (%s), degrading", e)
        return None


def _estimate_tokens(text: str) -> int:
    """粗估 token。刻意不用真 tokenizer：主循环路径上必须便宜且确定性
    （INTEGRATION.md §3.3），且预算控制只需要量级正确。"""
    return max(1, len(text) // ROUGH_CHARS_PER_TOKEN)
