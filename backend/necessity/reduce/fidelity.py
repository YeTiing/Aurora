"""最小化保真度检查 —— DIFF_REDUCER.md §8.4 的成功判据。

为什么必须单独成模块：文档把「保真度 = 100%」列为**成功判据**，并明确
「保真度 < 100%（说明算法会破坏功能，**必须修**）」。

它的含义是：**最小化后的改动集必须仍能让验收测试通过**。
若最小化把某块必要的改动删了，测试会红 —— 那说明算法错了，而不是
「找到了更多冗余」。没有这个检查，冗余率这个数字就不可信：
算法越激进地删，冗余率越好看，但功能已经坏了。

这是**零成本可离线验证**的判据（不需要 LLM），所以没有理由留空。
"""
from __future__ import annotations

from dataclasses import dataclass

# 测试结果取值（与 search.py 对齐）
PASS = "pass"
FAIL = "fail"
ERROR = "error"


@dataclass
class FidelityResult:
    """保真度检查结果。

    `fidelity` 是比率（1.0 = 100%），`ok` 是它是否达标。
    `failures` 记录每一处「最小化后反而失败」的详情，便于定位算法问题。
    """
    checked: int = 0
    preserved: int = 0
    failures: list = None  # type: ignore[assignment]
    errors: list = None    # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.failures is None:
            self.failures = []
        if self.errors is None:
            self.errors = []

    @property
    def fidelity(self) -> float:
        """保真度 = 保住的 / 检查过的。无样本时返回 1.0（不虚报失败）。"""
        return (self.preserved / self.checked) if self.checked else 1.0

    @property
    def ok(self) -> bool:
        """文档要求 100% —— 任何一处破坏功能都不达标。

        ⚠️ 注意 ``verified`` 与 ``ok`` 的区别：
          ``ok``        —— 没有被证明为坏（可能只是没检查）
          ``verified``  —— 确实检查过且通过了

        混用二者会让「基线本来就不通过、根本没检查」被当成「保真度 100%」，
        那是把未知当通过 —— 正是文档 §7「宁可返回未收敛，也不要错误结论」
        要避免的。所以报告用 ``verified`` 判断要不要告警。
        """
        return self.preserved == self.checked

    @property
    def verified(self) -> bool:
        """是否**确实检查过**并确认保真（checked>0 且全部通过）。"""
        return self.checked > 0 and self.preserved == self.checked

    def to_dict(self) -> dict:
        return {
            "checked": self.checked,
            "preserved": self.preserved,
            "fidelity": round(self.fidelity, 4),
            "ok": self.ok,
            "failures": self.failures,
            "errors": self.errors,
        }


def check_fidelity(test_runner, necessary_subset, baseline_result: str = PASS) -> FidelityResult:
    """验证「最小化后的子集仍能通过测试」。

    test_runner: 签名为 (subset) -> PASS|FAIL|ERROR 的判定函数
                 （与 search.py 用的同一个，保证口径一致）
    necessary_subset: ddmin 求出的最小通过集
    baseline_result: 压缩前的判定结果。若基线就不是 pass，
                     保真度无从谈起 —— 文档 §7 边界 5 已规定此时拒绝判定。

    为什么强调「同一口径」：若这里用另一个 runner，可能出现
    「搜索时说 pass、校验时说 fail」的矛盾，而那是口径问题不是算法问题。
    """
    res = FidelityResult()

    if baseline_result != PASS:
        # 基线不通过 -> 没有「保真」可言（§7 边界 5）
        res.errors.append(
            f"基线测试不是 pass（{baseline_result}）—— 无法评估保真度"
        )
        return res

    res.checked = 1
    try:
        after = test_runner(necessary_subset)
    except Exception as e:
        res.errors.append(f"保真度检查执行失败: {type(e).__name__}: {e}")
        return res

    if after == PASS:
        res.preserved = 1
    elif after == ERROR:
        # 无法判定时保守记为失败 —— 宁可报「不达标」让人去查，
        # 也不要把未知当成通过（那正是「错误结论」）
        res.errors.append("最小化后测试无法判定（error）—— 保守视为保真度不达标")
    else:
        res.failures.append({
            "reason": "最小化后测试失败，说明删掉了必要的改动",
            "subset_ids": _ids(necessary_subset),
        })
    return res


def _ids(subset) -> list:
    out = []
    for item in (subset or []):
        if isinstance(item, (list, tuple)):
            out.extend(str(getattr(x, "id", x)) for x in item)
        else:
            out.append(str(getattr(item, "id", item)))
    return out


def attach_to_report(report, fidelity: FidelityResult) -> None:
    """把保真度写进报告，并在不达标时加显式告警。

    为什么要写进报告而不是只在日志里：文档把保真度列为**成功判据** ——
    它是「这个冗余率能不能用」的前提。不达标时冗余率再好看也没有意义，
    必须让读报告的人一眼看到。
    """
    report.metrics["fidelity"] = round(fidelity.fidelity, 4)
    report.metrics["fidelity_ok"] = fidelity.ok
    report.metrics["fidelity_verified"] = fidelity.verified

    if not fidelity.verified:
        if fidelity.failures:
            report.notes.append(
                "⚠️ **保真度不达标**：最小化后有测试失败 —— 说明算法删掉了必要的改动，"
                "**冗余率不可用**（DIFF_REDUCER.md §8.4 明确要求此时必须修算法）。"
            )
        elif fidelity.errors:
            report.notes.append(
                "⚠️ 保真度无法确认：" + "；".join(fidelity.errors) +
                " —— 未知不等于通过，应保守对待该报告。"
            )
