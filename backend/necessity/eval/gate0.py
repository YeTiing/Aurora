"""Gate 0 —— 基线可行性测量（3 天内决定要不要做 Context Paging）。

EVAL.md §4：
    重复读取率 > 15%  → ✅ 继续 S2（Context Paging）
    重复读取率 5~15%  → ⚠️ 边际：缩小 S2 范围，或跳到 S4
    重复读取率 < 5%   → ❌ 停止 Context Paging，省下 2 周

这是整份设计里性价比最高的检查：**唯一能在 3 天内否掉 2 周工作的 gate**。

指标精确定义（EVAL.md §2.4，不能含糊）：
    R        = read_file 完整读取的次数；recall 按符号取**不计入**
    R_waste  = 状态表已有最新内容且 fresh 时的读取
    ΣR_waste / ΣR  = 重复读取率

边界（CONTEXT_PAGING.md §2.3，必须先划清否则优化会破坏正确性）：
    合法重读 ✅  编辑后验证 / 文件被外部修改 / 内容过期
    浪费重读 ❌  压缩后遗忘 / 同一轮内重复读 / 多子 Agent 各读一遍

本脚本只做**测量**，不做优化。它读 trace 的事实数据，按上述定义算比率。
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.necessity.index.trace import AgentEvent, TraceStore  # noqa: E402

# Gate 判据阈值（EVAL.md §4 Gate 0）
THRESHOLD_GO = 0.15
THRESHOLD_MARGINAL = 0.05


@dataclass
class Gate0Result:
    total_reads: int = 0          # R
    waste_reads: int = 0          # R_waste
    legit_reads: int = 0
    per_file: dict[str, dict] = field(default_factory=dict)
    compactions: int = 0
    read_after_compaction: int = 0

    @property
    def waste_ratio(self) -> float:
        return self.waste_reads / self.total_reads if self.total_reads else 0.0

    def verdict(self) -> tuple[str, str]:
        """返回 (判定, 行动建议)。"""
        r = self.waste_ratio
        if r > THRESHOLD_GO:
            return "PASS", "重复读取率 > 15% —— 继续 S2（Context Paging）"
        if r >= THRESHOLD_MARGINAL:
            return "MARGINAL", "5%~15% —— 缩小 S2 范围，或跳到 S4（Diff Reducer）"
        return "FAIL", "重复读取率 < 5% —— 停止 Context Paging，省下 2 周"

    def to_dict(self) -> dict:
        verdict, action = self.verdict()
        return {
            "total_reads": self.total_reads,
            "waste_reads": self.waste_reads,
            "legit_reads": self.legit_reads,
            "waste_ratio": round(self.waste_ratio, 4),
            "compactions": self.compactions,
            "reads_after_compaction": self.read_after_compaction,
            "verdict": verdict,
            "action": action,
            "per_file": self.per_file,
        }


def classify_reads(events: list[AgentEvent]) -> Gate0Result:
    """按 CONTEXT_PAGING.md §2.3 的边界分类每次读取。

    判定「浪费」的充分条件（按优先级，任一条成立即为浪费）：
      1. 同一轮内重复读同一文件
      2. 压缩之后又读了压缩前已读过、且此后未被修改的文件（遗忘式重读）

    反之为「合法」：
      - 文件在两次读之间被写过（验证改动）
      - 文件被外部修改过（writer != agent）
      - 首次读
    """
    res = Gate0Result()
    last_read_turn: dict[str, int] = {}
    last_read_idx: dict[str, int] = {}
    read_count: dict[str, int] = {}
    write_turns: dict[str, set[int]] = {}
    external_write: set[str] = set()
    compaction_turns: list[int] = []

    # 单遍扫描：事件本身有序（采集时按发生顺序 append）
    for idx, e in enumerate(events):
        p = e.payload or {}
        path = str(p.get("path") or "")
        if e.kind == "compaction":
            res.compactions += 1
            compaction_turns.append(e.turn)
            continue
        if e.kind == "file_write":
            write_turns.setdefault(path, set()).add(e.turn)
            if str(p.get("writer") or "agent") != "agent":
                external_write.add(path)
            continue
        if e.kind != "file_read":
            continue

        res.total_reads += 1
        read_count[path] = read_count.get(path, 0) + 1
        first = path not in last_read_turn

        wasted = False
        reason = ""
        if not first:
            # 条件 1：同一轮内重复读
            if last_read_turn[path] == e.turn:
                wasted, reason = True, "same_turn_reread"
            else:
                # 条件 2：压缩后的遗忘式重读
                #   两次读之间：文件没被写过，且中间发生过压缩
                between_writes = [
                    t for t in write_turns.get(path, set())
                    if last_read_turn[path] <= t <= e.turn
                ]
                compacted_between = any(
                    last_read_turn[path] < ct <= e.turn for ct in compaction_turns
                )
                if not between_writes and path not in external_write and compacted_between:
                    wasted, reason = True, "forgotten_after_compaction"

        if wasted:
            res.waste_reads += 1
        else:
            res.legit_reads += 1

        info = res.per_file.setdefault(
            path or "<unknown>",
            {"reads": 0, "waste": 0, "legit": 0, "reasons": []},
        )
        info["reads"] += 1
        info["waste" if wasted else "legit"] += 1
        if wasted:
            info["reasons"].append(reason)

        last_read_turn[path] = e.turn
        last_read_idx[path] = idx

    res.read_after_compaction = sum(
        1 for p, info in res.per_file.items()
        if "forgotten_after_compaction" in info["reasons"]
    )
    return res


def run_gate0(trace: TraceStore | None = None, session_id: str = "") -> Gate0Result:
    t = trace or TraceStore()
    events = t.events(session_id=session_id)
    return classify_reads(events)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    session_id = argv[0] if argv else ""

    t = TraceStore()
    events = t.events(session_id=session_id)

    if not events:
        print("Gate 0: 没有采集到任何轨迹事件。")
        print()
        print("这意味着埋点未生效或尚未跑基线。Gate 0 的前提是先在 Aurora 上")
        print("跑若干真实任务并采集轨迹（INTEGRATION.md §9 的 I2 -> I3）。")
        print("缺失的事件类别:", TraceStore().missing_event_kinds())
        return 2

    res = run_gate0(t, session_id)
    print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
    verdict, action = res.verdict()
    print()
    print(f"Gate 0 判定: {verdict}")
    print(f"行动: {action}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
