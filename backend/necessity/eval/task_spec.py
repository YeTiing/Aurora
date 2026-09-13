"""任务元数据契约 —— TaskSpec。

从 records.py 拆出（该文件超 300 行上限）。职责上也该分：
    records.py  放**运行记录**（Attempt / GateMetrics）—— runner 产出
    task_spec.py 放**任务定义**（TaskSpec）—— taskset 提供

两者生命周期不同：Attempt 每次跑都新建，TaskSpec 是静态配置。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TaskSpec:
    """任务集里一个任务的元数据（meta.json）。

    对齐 EVAL.md §1.4 与 INDEX.md Phase 3 的目录结构：
        tasks/<id>/{repo/, task.md, tests/, meta.json, ground_truth.diff}
    """
    task_id: str = ""
    category: str = ""               # A 基线 / B 区分性 / C 压力
    difficulty: str = ""             # easy / medium / hard
    title: str = ""
    files_touched: int = 0
    expected_callers: int = 0
    # B 类必备：仓库里必须存在同名干扰符号（INDEX.md Phase 3 的硬性要求）
    decoy_symbols: list[str] = field(default_factory=list)
    # 约束（Guard 用；诱导越界任务靠它）
    constraints: list[str] = field(default_factory=list)
    min_turns: int = 0
    min_lines_changed: int = 0
    min_files: int = 0
    source: str = ""                 # 来源 A=真实 commit / C=自造
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id, "category": self.category,
            "difficulty": self.difficulty, "title": self.title,
            "files_touched": self.files_touched,
            "expected_callers": self.expected_callers,
            "decoy_symbols": self.decoy_symbols,
            "constraints": self.constraints,
            "min_turns": self.min_turns,
            "min_lines_changed": self.min_lines_changed,
            "min_files": self.min_files,
            "source": self.source,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TaskSpec":
        return cls(
            task_id=d.get("task_id", ""), category=d.get("category", ""),
            difficulty=d.get("difficulty", ""), title=d.get("title", ""),
            files_touched=int(d.get("files_touched", 0)),
            expected_callers=int(d.get("expected_callers", 0)),
            decoy_symbols=list(d.get("decoy_symbols") or []),
            constraints=list(d.get("constraints") or []),
            min_turns=int(d.get("min_turns", 0)),
            min_lines_changed=int(d.get("min_lines_changed", 0)),
            min_files=int(d.get("min_files", 0)),
            source=d.get("source", ""), meta=d.get("meta") or {},
        )

    def validate(self) -> list[str]:
        """校验任务定义。返回问题列表（空 = 合格）。

        B 类的硬性要求单独检查：INDEX.md Phase 3 原话
        「每个 B 类任务的仓库里，必须存在一个与被改符号同名的干扰符号。
          如果造不出这个条件，这个任务不算 B 类。这是实验能否成立的关键。」
        """
        problems: list[str] = []
        if not self.task_id:
            problems.append("task_id 为空")
        if self.category not in ("A", "B", "C"):
            problems.append(f"category 必须是 A/B/C，实际 {self.category!r}")
        if self.category == "B" and not self.decoy_symbols:
            problems.append("B 类任务必须声明 decoy_symbols（同名干扰符号）")
        # C 类其实有**两种**（EVAL.md §1.3 的两个补充场景）：
        #   诱导越界 —— 必须有约束，否则测不出「会不会越界」
        #   高冗余   —— 不需要约束，它观测的是「顺手改动」的量
        # 用 meta.subtype 区分；未标注时按名字推断，避免把高冗余误判为无效。
        if self.category == "C" and not self.constraints:
            subtype = str((self.meta or {}).get("subtype", "")).lower()
            is_redundancy = ("redundancy" in subtype
                             or "redundancy" in (self.task_id or "").lower())
            if not is_redundancy:
                problems.append(
                    "C 类（诱导越界子类）任务必须声明 constraints；"
                    "若是高冗余子类请在 meta.subtype 标注 'redundancy'"
                )
        if self.difficulty not in ("easy", "medium", "hard", ""):
            problems.append(f"difficulty 取值异常: {self.difficulty!r}")
        return problems
