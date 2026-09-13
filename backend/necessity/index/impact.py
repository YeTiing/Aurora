"""impact.py —— 影响面查询。

回答一个问题：**「我要改这个函数，会炸谁？」**

对应 INDEX.md Phase 2 的接口：
    impact_analysis(symbol_ref, depth=2) -> {
        callers: [{file, line, symbol, distance}],   # distance=1 是直接调用方
        callees: [...],
        tests:   [{file, test_name}],                # 按命名/目录约定识别
        risk:    "high" | "medium" | "low",
    }

为什么按「图距离」分层而不是一锅端（INDEX.md §3.3）：
    直接调用方远比间接调用方重要。无差别塞进上下文会浪费 token 预算，
    而 Context Paging 的价值就在于把预算花在真正相关的东西上。

风险判定的依据（INDEX.md Phase 2 原文）：
    「基于调用方数量与是否有测试」。有测试 = 风险可控（改坏了测试会报），
    无测试 + 调用方多 = 高风险（改坏了没人发现）。

降级：没有调用图时（LSP 不可用）返回空结果 + `degraded=True`，
**不是抛异常** —— INTEGRATION.md §8.2 要求任何降级都不得让 Agent 无法工作。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# 测试文件识别（INDEX.md Phase 2：「按命名/目录约定识别」）
_TEST_PATH_RE = re.compile(r"(^|[/\\])(tests?|testing)[/\\]|(^|[/\\])test_[^/\\]+\.py$|_test\.py$")
_TEST_FUNC_RE = re.compile(r"^(test_|Test)")

RISK_HIGH = "high"
RISK_MEDIUM = "medium"
RISK_LOW = "low"


@dataclass
class ImpactNode:
    """影响面里的一个节点。"""
    file: str
    line: int = 0
    symbol: str = ""
    distance: int = 1

    def to_dict(self) -> dict:
        return {"file": self.file, "line": self.line,
                "symbol": self.symbol, "distance": self.distance}


@dataclass
class Impact:
    """影响面分析结果。"""
    symbol: str = ""
    callers: list[ImpactNode] = field(default_factory=list)
    callees: list[ImpactNode] = field(default_factory=list)
    tests: list[dict] = field(default_factory=list)
    risk: str = RISK_LOW
    degraded: bool = False
    note: str = ""

    @property
    def direct_callers(self) -> list[ImpactNode]:
        return [c for c in self.callers if c.distance == 1]

    @property
    def counts(self) -> dict:
        return {
            "direct_callers": len(self.direct_callers),
            "total_callers": len(self.callers),
            "callees": len(self.callees),
            "tests": len(self.tests),
        }

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "callers": [c.to_dict() for c in self.callers],
            "callees": [c.to_dict() for c in self.callees],
            "tests": self.tests,
            "risk": self.risk,
            "degraded": self.degraded,
            "note": self.note,
            # 便于调用方直接判断规模，无需自己遍历
            "counts": self.counts,
        }


def is_test_file(path: str) -> bool:
    return bool(_TEST_PATH_RE.search(path or ""))


def is_test_symbol(name: str) -> bool:
    return bool(_TEST_FUNC_RE.match(name or ""))


def compute_risk(direct_callers: int, has_tests: bool,
                 total_callers: int = 0) -> tuple[str, str]:
    """风险判定。返回 (risk, 理由)。

    规则（INDEX.md Phase 2 给了依据，此处把它具体化）：
      - 无调用方            → low（改了没人受影响；但注意可能是死代码）
      - 有测试覆盖          → medium（改坏了测试会报，风险可控）
      - 调用方 > 5 且无测试 → high（改坏了没人发现，且面大）
      - 其余                → medium
    """
    if direct_callers == 0 and total_callers == 0:
        return RISK_LOW, "无调用方"
    if has_tests:
        return RISK_MEDIUM, f"{total_callers} 个调用方，但有测试覆盖"
    if direct_callers > 5:
        return RISK_HIGH, f"{direct_callers} 个直接调用方且无测试覆盖"
    return RISK_MEDIUM, f"{total_callers} 个调用方，无测试覆盖"


def _query(store, base: str, node_id: str) -> list[dict]:
    """调用 store 的查询方法，兼容两种命名。

    真实 Store 的方法名是 `callers` / `callees`（core/index/store.py）；
    早期设计稿与部分测试替身用 `callers_of` / `callees_of`。
    这里做一次适配，避免把命名差异泄漏到调用方。
    """
    fn = getattr(store, base, None) or getattr(store, base + "_of", None)
    if fn is None:
        raise AttributeError(f"store 未提供 {base}/{base}_of 查询方法")
    return fn(node_id) or []


def _bfs(store, start_id: str, depth: int, direction: str) -> list[ImpactNode]:
    """在边上做 BFS，返回距离 1..depth 的节点。

    direction:
      "callers" —— 沿 dst_id 向上找 src（谁调用我）
      "callees" —— 沿 src_id 向下找 dst（我调用谁）

    为什么自己做 BFS 而不是递归 SQL：需要按距离分层有序输出，
    且要防环（A→B→A 会让递归不终止）。
    """
    seen: set[str] = {start_id}
    frontier = [start_id]
    out: list[ImpactNode] = []

    for dist in range(1, max(1, depth) + 1):
        nxt: list[str] = []
        for node_id in frontier:
            # 不在这里吞异常：静默返回部分结果会让调用方以为「影响面就这么大」，
            # 从而漏改。必须向上抛，由 analyze 转成显式的 degraded 标记。
            if direction == "callers":
                # 谁调用我：边是 (src -> dst=node_id)，对手方在 src_id
                rows = _query(store, "callers", node_id)
                other_key = "src_id"
            else:
                # 我调用谁：边是 (src=node_id -> dst)，对手方在 dst_id
                rows = _query(store, "callees", node_id)
                other_key = "dst_id"
            for r in rows or []:
                other = r.get(other_key)
                if not other or other in seen:
                    continue
                seen.add(other)
                out.append(ImpactNode(
                    file=str(r.get("file") or ""),
                    line=int(r.get("line") or 0),
                    symbol=str(other),
                    distance=dist,
                ))
                nxt.append(other)
        frontier = nxt
        if not frontier:
            break
    return out


def analyze(store, symbol_id: str, depth: int = 2) -> Impact:
    """影响面分析。store 为 None 时返回降级结果（不抛异常）。"""
    if store is None:
        return Impact(
            symbol=symbol_id, degraded=True,
            note="无调用图可用（LSP 不可用或未建图），影响面未知；"
                 "此时应保守处理：改动前自行确认调用点。",
        )
    if not symbol_id:
        return Impact(degraded=True, note="未提供符号 id")

    try:
        callers = _bfs(store, symbol_id, depth, "callers")
        callees = _bfs(store, symbol_id, depth, "callees")
    except Exception as e:
        return Impact(symbol=symbol_id, degraded=True,
                      note=f"查询失败，影响面未知: {type(e).__name__}: {e}")

    # 测试识别：调用方里落在测试文件、或名字像测试的
    tests: list[dict] = []
    seen_tests: set[tuple[str, str]] = set()
    for c in callers:
        f = c.file
        name = c.symbol.split("::")[-1].split(".")[-1]
        if is_test_file(f) or is_test_symbol(name):
            key = (f, name)
            if key not in seen_tests:
                seen_tests.add(key)
                tests.append({"file": f, "test_name": name})

    direct = sum(1 for c in callers if c.distance == 1)
    risk, reason = compute_risk(direct, bool(tests), len(callers))

    note = reason
    if not tests:
        note += "；无测试覆盖，改坏了不会被自动发现"
    # 只返回 depth 内的，且已经在 _bfs 里限制了
    return Impact(symbol=symbol_id, callers=callers, callees=callees,
                  tests=tests, risk=risk, note=note)


def symbol_test_coverage_hint(store, symbol_ids: list[str]) -> dict[str, bool]:
    """批量判断「这些符号有没有被测试触及」。

    用途：Diff Reducer 选测试范围（DIFF_REDUCER.md §6.1）——
    只跑受影响符号相关的测试，把单次判定从 30s 压到 2s。
    """
    out: dict[str, bool] = {}
    for sid in symbol_ids or []:
        try:
            callers = _query(store, 'callers', sid)
            out[sid] = any(
                is_test_file(str(c.get("file") or "")) for c in callers
            )
        except Exception:
            out[sid] = False
    return out
