"""`necessity attribution report` —— Failure Attribution 的离线入口。

INTEGRATION.md §1.3：归因是**离线分析**，不需要宿主循环，所以 CLI 是它
最自然的集成路径（与 MCP 并列）。

⚠️ 证据不是可选项：
    ATTRIBUTION.md §3.3 的不变式是「primary 非 unknown ⇒ evidence 必须非空」
    —— 没有证据的归因等于猜。CLI 必须把 evidence 打出来（默认文本也打），
    否则使用者只看到一个类别名，无法判断这是结论还是噪声。

⚠️ 轨迹读取的现实限制（诚实说明）：
    设计上轨迹走 `core/index/store.py` 的 SQLite，但当前 `Store` **尚未实现**
    `append_agent_events` / `query_agent_events`（只有 trace.py 按鸭子类型调用）。
    为了让离线命令现在就能用，本模块自带一个最小适配器 `TraceDB`：
      - 若 Store 已实现这两个方法，直接用（未来兼容，无需改本文件）；
      - 否则用 CLI 自己的 `agent_events` 表读写。
    这是一处已知的接缝 —— 属于 core，不能在本层修（硬约束）。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

# CLI 自带的轨迹表（Store 补齐 append/query_agent_events 后此表可弃用）
# ⚠️ 曾有本模块自带的一张 `agent_events`（复数）表，与 core 的
# `agent_event`（单数）**并存** —— 两条写入路径互不可见，
# 表现为「写进去读不回来」。与之前的符号 id 两套方案是同一类缺陷。
# 现统一委托 core/index/store.py 的 Store（唯一真源）。
class TraceDB:
    """core Store 的薄委托 —— 保留本名字以免扩大改动面。

    Store 现在是轨迹存储的唯一实现；本类只把「路径字符串」的构造签名
    适配成 Store 的调用方式，不再持有自己的 schema。
    """

    def __init__(self, path: str):
        from backend.necessity.index.store import Store

        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._store = Store(self.path)

    def append_agent_events(self, rows: list[dict]) -> int:
        return self._store.append_agent_events(rows)

    def query_agent_events(self, session_id: str = "", kind: str = "") -> list[dict]:
        return self._store.query_agent_events(session_id=session_id, kind=kind)

    def close(self) -> None:
        try:
            self._store.close()
        except Exception:
            pass


def open_trace(db_path: str):
    """优先用 Store（若已实现轨迹方法），否则用本层适配器。"""
    if db_path != ":memory:" and not Path(db_path).exists():
        return None
    try:
        from backend.necessity.index.store import Store

        s = Store(db_path)
        if hasattr(s, "query_agent_events"):
            return s
    except Exception:
        pass
    return TraceDB(db_path)


def cmd_attribution_report(args) -> int:
    from backend.necessity.attribution import FailureClassifier, check_signal_coverage
    from backend.necessity.index.trace import TraceStore

    db = open_trace(args.trace_db)
    if db is None:
        print(f"轨迹库不存在: {args.trace_db}", file=sys.stderr)
        print("提示：轨迹库是一个 SQLite 文件（trace 采集落库的产物）。"
              "若还没跑过基线，先按 INTEGRATION.md §9 的 I2→I3 采集。", file=sys.stderr)
        return 4

    trace = TraceStore(db=db, flush_every=1)
    events = trace.events(session_id=args.session)

    # 用一个「失败的 result」承载会话元数据。CLI 没有 TaskResult 对象，
    # 但 classifier 只依赖 ok / turns / diff_stats 三个属性（鸭子类型）。
    result = _FailedAttempt(turns=args.turns)

    classifier = FailureClassifier(trace=trace, llm=None)
    res = classifier.attribute(result, task={}, session_id=args.session)
    if res is None:  # attribute 不会对失败返回 None，防御一下
        print("未能归因（classifier 返回 None）", file=sys.stderr)
        return 1

    coverage = None
    if args.gate6:
        # 只用这一个归因结果算 unknown 占比；单点可能偏高，
        # 但 missing_event_kinds 部分与样本量无关，仍能暴露埋点缺口。
        coverage = check_signal_coverage([res], trace=trace)

    if args.json:
        out = {"events": len(events), "attribution": res.as_dict()}
        if coverage is not None:
            out["gate6"] = coverage.as_dict()
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        _print_report(args, res, events, coverage)

    # 有具体归因（非 unknown）→ 0；unknown / 无轨迹 → 1（未得到结论）
    return 0 if res.primary != "unknown" else 1


class _FailedAttempt:
    """最小 TaskResult 替身 —— 只为满足 classifier 的属性访问。"""

    def __init__(self, turns: int = 0):
        self.ok = False
        self.turns = turns
        self.diff_stats: dict = {}


def _print_report(args, res, events, coverage) -> None:
    from backend.necessity.attribution import improvement_for
    from backend.necessity.attribution.taxonomy import describe

    print(f"会话: {args.session}")
    print(f"轨迹事件: {len(events)} 条")
    print(f"归因: {res.primary}  (method={res.method}, confidence={res.confidence:.2f})")
    try:
        print(f"说明: {describe(res.primary)}")
    except Exception:
        pass
    print(f"Agent 责任: {'是' if res.agent_fault else '否'}")
    if res.contributing:
        print(f"并发因素: {', '.join(res.contributing)}")
    print(f"改进方向: {res.improvement or improvement_for(res.primary)}")

    # §3.3：证据必须可见 —— 这是「结论」与「猜」的分界
    print()
    print(f"证据 ({len(res.evidence)} 条):")
    if not res.evidence:
        print("  （无 —— unknown 允许无证据；其他类别不应为空）")
    for e in res.evidence:
        print(f"  - [turn {e.get('turn', '?')}] {e.get('signal')}: {e.get('detail')}")
    if res.note:
        print(f"\n备注: {res.note}")
    if res.conflict:
        print(f"冲突: {res.conflict}")

    if coverage is not None:
        print()
        print("Gate 6 信号覆盖检查:")
        print(f"  unknown 占比: {coverage.unknown_ratio:.0%} "
              f"(阈值 40%，超过则判为埋点不足)")
        print(f"  判定: {'不足 —— 需补埋点' if coverage.insufficient else '充分'}")
        if coverage.missing_event_kinds:
            print(f"  从未采集的事件类别: {', '.join(coverage.missing_event_kinds)}")
        print(f"  {coverage.note}")


def add_parser(top) -> None:
    a = top.add_parser("attribution", help="Failure Attribution（离线归因，能力 4）")
    asub = a.add_subparsers(dest="cmd", required=True)

    r = asub.add_parser("report", help="从轨迹库归因一个失败会话")
    r.add_argument("trace_db", help="轨迹 SQLite 库路径")
    r.add_argument("--session", required=True, help="会话 id")
    r.add_argument("--turns", type=int, default=0,
                   help="该会话总轮次（影响 planning/capability 类信号）")
    r.add_argument("--gate6", action="store_true",
                   help="附带 §6/Gate-6 信号覆盖检查（unknown>40% → 埋点不足）")
    r.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    r.set_defaults(fn=cmd_attribution_report)
