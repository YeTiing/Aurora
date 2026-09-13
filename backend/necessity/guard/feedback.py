"""feedback.py —— 违反反馈措辞与合并（GUARD.md §7.2 / §7.3）。

**这段文字本身就是防约束衰减的手段**（§7.2）：Agent 不知道自己跑偏时
不会自我纠正，明确告诉它「违反了哪一条、为什么、已如何处理」就能拉回来。
因此格式是产品的一部分，不是日志，单独立文件而不是散在 interceptor 里。

§7.3 的反效果风险：反馈太频繁会挤占上下文。缓解是**同约束连续违反
合并**而非逐次输出 —— `FeedbackLedger` 负责计数与合并。
"""
from __future__ import annotations

from .checker import Violation

__all__ = ["format_violation", "FeedbackLedger"]

_ROLLED_OK = ("restored", "restored_absent")


def format_violation(v: Violation, *, blocked: bool = False, merged: int = 0,
                     rolled: list[dict] | None = None) -> str:
    """按 §7.2 的模板生成反馈文本。"""
    lines = [f"✗ 约束违反 [{v.constraint_id}: {v.ctype}]"]
    if v.paths:
        shown = ", ".join(v.paths[:5])
        more = f" 等 {len(v.paths)} 个" if len(v.paths) > 5 else ""
        lines.append(f"  你修改了 {shown}{more}")
    lines.append(f"  但本任务约束为：{v.message}")
    if v.detail:
        lines.append(f"  细节：{v.detail}")
    if blocked:
        lines.append("  该调用已被拦截，未执行。")
    ok = [a for a in (rolled or []) if a.get("status") in _ROLLED_OK]
    if ok:
        lines.append(f"  该改动已回滚（{len(ok)} 个文件，{v.lines} 行）")
    fail = [a for a in (rolled or []) if a.get("status") == "escalated"]
    if fail:
        lines.append(f"  ⚠ {len(fail)} 个文件回滚失败，已转人工："
                     + ", ".join(str(a.get("path")) for a in fail[:3]))
    if merged > 1:
        lines.append(f"  （同一约束已连续违反 {merged} 次，此处合并反馈）")
    lines.append("  提示：如需修改该文件，请先确认任务范围是否应扩大。")
    return "\n".join(lines)


class FeedbackLedger:
    """同约束违反计数 + 反馈留存，供 on_task_end 汇总。"""

    def __init__(self, keep: int = 5):
        self.keep = max(1, int(keep))
        self._counts: dict[str, int] = {}
        self.messages: list[str] = []

    def reset(self) -> None:
        self._counts.clear()
        self.messages.clear()

    def record(self, v: Violation, *, blocked: bool = False,
               rolled: list[dict] | None = None) -> tuple[str, int]:
        """登记一次违反，返回 (应发给 Agent 的文本, 该约束累计次数)。

        连续违反时合并反馈（§7.3）：文本里带累计次数，而不是重复刷屏。
        """
        count = self._counts.get(v.constraint_id, 0) + 1
        self._counts[v.constraint_id] = count
        text = format_violation(v, blocked=blocked, merged=count, rolled=rolled)
        self.messages.append(text)
        return text, count

    def count(self, constraint_id: str) -> int:
        return self._counts.get(constraint_id, 0)

    def recent(self, n: int | None = None) -> list[str]:
        return self.messages[-(n or self.keep):]
