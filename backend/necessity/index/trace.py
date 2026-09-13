"""轨迹采集 —— 只记事实，不做判断。

设计原则（INTEGRATION.md §5.2）：
    **只记录事实，不做判断。** 判断留给 core/attribution/signals.py，
    这样信号逻辑可以随时改而不影响已采集的数据。

为什么需要补埋点：Aurora 的 `agent/sse_events.py` 有 62 个事件常量，
但**没有 file_read / file_write / compaction**（已实测确认）。而这三个
恰好是 Context Paging 与 Attribution 的必需输入：
  - file_read  → 重复读取率（Gate 0 的核心指标）
  - compaction → 区分「遗忘式重读」与「合法重读」
  - file_write → 判断 Agent 是否改了自己没读过的文件

存储用 store 的 SQLite（复用同一个库文件，避免第二个真源）。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

logger = logging.getLogger("necessity.index.trace")

EventKind = Literal[
    "file_read",          # 读文件（含量与哈希）
    "file_write",         # 写文件（增删行数）
    "compaction",         # 上下文压缩（压缩前后 token、丢弃范围）
    "edit_revert",        # 编辑回滚
    "constraint_violation",  # 约束违反
    "test_run",           # 测试运行
]

# 六类事件，对应 INTEGRATION.md §5.2 的表格。
REQUIRED_EVENTS: tuple[str, ...] = (
    "file_read", "file_write", "compaction",
    "edit_revert", "constraint_violation", "test_run",
)


@dataclass
class AgentEvent:
    """一条轨迹事实。

    `payload` 只放客观数据（路径、行数、哈希、耗时），**不放判断结论**
    —— 判断属于 signals.py。
    """
    session_id: str
    kind: EventKind
    turn: int = 0
    ts: float = field(default_factory=time.time)
    payload: dict = field(default_factory=dict)


class TraceStore:
    """轨迹的内存缓冲 + 落库。

    内存缓冲的理由：主循环路径上不能每个事件都做一次磁盘写
    （`INTEGRATION.md §3.3` 要求钩子快）。攒够一批再 flush。

    线程安全用可重入锁 —— 钩子可能从工具线程被调用。
    """

    def __init__(self, db=None, flush_every: int = 50):
        self._db = db
        self._buf: list[AgentEvent] = []
        self._lock = threading.RLock()
        self._flush_every = max(1, int(flush_every))
        self._dropped = 0

    def record(self, session_id: str, kind: EventKind, turn: int = 0,
               **payload: Any) -> None:
        """记一条事实。任何异常都不向外抛（契约 1：不得阻塞任务）。"""
        try:
            with self._lock:
                self._buf.append(AgentEvent(
                    session_id=session_id, kind=kind, turn=turn, payload=payload,
                ))
                if len(self._buf) >= self._flush_every:
                    self._flush_locked()
        except Exception:
            # 采集失败不能影响任务；计数以便排查
            self._dropped += 1

    def flush(self) -> int:
        with self._lock:
            return self._flush_locked()

    def _flush_locked(self) -> int:
        if not self._buf:
            return 0
        if self._db is None:
            # 无存储时保留在内存（便于测试与 Gate 0 的纯内存模式）
            return 0
        batch, self._buf = self._buf, []
        try:
            self._db.append_agent_events([
                {
                    "session_id": e.session_id, "kind": e.kind, "turn": e.turn,
                    "ts": e.ts, "payload": json.dumps(e.payload, ensure_ascii=False),
                }
                for e in batch
            ])
            return len(batch)
        except Exception:
            # 落库失败把数据放回，下次再试；不丢事实
            self._buf = batch + self._buf
            self._dropped += len(batch)
            return 0

    # ── 查询（供 signals / Gate 0 用）──────────────────────────────

    def events(self, session_id: str = "", kind: str = "") -> list[AgentEvent]:
        with self._lock:
            out = [
                e for e in self._buf
                if (not session_id or e.session_id == session_id)
                and (not kind or e.kind == kind)
            ]
        if self._db is not None:
            try:
                rows = self._db.query_agent_events(session_id=session_id, kind=kind)
            except Exception as e:
                # ⚠️ 这里曾写 `except Exception: pass` —— 一个 TypeError
                # （payload 已是 dict 却又 json.loads）被静默吞掉，
                # 表现为「事件写进去了但读不回来」，Attribution 与 Gate 0
                # 因此永远返回 unknown 且**不报错**。
                # 采集失败必须可见，否则会伪装成「没有浪费行为」。
                logger.warning("query_agent_events failed, returning buffer only: %s", e)
                return out
            for r in rows:
                payload = r.get("payload")
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload or "{}")
                    except Exception:
                        payload = {}
                elif not isinstance(payload, dict):
                    payload = {}
                out.append(AgentEvent(
                    session_id=r.get("session_id", ""), kind=r.get("kind", ""),
                    turn=int(r.get("turn", 0) or 0), ts=float(r.get("ts", 0.0) or 0.0),
                    payload=payload,
                ))
        return out

    def stats(self) -> dict[str, Any]:
        with self._lock:
            by_kind: dict[str, int] = {}
            for e in self._buf:
                by_kind[e.kind] = by_kind.get(e.kind, 0) + 1
            return {
                "buffered": len(self._buf),
                "dropped": self._dropped,
                "buffered_by_kind": by_kind,
                "required_kinds": list(REQUIRED_EVENTS),
            }

    def missing_event_kinds(self) -> list[str]:
        """哪些必需事件一次都没被记录过。

        用途：`ATTRIBUTION.md` Gate 6 的失败判据之一是「unknown 占比 > 40%
        → 信号覆盖不足，补埋点」。这个方法是那个判断的直接依据。
        """
        seen = {e.kind for e in self.events()}
        return [k for k in REQUIRED_EVENTS if k not in seen]


# 进程级单例（宿主挂载时使用）
_trace: TraceStore | None = None
_trace_lock = threading.Lock()


def get_trace(db=None) -> TraceStore:
    global _trace
    with _trace_lock:
        if _trace is None:
            _trace = TraceStore(db=db)
        elif db is not None and _trace._db is None:
            _trace._db = db
        return _trace


def reset_trace() -> None:
    """测试隔离用。"""
    global _trace
    with _trace_lock:
        _trace = None
