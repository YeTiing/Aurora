"""反事实实验的**前提校验** —— 隔离环境里必须真的存在「改动已应用」的状态。

## 它修的问题（实测，且完全静默）

反事实实验的语义是「从**改动已应用**的状态出发，撤销一部分，看测试是否变化」。
隔离环境（`sandbox.py`）用 `git worktree add --detach <base_commit>` 建立，
默认 `base_commit` 是 **HEAD**。于是：

    改在**已提交**的 commit 里（base 指向前态）-> worktree 里有改动 ✅ 正确
    改在**未提交的工作区**里（base=HEAD）          -> worktree 里是**旧代码** ❌

第二种情况下 `post_state`（「全量改动后」的基准）读到的本来就是旧代码，
于是「撤销某个 hunk」= 把旧代码「撤销成」旧代码 —— **恒等变换**。
无论撤销哪个子集，测试都只看到旧代码，结论恒为「全部冗余、冗余率 1.0」。

实测：一个含 `test_custom`（要求 `trunc` 支持 `suffix` 参数）的**必要**改动，
被判成「冗余率 1.0，Agent 的改动对目标无贡献」——完全相反的结论，且不报错。

## 为什么是「拒绝回答」而不是「自动猜基准」

自动猜（比如「用 HEAD~1」）在改动跨多个 commit、或与未提交改动混合时同样会错。
DIFF_REDUCER.md §7 的总原则是：

    「宁可返回未收敛的最优解，也不要返回错误结论。」

所以这里做**前提校验**：环境里找不到「改动已应用」的状态就直接拒绝，
把原因和该怎么做讲清楚 —— 而不是给一个看起来合理的错数字。
"""
from __future__ import annotations

from dataclasses import dataclass

from .apply import apply_text_patch
from .split import DiffFile, Hunk


@dataclass
class PremiseCheck:
    """隔离环境是否满足「改动已应用」这个前提。"""

    ok: bool = False
    reason: str = ""              # 不满足时的可执行说明
    checked: int = 0              # 实际校验过的文件数
    detail: list = None           # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.detail is None:
            self.detail = []

    def to_dict(self) -> dict:
        return {"ok": self.ok, "reason": self.reason, "checked": self.checked,
                "detail": self.detail}


# 判定「这份内容是否等于把 hunks 正向应用后的结果」时，容忍的差异行数。
# 0 最严；但 Agent 可能在 diff 之外还有别的改动（例如同时改了别的文件），
# 而本校验只针对**本 diff 涉及的文件**，所以允许 0 —— 只要正向应用能复现，
# 就足以证明前提成立。
def _expected_after(base_text: str, hunks: list[Hunk]) -> str:
    return apply_text_patch(base_text, hunks, reverse=False)


def check_applied(files: list[DiffFile], read, *,
                  max_report: int = 3) -> PremiseCheck:
    """校验隔离环境里确实存在「改动已应用」的状态。

    `files`  —— 解析出的 diff（含 hunk 的行号与正文）
    `read`   —— `(relpath) -> str`，读隔离环境里的文件内容

    判据：对每个被改动的文件，从**撤销全部 hunk 得到的原文**出发正向应用，
    结果必须等于环境里的实际内容。相等 ⇒ 环境是「已应用」态。

    为什么用「撤销后正向应用」而不是直接比字符串：hunk 的正文就是权威描述，
    从它反推原文再正向走一遍，能同时验证行号与内容都对得上。
    """
    chk = PremiseCheck()
    if not files:
        chk.reason = "diff 里没有文件改动"
        return chk

    for f in files:
        if f.is_deleted or not f.hunks:
            continue
        chk.checked += 1
        actual = read(f.path)
        if not actual:
            chk.detail.append(f"{f.path}: 环境中不存在该文件")
            continue
        # 从「已应用」反推原文，再正向应用，看能否复现环境内容
        original = apply_text_patch(actual, f.hunks, reverse=True)
        reapplied = _expected_after(original, f.hunks)
        if _norm(reapplied) != _norm(actual):
            chk.detail.append(
                f"{f.path}: 环境内容与「已应用全部改动」不一致"
                f"（撤销后重新应用无法复现，说明隔离环境不在改动后的状态）"
            )

    if chk.detail:
        chk.ok = False
        shown = chk.detail[:max_report]
        chk.reason = (
            "隔离环境里没有「改动已应用」的状态，反事实实验无法进行：\n  - "
            + "\n  - ".join(shown)
            + ("\n  - …" if len(chk.detail) > max_report else "")
            + "\n原因：隔离环境按 `git worktree add --detach <base_commit>` 建立，"
              "默认 base_commit 是 HEAD。若改动**尚未提交**（还在工作区），"
              "worktree 里就是改动前的旧代码；此时「撤销某个改动」变成恒等变换，"
              "所有改动都会被误判为冗余。\n"
              "改法（二选一）：\n"
              "  1) 先把改动提交，并用 `--base-commit <改动前的 commit>` 指明前态；\n"
              "  2) 若你确实要分析未提交的工作区改动，请先提交或改用能承载"
              "工作区内容的隔离方式（当前实现不支持，宁可不给结论）。"
        )
        return chk

    chk.ok = True
    chk.reason = f"已校验 {chk.checked} 个文件：环境处于「改动已应用」状态"
    return chk


def _norm(text: str) -> str:
    """比较前归一化行尾 —— Windows 上 CRLF/LF 混用会让逐字符比较假失败。"""
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")
