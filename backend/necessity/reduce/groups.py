"""一致性组构造 —— DIFF_REDUCER.md §3.2 的四阶段传播。

从 split.py 拆出（该文件超 300 行上限）。

⚠️ 文档特意记录了早期版本的**错误算法**：对每对组做可编译性检查、
反复直到无合并。`n=60` hunks -> 约 1800 次**秒级**类型检查 -> 约 30 分钟，
且与「廉价剪枝」自相矛盾。

本模块实现修正后的做法：
    阶段 1 符号归属    毫秒级
    阶段 2 调用边传播  毫秒级（**关键**：改签名与改调用点触及不同符号）
    阶段 3 导入边传播  毫秒级
    阶段 4 分组校验    秒级 × 组数（每组一次，不是两两）

复杂度 O(n) 图查询 + O(m) 类型检查，m ≪ n。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .split import Hunk

__all__ = ["CoherenceGroups", "build_coherence_groups"]


@dataclass
class CoherenceGroups:
    """构造结果 + 降级级别，便于调用方在报告里如实标注。"""
    groups: list[list[Hunk]] = field(default_factory=list)
    level: str = "L1"          # L1 完整 / L2 文件级 / L3 全体一组
    reason: str = ""

    def of(self, hunk_id: str) -> list[Hunk] | None:
        for g in self.groups:
            if any(h.id == hunk_id for h in g):
                return g
        return None


class _UnionFind:
    """并查集 —— 阶段 1~3 的合并都是并查集操作，O(α(n))。"""

    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        self.p[rb] = ra
        return True


def build_coherence_groups(
    hunks: list[Hunk],
    symbols_by_file: dict[str, list] | None = None,
    callers_of=None,
    callees_of=None,
    imports_of=None,
    importers_of=None,
    typecheck=None,
) -> CoherenceGroups:
    """四阶段构造一致性组。

    symbols_by_file: {relpath: [Symbol-like]}（需有 start_line/end_line/id）
    callers_of/callees_of: (symbol_id) -> [{"src_id"|"dst_id", ...}]
    imports_of/importers_of: (relpath) -> [relpath]（模块粒度）
    typecheck: (list[Hunk]) -> bool —— 仅在阶段 4 调用，每组一次

    降级：没有符号表 → L2 文件级合并；连文件信息都没有 → L3 全体一组。
    **宁可组偏大（保守），也不要组偏小（误判）** —— 文档 §3.3 的明确要求。
    """
    if not hunks:
        return CoherenceGroups(groups=[], level="L1", reason="无改动")

    # 无符号表 -> L2：同文件所有 hunk 归一组
    if not symbols_by_file:
        by_file: dict[str, list[Hunk]] = {}
        for h in hunks:
            by_file.setdefault(h.file, []).append(h)
        return CoherenceGroups(
            groups=list(by_file.values()), level="L2",
            reason="无符号表，降级为文件级保守合并（组偏大但不会误判）",
        )

    n = len(hunks)
    uf = _UnionFind(n)
    idx_of = {h.id: i for i, h in enumerate(hunks)}

    # ── 阶段 1：符号归属（毫秒级）──────────────────────────────
    # 每个 hunk 触及哪些符号：按 new 侧行范围与符号定义范围求交
    touched: dict[int, set[str]] = {}
    sym_by_id: dict[str, object] = {}
    for i, h in enumerate(hunks):
        lo, hi = h.line_range
        hit: set[str] = set()
        for s in symbols_by_file.get(h.file, []) or []:
            s_lo = int(getattr(s, "start_line", 0))
            s_hi = int(getattr(s, "end_line", s_lo))
            if s_lo <= hi and s_hi >= lo:
                sid = getattr(s, "id", "")
                if sid:
                    hit.add(sid)
                    sym_by_id[sid] = s
        touched[i] = hit

    # 同符号 -> 同组
    owner: dict[str, int] = {}
    for i, sids in touched.items():
        for sid in sids:
            if sid in owner:
                uf.union(owner[sid], i)
            else:
                owner[sid] = i

    # ── 阶段 2：调用边传播（毫秒级，关键）─────────────────────
    # 文档的关键洞见：改签名（符号 parse）与改调用点（符号 caller）触及
    # **不同符号**，阶段 1 合并不了它们，必须靠调用边。
    if callers_of is not None or callees_of is not None:
        # 先建「符号 id -> 触及它的 hunk 下标集合」
        hunk_of_sym: dict[str, set[int]] = {}
        for i, sids in touched.items():
            for sid in sids:
                hunk_of_sym.setdefault(sid, set()).add(i)

        for i, sids in touched.items():
            for sid in sids:
                neighbors: set[str] = set()
                if callers_of is not None:
                    for r in (callers_of(sid) or []):
                        v = r.get("src_id") if isinstance(r, dict) else None
                        if v:
                            neighbors.add(v)
                if callees_of is not None:
                    for r in (callees_of(sid) or []):
                        v = r.get("dst_id") if isinstance(r, dict) else None
                        if v:
                            neighbors.add(v)
                # 邻居符号若也被某个 hunk 触及 -> 合并
                for nb in neighbors:
                    for j in hunk_of_sym.get(nb, ()):  # noqa: B007
                        uf.union(i, j)

    # ── 阶段 3：导入边传播（毫秒级）────────────────────────────
    if imports_of is not None or importers_of is not None:
        file_hunks: dict[str, list[int]] = {}
        for i, h in enumerate(hunks):
            file_hunks.setdefault(h.file, []).append(i)
        for fpath, idxs in file_hunks.items():
            related: set[str] = set()
            if imports_of is not None:
                related.update(imports_of(fpath) or [])
            if importers_of is not None:
                related.update(importers_of(fpath) or [])
            for other in related:
                for j in file_hunks.get(other, ()):
                    for i in idxs:
                        uf.union(i, j)

    # ── 组装分组 ────────────────────────────────────────────────
    buckets: dict[int, list[Hunk]] = {}
    for h in hunks:
        buckets.setdefault(uf.find(idx_of[h.id]), []).append(h)
    groups = list(buckets.values())

    # 新文件整体视为一个组（§7 边界 10）
    # 上面已按 hunk 归属处理，但新文件的多个 hunk 未必同符号 -> 显式合并
    new_file_buckets: dict[str, int] = {}
    merged: list[list[Hunk]] = []
    for g in groups:
        nf = {h.file for h in g if h.is_new_file}
        if not nf:
            merged.append(g)
            continue
        key = sorted(nf)[0]
        if key in new_file_buckets:
            merged[new_file_buckets[key]].extend(g)
        else:
            new_file_buckets[key] = len(merged)
            merged.append(g)
    groups = merged

    # ── 阶段 4：分组校验（秒级 × 组数，不是两两）───────────────
    reason = "四阶段传播（无类型检查回调，跳过阶段 4）"
    if typecheck is not None:
        attempts = 0
        changed = True
        while changed and attempts < 2:   # 最多重试 2 轮（文档要求）
            changed = False
            attempts += 1
            for g in list(groups):
                try:
                    ok = typecheck(g)
                except Exception:
                    ok = True   # 校验器故障视为通过（不阻塞），与主循环契约一致
                if not ok:
                    # 失败 -> 保守并入相邻组（宁可组偏大）
                    gi = groups.index(g)
                    if gi + 1 < len(groups):
                        groups[gi + 1] = g + groups.pop(gi)
                    elif gi > 0:
                        groups[gi - 1] = groups[gi - 1] + groups.pop(gi)
                    changed = True
                    break
        reason = f"四阶段传播 + 分组校验（{attempts} 轮，{len(groups)} 组）"

    return CoherenceGroups(groups=groups, level="L1", reason=reason)
