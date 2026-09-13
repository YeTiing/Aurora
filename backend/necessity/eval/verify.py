"""验收测试判定 —— 决定一个 attempt 到底算 pass 还是 fail。

## 为什么必须有这个模块（它修的是一处「指标整体失真」）

此前 `aurora_agent.py` 用这一行判定成功：

    status = "pass" if data.get("response") else "fail"

即「LLM 回了文本」= pass。而 EVAL.md / INDEX.md 明确规定
**用验收测试判定，不用 LLM 当裁判**（§Phase 3「明确不做」第一条：
❌ 不要用 LLM 当裁判；用测试用例判定，客观、可复现）。

后果不是崩溃，而是**所有指标同时失真**：
  - INDEX.md 的主指标「破坏调用方的次数」不可测
  - Phase 3 验收标准「B 类上 graph 组在主指标上明显优于 baseline」
    比的会是「谁更爱回话」
  - 「A 类不倒退」也失去意义（两边都会是 100%）

所以这里把判定换成真实执行 `tests/`。判定口径与 `tasks/baseline.py`
的反向前置检查**刻意一致**（同一个 pytest 调用方式），否则会出现
「前置检查说基线失败、判定说基线通过」的口径矛盾。

## 为什么 error ≠ fail

`fail`（测试跑了且失败）是有效的观测值 —— Agent 没做对。
`error`（没跑起来 / 内部错误 / 没收集到用例）说明**这次运行没有被观测到**，
它与「被观测到失败」是不同的信息。混为一谈会让报告把
「环境坏了」读成「Agent 不行」。

退出码 2（被中断）、3（内部错误）、4（用法错误）、5（没收集到用例）
全部归 error；只有 0（全通过）归 pass、1（有用例失败）归 fail。
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("necessity.eval.verify")

# 复制仓库时排除的目录
_SKIP = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".pytest_cache")

# pytest 汇总行的各计数
_COUNT_RE = re.compile(r"(\d+)\s+(failed|passed|error|errors|skipped|deselected)")

_EXIT_MEANING = {
    0: "全部通过",
    1: "有用例失败",
    2: "被中断（多半是 collection error）",
    3: "内部错误",
    4: "命令行用法错误",
    5: "一个用例都没收集到",
}


@dataclass
class VerifyResult:
    """一次验收测试执行的结果。"""

    status: str = "error"            # pass / fail / error
    returncode: int = -1
    passed: int = 0
    failed: int = 0
    errors: int = 0
    collected: int = 0
    output: str = ""

    @property
    def diagnosis(self) -> str:
        if self.returncode < 0:
            return self.output[:300] or "未执行"
        why = _EXIT_MEANING.get(self.returncode, f"退出码 {self.returncode}")
        return (f"退出码 {self.returncode}（{why}）"
                f"收集 {self.collected} / 通过 {self.passed} / 失败 {self.failed}")

    def to_dict(self) -> dict:
        return {
            "status": self.status, "returncode": self.returncode,
            "passed": self.passed, "failed": self.failed,
            "errors": self.errors, "collected": self.collected,
            "diagnosis": self.diagnosis,
        }


def _parse_counts(output: str) -> dict:
    out = {"passed": 0, "failed": 0, "errors": 0, "collected": 0}
    for m in _COUNT_RE.finditer(output or ""):
        n, kind = int(m.group(1)), m.group(2)
        if kind == "passed":
            out["passed"] = n
        elif kind == "failed":
            out["failed"] = n
        elif kind.startswith("error"):
            out["errors"] = n
    m = re.search(r"collected\s+(\d+)\s+item", output or "")
    out["collected"] = (int(m.group(1)) if m
                        else out["passed"] + out["failed"] + out["errors"])
    return out


def classify(returncode: int, output: str) -> str:
    """退出码 + 输出 -> pass / fail / error。口径与 baseline.py 一致。"""
    counts = _parse_counts(output)
    if returncode == 0:
        # 全绿但一个用例都没收集到 = 任务没写测试，不能算「通过」
        return "pass" if counts["collected"] > 0 else "error"
    if returncode == 1:
        # 有用例失败才是有效观测；1 但零失败（罕见）说明输出异常
        return "fail" if (counts["failed"] + counts["errors"]) > 0 else "error"
    return "error"


def verify(repo: str | Path, tests: str | Path, *, timeout: int = 300,
           python: str = "", copy_to: str | Path | None = None) -> VerifyResult:
    """在 `repo` + `tests` 上跑验收测试。

    默认把两者合成到一个临时工作目录再跑（与 `tasks/baseline.py` 同一布局），
    避免污染 `repo` —— 它是快照，被污染后所有后续 diff 都不准。
    传 `copy_to` 可指定工作目录（调用方已有临时目录时省一次复制）。
    """
    repo, tests = Path(repo), Path(tests)
    res = VerifyResult()
    if not repo.is_dir():
        res.output = f"repo/ 不存在: {repo}"
        return res
    if not tests.is_dir():
        res.output = f"tests/ 不存在: {tests}"
        return res

    import tempfile

    tmp = None
    try:
        if copy_to is not None:
            work = Path(copy_to)
            work.mkdir(parents=True, exist_ok=True)
        else:
            tmp = tempfile.mkdtemp(prefix="nsk-verify-")
            work = Path(tmp) / "work"
            work.mkdir(parents=True, exist_ok=True)
        # dirs_exist_ok=True：copy_to 指向已有目录时叠加而不是报错
        shutil.copytree(repo, work, ignore=_SKIP, dirs_exist_ok=True)
        shutil.copytree(tests, work / "tests", ignore=_SKIP, dirs_exist_ok=True)

        proc = subprocess.run(
            [python or sys.executable, "-m", "pytest", "-q", "tests/",
             "-p", "no:cacheprovider", "-rA"],
            cwd=work, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        res.output = f"验收测试超时（>{timeout}s）"
        return res
    except Exception as e:
        res.output = f"{type(e).__name__}: {e}"
        return res
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)

    res.returncode = proc.returncode
    res.output = ((proc.stdout or "") + (proc.stderr or ""))[-4000:]
    for k, v in _parse_counts(res.output).items():
        setattr(res, k, v)
    res.status = classify(res.returncode, res.output)
    return res


def verify_workdir(workdir: str | Path, *, timeout: int = 300) -> VerifyResult:
    """在**已经存在的**工作目录里跑 `tests/`（Agent 跑完后的现场）。

    这是评测主路径用的入口：Agent 的改动就在 `workdir` 里，
    直接在那里跑验收测试，不做任何复制。
    """
    workdir = Path(workdir)
    res = VerifyResult()
    tests = workdir / "tests"
    if not tests.is_dir():
        res.output = f"工作目录里没有 tests/: {tests}"
        return res
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "tests/",
             "-p", "no:cacheprovider", "-rA"],
            cwd=workdir, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        res.output = f"验收测试超时（>{timeout}s）"
        return res
    except Exception as e:
        res.output = f"{type(e).__name__}: {e}"
        return res

    res.returncode = proc.returncode
    res.output = ((proc.stdout or "") + (proc.stderr or ""))[-4000:]
    for k, v in _parse_counts(res.output).items():
        setattr(res, k, v)
    res.status = classify(res.returncode, res.output)
    return res
