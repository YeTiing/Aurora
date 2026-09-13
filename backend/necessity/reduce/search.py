"""ddmin 搜索 —— 两个相反方向，带预算与缓存。

对应 DIFF_REDUCER.md §1.2 / §5.3 / §6.2 / §6.3。

⚠️ 两个方向**是相反的**（文档 §1.2 的表格）：

    必要性最小化  T(D)=pass         求最小 D'⊆D 使 T(D')=pass   找**冗余**
    致败定位      T(D)=fail且T(∅)=pass 求最小 D''⊆D 使 T(D'')=fail 找**致败**

同一个算法、相反的谓词。写反了会得到完全错误的结论 —— 本模块用
`Direction` 显式区分，避免调用方传错布尔值。

预算（§6.3，硬上限）：
    max_test_runs  200    超限 -> 返回当前最优 + converged=false
    max_wall_time  15min  同上
    max_hunk_count 60     超过先做文件级粗最小化

§7 总原则：**宁可返回「未收敛的最优解」，也不要返回错误结论。**
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Sequence

PASS, FAIL, ERROR = "pass", "fail", "error"


class Direction(str, Enum):
    """搜索方向。用枚举而非 bool 是因为二者语义相反、极易混淆。"""
    NECESSARY = "necessary"   # 求最小**通过**集 -> 找冗余
    CULPRIT = "culprit"       # 求最小**失败**集 -> 找致败


# Budget 已拆到 budget.py（search.py 接近 300 行上限）。
from .budget import Budget  # noqa: E402,F401


@dataclass
class SearchResult:
    """搜索结果 + 收敛状态。

    `converged=False` 时 `best` 是**当前最优**而非全局最优 —— 报告必须
    如实标注（§7 总原则）。调用方不得把未收敛结果当成结论使用。
    """
    # ⚠️ best 的内部单元是**一致性组**（list[list[Hunk]]）—— 耦合改动必须
    # 同进同退，算法上不能拆。但**对外一律用 `hunks` 属性取扁平列表**：
    # 直接迭代 best 会拿到组对象，getattr(g,"id") 取不到 → 行数指标静默归零、
    # 冗余率虚报 1.0。这个不一致曾真实发生（两个 agent 独立报告 + 实测确认）。
    best: list = field(default_factory=list)
    converged: bool = False
    test_runs: int = 0
    cache_hits: int = 0
    elapsed: float = 0.0
    monotonic_violations: int = 0     # 反直觉交互的观测次数（§5.3 剪枝 2）
    stopped_reason: str = ""

    @property
    def hunks(self) -> list:
        """扁平 hunk 列表 —— **报告层唯一应该用的入口**。

        自动兼容两种形态：本来就是扁平列表时原样返回；是一致性组时拍平。
        这样调用方不可能再用错（用错会静默归零，不值得靠约定来防）。
        """
        out: list = []
        for item in self.best:
            if isinstance(item, (list, tuple)):
                out.extend(item)
            else:
                out.append(item)
        return out

    @property
    def groups(self) -> list:
        """按组返回（仅在需要保留分组语义时使用）。"""
        if not self.best:
            return []
        if isinstance(self.best[0], (list, tuple)):
            return [list(g) for g in self.best]
        return [[h] for h in self.best]


def _key(subset: Sequence) -> str:
    """子集缓存键 —— 用 id 序列的哈希，顺序无关。"""
    ids = sorted(str(getattr(x, "id", x)) for x in subset)
    return hashlib.sha1("|".join(ids).encode("utf-8")).hexdigest()


class _Runner:
    """带缓存与预算的判定器。"""

    def __init__(self, test: Callable[[list], str], budget: Budget,
                 stop_on: str):
        self._test = test
        self.budget = budget
        self._stop_on = stop_on          # PASS 或 FAIL
        self._cache: dict[str, str] = {}
        self.cache_hits = 0
        self.monotonic_violations = 0
        self._empty_result = None

    def __call__(self, subset: list) -> str:
        k = _key(subset)
        if k in self._cache:
            self.cache_hits += 1
            return self._cache[k]

        if self.budget.exhausted():
            # 预算耗尽：不执行、不缓存，返回一个「不可判定」的保守值。
            # 对必要性搜索，「不可判定」应视为**不可移除**（保守，§7 边界 8）；
            # 对致败搜索同理保守。
            return ERROR

        self.budget.test_runs += 1
        try:
            res = self._test(subset)
        except Exception:
            # 测试异常 -> error（§7 边界 8：保守视为不可移除）
            res = ERROR
        if res not in (PASS, FAIL, ERROR):
            res = ERROR

        self._cache[k] = res
        # 单调性校验（§5.3 剪枝 2）：正常情况「改动越少越可能 pass」。
        # ERROR 不参与判断 —— 它不携带单调性信息。
        if res != ERROR:
            self._check_monotonic(subset)
        return res

    def _check_monotonic(self, subset: list) -> None:
        """同一缓存里若出现「超集 pass 而子集 fail」则记一次反直觉交互。

        极简实现：只比对已有缓存条目，不做全序回溯 —— 目的是**观测**
        而非修正，报告里如实标注即可。
        """
        ids = {str(getattr(x, "id", x)) for x in subset}
        for k, v in self._cache.items():
            other = set(k.split("|")) if k else set()
            if ids < other and v == PASS and self._cache.get(_key(subset)) == FAIL:
                self.monotonic_violations += 1
                return

    def is_goal(self, subset: list) -> bool:
        return self(subset) == self._stop_on

    def baseline(self) -> str:
        """T(∅) —— 不做任何改动时的结果。用于 §7 边界 5 的前置检查。"""
        if self._empty_result is None:
            self._empty_result = self([])
        return self._empty_result


def _split(items: list, n: int) -> list[list]:
    """把 items 切成 n 份（ddmin 的 split）。"""
    n = max(1, min(n, len(items)))
    size = len(items) // n
    if size == 0:
        return [items]
    out = [items[i * size:(i + 1) * size] for i in range(n)]
    rest = items[n * size:]
    if rest:
        out[-1] = out[-1] + rest
    return [s for s in out if s]


def minimize(
    groups: list,
    test: Callable[[list], str],
    direction: Direction = Direction.NECESSARY,
    budget: Budget | None = None,
) -> SearchResult:
    """ddmin 主搜索（§5.3 的算法，两个方向共用）。

    test(subset) -> PASS | FAIL | ERROR
        调用方负责把「应用该子集并跑测试」包成这个签名。
        异常会被转为 ERROR 并保守处理。
    """
    budget = budget or Budget()
    if not budget._t0:
        budget.start()

    stop_on = PASS if direction is Direction.NECESSARY else FAIL
    runner = _Runner(test, budget, stop_on)

    # §7 边界 5：致败定位要求 T(∅)=pass，否则无法判定
    if direction is Direction.CULPRIT:
        base = runner.baseline()
        if base == FAIL:
            return SearchResult(
                best=list(groups), converged=False, test_runs=budget.test_runs,
                elapsed=budget.elapsed(),
                stopped_reason="T(∅)=fail：改动前测试就是挂的，无法判定致败改动（§7 边界 5）",
            )
        if base == ERROR:
            return SearchResult(
                best=list(groups), converged=False, test_runs=budget.test_runs,
                elapsed=budget.elapsed(),
                stopped_reason="基线测试不可判定（error），无法进行致败定位",
            )

    # §6.3：hunk 数超上限 -> 先做粗最小化（调用方应已做文件级合并，
    # 这里只在完全无法切分时兜底为「整体一组」）
    current = list(groups)
    n = 2
    converged = False
    stopped = ""

    while len(current) >= 2:
        if budget.exhausted():
            stopped = (
                f"预算耗尽（runs={budget.test_runs}/{budget.max_test_runs}, "
                f"elapsed={budget.elapsed():.1f}s）—— 返回当前最优，未收敛"
            )
            break

        subsets = _split(current, n)
        reduced = False
        for s in subsets:
            if not s:
                continue
            if runner.is_goal(s):
                current = s
                n = max(n - 1, 2)
                reduced = True
                break
        if not reduced:
            if n >= len(current):
                converged = True
                break
            n = min(n * 2, len(current))

    # 收敛的另一种情形：已缩到单个元素 —— 不可能再缩，这就是最优。
    # 曾经漏判这一条，导致「找到 1 个必要 hunk」被误报为未收敛。
    if len(current) < 2:
        converged = True

    # ⚠️ 必须显式尝试空集：必要性最小化的最优解可能就是**空集**
    # （即「全部改动都冗余」，DIFF_REDUCER.md §7 边界 4 明确要求处理）。
    # 主循环的条件是 len(current) >= 2，因此永远不会试到空集 —— 曾因此
    # 把「改了个寂寞」误报成「有一个必要改动」。
    if (direction is Direction.NECESSARY and current and not stopped
            and not budget.exhausted()):
        if runner.is_goal([]):
            current = []
            converged = True

    return SearchResult(
        best=current, converged=converged, test_runs=budget.test_runs,
        cache_hits=runner.cache_hits, elapsed=budget.elapsed(),
        monotonic_violations=runner.monotonic_violations,
        stopped_reason=stopped,
    )


def minimize_necessary(groups, test, budget=None) -> SearchResult:
    """求最小通过集 —— 差集即冗余改动。"""
    return minimize(groups, test, Direction.NECESSARY, budget)


def minimize_culprit(groups, test, budget=None) -> SearchResult:
    """求最小失败集 —— 即致败改动。"""
    return minimize(groups, test, Direction.CULPRIT, budget)


def redundancy_ratio(all_hunks: list, necessary_hunks: list) -> float:
    """冗余率 = 冗余改动行数 / 总改动行数。

    ⚠️ 用**行数**而非 hunk 数（EVAL.md §2.4 与 DIFF_REDUCER.md §8.5 的
    定义都是「冗余改动行数 / 总改动行数」）。用 hunk 计数会让一个改了
    50 行的 hunk 和一个改了 1 行的 hunk 权重相同，指标失真。
    """
    def _lines(hs):
        return sum(int(getattr(h, "added", 0)) + int(getattr(h, "removed", 0)) for h in hs)

    total = _lines(all_hunks)
    if total == 0:
        return 0.0
    nec_ids = {str(getattr(h, "id", h)) for h in necessary_hunks}
    red = sum(
        int(getattr(h, "added", 0)) + int(getattr(h, "removed", 0))
        for h in all_hunks if str(getattr(h, "id", h)) not in nec_ids
    )
    return red / total
