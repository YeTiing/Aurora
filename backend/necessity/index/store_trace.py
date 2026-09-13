"""轨迹事件的存取 —— store 的 agent_event 部分。

**这曾是一个真实缺口**：`core/index/trace.py` 通过
`db.append_agent_events` / `db.query_agent_events` 存取事件，但 `Store`
从未实现这两个方法，schema 里也没有 `agent_event` 表。后果是
Attribution 与 Gate 0 在真实数据上**永远拿不到事件**（返回 unknown），
而且**不报错** —— 两个独立的 agent 各自发现了这个问题。

独立成模块而非塞进 store_ops.py：后者已接近 300 行上限，
且「轨迹」与「符号索引」是不同关注点（前者是会话级时序数据，
后者是代码结构数据）。

INTEGRATION.md §5.2 的原则：**只记录事实，不做判断**。
所以 payload 原样存 JSON 文本，不拆字段 —— 判断逻辑改了不需要重采数据。
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Mapping


class StoreTraceMixin:
    """给 Store 提供 agent_event 的读写。"""

    def append_agent_events(self, rows: Iterable[Mapping[str, Any]]) -> int:
        """批量写事件。返回写入条数。

        用 executemany 单事务：轨迹是高频写入（每个工具调用都可能产生），
        逐条提交会让建图/跑分为主循环路径付出不必要的 I/O 代价。
        """
        params: list[tuple] = []
        for r in rows or ():
            try:
                payload = r.get("payload")
                if not isinstance(payload, str):
                    # 允许调用方直接传 dict；统一序列化，保证读回来是合法 JSON
                    payload = json.dumps(payload or {}, ensure_ascii=False)
                params.append((
                    str(r.get("session_id", "")),
                    str(r.get("kind", "")),
                    int(r.get("turn", 0) or 0),
                    float(r.get("ts", 0.0) or 0.0),
                    payload,
                ))
            except Exception:
                # 单条坏数据不拖垮整批（与 trace.py 的「不丢事实」原则一致）
                continue
        if not params:
            return 0
        with self._write() as conn:
            before = conn.total_changes
            conn.executemany(
                "INSERT INTO agent_event (session_id, kind, turn, ts, payload) "
                "VALUES (?,?,?,?,?)",
                params,
            )
            return conn.total_changes - before

    def query_agent_events(self, session_id: str = "", kind: str = "",
                           limit: int = 0) -> list[dict]:
        """按会话/类别读事件，按 (turn, ts, id) 排序。

        排序的意义：Attribution 的信号判定依赖时序（例如「压缩**之后**又
        重读了压缩前读过的文件」）。乱序会让这类信号失效或反向。
        `id` 作为最后一级排序键保证同一 turn 内的插入顺序稳定。
        """
        sql = "SELECT session_id, kind, turn, ts, payload FROM agent_event WHERE 1=1"
        args: list[Any] = []
        if session_id:
            sql += " AND session_id = ?"
            args.append(session_id)
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        sql += " ORDER BY turn ASC, ts ASC, id ASC"
        if limit and limit > 0:
            sql += " LIMIT ?"
            args.append(int(limit))
        with self._read() as conn:
            rows = conn.execute(sql, tuple(args)).fetchall()
        out: list[dict] = []
        for r in rows:
            try:
                payload = json.loads(r["payload"] if not isinstance(r, tuple) else r[4])
            except Exception:
                payload = {}
            if isinstance(r, tuple):
                out.append({"session_id": r[0], "kind": r[1], "turn": r[2],
                            "ts": r[3], "payload": payload})
            else:
                out.append({"session_id": r["session_id"], "kind": r["kind"],
                            "turn": r["turn"], "ts": r["ts"], "payload": payload})
        return out

    def count_agent_events(self, session_id: str = "") -> int:
        """事件条数 —— 供 Gate 6 判断「信号覆盖是否充分」。"""
        sql = "SELECT COUNT(*) FROM agent_event"
        args: tuple = ()
        if session_id:
            sql += " WHERE session_id = ?"
            args = (session_id,)
        with self._read() as conn:
            return int(conn.execute(sql, args).fetchone()[0])
