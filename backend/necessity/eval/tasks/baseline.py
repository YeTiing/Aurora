"""反向前置检查 —— EVAL.md §1.2 第 5 步，**必须**在生产路径上强制。

文档原话：

    必须验证 repo/ 原始状态下这些测试是**失败**的，否则这个任务没有区分度。

这条不变量为什么值得单独成模块并强制在跑分前执行：

    基线就通过 = Agent 什么都不做也算成功 = 整批评测的数字全是虚的。
    而它失效时**不报错** —— 只是让每个 arm 的通过率都虚高、两组看起来持平。
    这正是「错误结论」而不是「崩溃」，所以必须主动检查。

此前只有 `tests/test_necessity_eval_tasks.py` 对生成器产出的**临时**任务做过
这条断言；仓库里真实的任务集（`eval/tasks/*/`）在进 runner 时没有任何检查 ——
本轮就是靠肉眼才发现合并时漏掉了 task.md / meta.json。故补此模块。

判定为何不用「returncode != 0」了事：
    pytest 的退出码里，2=被中断 / 3=内部错误 / 4=用法错误 / 5=没收集到用例。
    这些都 != 0，但**没有一个**说明「测试真的失败了，任务有区分度」。
    尤其退出码 5（一个用例都没收集到）恰恰是**零区分度**的反面情形 ——
    若按 != 0 判定，它会被判成「合格」，把最坏的情况放进去。
    所以要求：退出码 == 1 **且** 汇总里 failed/error 计数 > 0。
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("necessity.eval.tasks.baseline")

# 复制仓库时排除的目录 —— .git 会让复制变慢且无意义，__pycache__ 会带进陈旧字节码
_SKIP_COPY = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".pytest_cache")

# pytest 汇总行里各计数，如 "1 failed, 1 passed in 0.03s"
_COUNT_RE = re.compile(r"(\d+)\s+(failed|passed|error|errors|skipped|deselected)")

# pytest 退出码的语义（用于给出可读的诊断，而不是只报一个数字）
_EXIT_MEANING = {
    0: "全部通过（= 任务没有区分度）",
    1: "有用例失败（= 期望的基线状态）",
    2: "被中断",
    3: "内部错误（多半是测试自身写错了）",
    4: "命令行用法错误",
    5: "一个用例都没收集到（= 零区分度）",
}


@dataclass
class BaselineCheck:
    """一个任务在**原始状态**下跑验收测试的结果。"""

    task_id: str = ""
    checked: bool = False            # 是否真的跑起来了
    returncode: int = -1
    passed: int = 0
    failed: int = 0
    errors: int = 0
    collected: int = 0
    output: str = ""
    error: str = ""                  # 没能跑起来的原因（区别于测试失败）

    @property
    def has_discrimination(self) -> bool:
        """**核心判据**：基线存在真实失败 = 任务有区分度。

        同时要求 collected > 0：没有用例可收集时 failed 必然是 0，
        但那种情况是「任务没写测试」而不是「测试通过」，
        单独判掉才能给出正确的诊断（见 _EXIT_MEANING）。
        """
        return (self.checked and self.returncode == 1
                and (self.failed + self.errors) > 0 and self.collected > 0)

    @property
    def diagnosis(self) -> str:
        if self.error:
            return f"无法执行: {self.error}"
        if not self.checked:
            return "未检查"
        why = _EXIT_MEANING.get(self.returncode, f"退出码 {self.returncode}")
        return (f"退出码 {self.returncode}（{why}）"
                f"收集 {self.collected} / 失败 {self.failed} / 错误 {self.errors}")

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id, "checked": self.checked,
            "returncode": self.returncode, "passed": self.passed,
            "failed": self.failed, "errors": self.errors,
            "collected": self.collected,
            "has_discrimination": self.has_discrimination,
            "diagnosis": self.diagnosis,
        }


def _parse_counts(output: str) -> dict:
    """从 pytest 输出里抠出各计数（取最后一次出现的值）。"""
    out = {"passed": 0, "failed": 0, "errors": 0, "collected": 0}
    for m in _COUNT_RE.finditer(output or ""):
        n, kind = int(m.group(1)), m.group(2)
        if kind == "passed":
            out["passed"] = n
        elif kind == "failed":
            out["failed"] = n
        elif kind.startswith("error"):
            out["errors"] = n
    # "collected N items" / "N tests collected" —— 用于识别「没收集到用例」
    m = re.search(r"collected\s+(\d+)\s+item", output or "")
    if m:
        out["collected"] = int(m.group(1))
    else:
        # 没有 collected 行时用「跑过的」总量兜底
        out["collected"] = out["passed"] + out["failed"] + out["errors"]
    return out


def check_baseline(task, timeout: int = 180, python: str = "") -> BaselineCheck:
    """在临时副本里跑 `tests/`，看**原始状态**下是否失败。

    为什么复制到临时目录而不是原地跑：验收测试通常会写进 repo/ 下
    （见 loader.repo / loader.tests 分离的约定），原地跑会污染快照 ——
    而这份快照是 Diff Reducer 的 worktree 基线，被污染后所有 diff 都不准。
    """
    task_id = getattr(getattr(task, "spec", None), "task_id", "") or str(task)
    res = BaselineCheck(task_id=task_id)

    repo = Path(getattr(task, "repo", ""))
    tests = Path(getattr(task, "tests", ""))
    if not repo.is_dir():
        res.error = f"repo/ 不存在: {repo}"
        return res
    if not tests.is_dir():
        res.error = f"tests/ 不存在: {tests}"
        return res

    exe = python or sys.executable
    try:
        with tempfile.TemporaryDirectory(prefix=f"nsk-baseline-{task_id}-") as td:
            work = Path(td) / "work"
            shutil.copytree(repo, work, ignore=_SKIP_COPY)
            shutil.copytree(tests, work / "tests", dirs_exist_ok=True)
            proc = subprocess.run(
                [exe, "-m", "pytest", "-q", "tests/", "-p", "no:cacheprovider", "-rA"],
                cwd=work, capture_output=True, text=True, timeout=timeout,
                encoding="utf-8", errors="replace",
            )
    except subprocess.TimeoutExpired:
        res.error = f"验收测试超时（>{timeout}s）"
        return res
    except Exception as e:
        res.error = f"{type(e).__name__}: {e}"
        return res

    res.checked = True
    res.returncode = proc.returncode
    res.output = ((proc.stdout or "") + (proc.stderr or ""))[-4000:]
    for k, v in _parse_counts(res.output).items():
        setattr(res, k, v)
    return res


def check_all(tasks, timeout: int = 180, workers: int = 4, log=None) -> list[BaselineCheck]:
    """并发检查整个任务集。

    并发而非串行：每个任务要起一个 pytest 进程（秒级），
    22 个任务串行会让预检本身变成一次「不声不响的长任务」。
    并发度默认 4 —— 再高只是让机器抢 CPU，不会更快。
    """
    items = list(tasks)
    if not items:
        return []
    n = max(1, min(workers, len(items)))

    def one(t):
        c = check_baseline(t, timeout=timeout)
        if log:
            tid = c.task_id
            log(f"  基线 {tid:32s} {'✓ 有区分度' if c.has_discrimination else '✗ ' + c.diagnosis}")
        return c

    if n == 1:
        return [one(t) for t in items]
    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(one, items))


def problems(checks: list[BaselineCheck]) -> list[str]:
    """把不合格的检查转成可读问题列表（供 CLI / 测试断言）。"""
    out: list[str] = []
    for c in checks:
        if not c.has_discrimination:
            out.append(
                f"{c.task_id}: 基线不满足「测试必须失败」—— {c.diagnosis}。"
                "该任务没有区分度，Agent 什么都不做也会被判成功。"
            )
    return out


class TasksetInvalid(RuntimeError):
    """任务集不满足「基线必须失败」—— 继续跑分会产出假数字，必须中止。

    与 `AgentUnavailable` 同类：都是**环境/配置问题**而不是某次运行失败，
    所以不能被记成一条 status=error 的记录混过去（那会让报告看起来
    「跑过了、只是有些任务没通过」，而实际是整批数字都不可信）。
    """

    def __init__(self, bad: list[str]):
        self.problems = list(bad)
        super().__init__(
            f"任务集不满足 EVAL.md §1.2 第 5 步（基线测试必须失败）："
            f"{len(self.problems)} 个任务没有区分度，跑分会产出虚高数字，已中止。\n  - "
            + "\n  - ".join(self.problems)
        )


def assert_discriminates(tasks, *, workers: int = 4, timeout: int = 180,
                         log=None) -> list[BaselineCheck]:
    """检查并**强制**：不合格就抛 TasksetInvalid。

    这是给 runner 用的入口 —— 它只关心「能不能开跑」，
    不需要自己拼 problems 列表。
    """
    checks = check_all(tasks, timeout=timeout, workers=workers, log=log)
    bad = problems(checks)
    if bad:
        raise TasksetInvalid(bad)
    return checks
