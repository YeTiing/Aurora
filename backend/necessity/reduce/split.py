"""改动切分 + 一致性组构造。

对应 DIFF_REDUCER.md §3 与 §5.1/§5.2。

一致性组是本设计**最容易做错的地方**（文档原话）。朴素做法按 hunk 独立
回退会遇到耦合改动必然误判：

    hunk 1: 改 def parse(text, strict=False)       ← 改签名
    hunk 2: 调用点 parse(t) → parse(t, strict=True) ← 跟着改

单独回退 hunk 1 → 调用点仍传 strict=True → 类型检查报错 → 测试失败 →
算法判定「hunk 1 必要」→ **误判**。实际上它们是原子单元。

⚠️ 文档还特意记录了早期算法的错误：对**每对**组做可编译性检查，
n=60 hunks → ~1800 次**秒级**类型检查 → 约 30 分钟，且与「廉价剪枝」
自相矛盾。本模块实现的是修正后的**四阶段传播**：

    阶段 1 符号归属     hunk → 触及的符号（按行范围匹配）        毫秒级
    阶段 2 调用边传播   改签名 + 改调用点 → 并入同组（关键！）   毫秒级
    阶段 3 导入边传播   改模块导出 + 改导入方 → 并入同组         毫秒级
    阶段 4 分组校验     每组 1 次类型检查（不是两两）            秒级 × 组数

复杂度 O(n) 图查询 + O(m) 类型检查，m ≪ n。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

# unified diff 的 hunk 头：@@ -old_start,old_count +new_start,new_count @@
_HUNK_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)
_FILE_RE = re.compile(r"^diff --git a/(?P<a>\S+) b/(?P<b>\S+)$")
_OLD_FILE_RE = re.compile(r"^--- (?:a/)?(?P<p>\S+)")
_NEW_FILE_RE = re.compile(r"^\+\+\+ (?:b/)?(?P<p>\S+)")


@dataclass
class Hunk:
    """一个改动块（`@@` 分隔）。

    行号用 new 侧（改动后的位置）—— 符号表也是按改动后的文件建的。
    """
    id: str
    file: str
    old_start: int
    new_start: int
    old_count: int
    new_count: int
    added: int = 0
    removed: int = 0
    is_new_file: bool = False
    body: list[str] = field(default_factory=list)

    @property
    def line_range(self) -> tuple[int, int]:
        """new 侧影响的半开区间 [start, end)。"""
        end = self.new_start + max(self.new_count, 1) - 1
        return (self.new_start - 1, max(end, self.new_start - 1))


@dataclass
class DiffFile:
    path: str
    hunks: list[Hunk] = field(default_factory=list)
    is_new: bool = False
    is_deleted: bool = False


def parse_unified_diff(text: str) -> list[DiffFile]:
    """把 unified diff 切成按文件分组的 hunk 列表。

    只处理标准 unified 格式（`diff --git` / `---` / `+++` / `@@`）。
    Git 扩展头（rename from/to、mode change）被忽略但不影响切分。
    """
    files: list[DiffFile] = []
    cur: DiffFile | None = None
    cur_hunk: Hunk | None = None
    counting = False

    for raw in (text or "").splitlines():
        m = _FILE_RE.match(raw)
        if m:
            cur = DiffFile(path=m.group("b"))
            files.append(cur)
            cur_hunk = None
            counting = False
            continue

        if raw.startswith("--- "):
            mo = _OLD_FILE_RE.match(raw)
            # 新文件：old 侧是 /dev/null
            if cur is not None and mo and mo.group("p") == "/dev/null":
                cur.is_new = True
            continue

        if raw.startswith("+++ "):
            mn = _NEW_FILE_RE.match(raw)
            if cur is None:
                cur = DiffFile(path=(mn.group("p") if mn else "?"))
                files.append(cur)
            elif mn and mn.group("p") == "/dev/null":
                cur.is_deleted = True
            elif mn and cur.path in ("?", ""):
                cur.path = mn.group("p")
            continue

        hm = _HUNK_RE.match(raw)
        if hm:
            if cur is None:
                continue
            cur_hunk = Hunk(
                id=f"h{len(files)}-{len(cur.hunks)}",
                file=cur.path,
                old_start=int(hm.group("old_start")),
                new_start=int(hm.group("new_start")),
                old_count=int(hm.group("old_count") or 1),
                new_count=int(hm.group("new_count") or 1),
                is_new_file=cur.is_new,
            )
            cur.hunks.append(cur_hunk)
            counting = True
            continue

        if counting and cur_hunk is not None:
            # 保存原文：反向应用（revert）需要按行重建补丁
            cur_hunk.body.append(raw)
            if raw.startswith("+") and not raw.startswith("+++"):
                cur_hunk.added += 1
            elif raw.startswith("-") and not raw.startswith("---"):
                cur_hunk.removed += 1

    return [f for f in files if f.hunks]


def all_hunks(files: list[DiffFile]) -> list[Hunk]:
    return [h for f in files for h in f.hunks]


# ── 一致性组构造（§5.2 的四阶段）──────────────────────────────────

def split_large_hunk(hunk: Hunk, max_lines: int = 50) -> list[Hunk]:
    """hunk 过大时向下切分到符号级（§5.1）。

    首版只做等分切分 —— 真正的符号级切分需要 AST，而 §5.2 的符号归属
    已经在分组阶段处理了「哪些 hunk 属于同一符号」。等分足以让最小化
    粒度不至于过粗，且不引入 AST 依赖。
    """
    if hunk.added + hunk.removed <= max_lines:
        return [hunk]
    parts: list[Hunk] = []
    n = max(2, (hunk.added + hunk.removed + max_lines - 1) // max_lines)
    span = max(1, hunk.new_count // n)
    for k in range(n):
        parts.append(Hunk(
            id=f"{hunk.id}.{k}", file=hunk.file,
            old_start=hunk.old_start + k * span,
            new_start=hunk.new_start + k * span,
            old_count=span, new_count=span,
            is_new_file=hunk.is_new_file,
        ))
    return parts


# 一致性组构造已拆到 groups.py（split.py 只负责解析）。
from .groups import (  # noqa: E402,F401
    CoherenceGroups,
    build_coherence_groups,
)

# patch 应用（正向/反向）已拆到 apply.py —— split.py 只负责解析与分组。
# 这里转发导出，保持既有调用点（含测试）不需要改 import 路径。
from .apply import (  # noqa: E402,F401
    apply_text_patch,
    hunk_header,
    reverse_hunk_lines,
)
