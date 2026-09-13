"""每次运行的快照隔离 —— EVAL.md §3.2 第 1 步「复制快照到临时目录」。

## 它修的问题

此前 `runner.run_one()` 把 `repo_of(task, task_id)` 直接交给 Agent 当
workspace，**没有任何复制**。而 `repo_of` 返回的是任务快照在仓库里的
真实路径。后果：

    第 1 次运行 -> Agent 就地把「原始代码快照」改了
    第 2 次运行 -> 基线已经是改过的，反向前置检查的结论失效
    ...           后续全部运行都在脏基线上跑，且**不报错**

更糟的是快照被改后，`git status` 在工作区显示成「未提交改动」——
看起来像开发者在改代码，而不是评测在污染仓库。而 A 组的
「裸 Agent」本应是干净基线，实际第一次就被自己污染了。

## 设计

`SandboxWorkspace` 一次运行一个临时目录：

    <tmp>/repo/     快照副本 + tests/（Agent 在这里干活，验收测试也在这里跑）
    <tmp>/          运行结束后整体删除

`tests/` 与 Agent 的工作目录**合并到同一处**是刻意的：
验收测试要用 `from models.user import User` 这种写法 import 被测代码，
所以 repo 的根必须同时是 sys.path 起点。分开放会让测试 import 不到。

## 保留现场

`keep_on_failure=True` 时失败运行不删目录，并把路径记进 meta ——
没有现场就无法归因（EVAL.md §7.4 的精神）。
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("necessity.eval.snapshot")

# 复制时排除：.git 会拖慢且评测不需要历史；缓存会带进陈旧字节码
_SKIP = shutil.ignore_patterns(
    ".git", "__pycache__", "*.pyc", ".pytest_cache", ".mypy_cache",
)


@dataclass
class SandboxWorkspace:
    """一次运行的工作目录。用 `with` 自动清理。

    为什么用类而不是两个函数：临时目录的创建与删除必须成对 ——
    中途抛异常时若没删，长跑（168 次）会堆满磁盘；
    若删早了，则拿不到失败现场。
    """

    root: Path
    repo_path: Path
    kept: bool = False
    pristine: str = ""      # Agent 动手前建立的基线 commit SHA（diff 的对照点）

    @property
    def workdir(self) -> Path:
        """Agent 的工作目录（= 合并了 tests/ 的 repo 副本）。"""
        return self.repo_path

    def cleanup(self) -> None:
        if self.kept:
            return
        shutil.rmtree(self.root, ignore_errors=True)

    def keep(self) -> str:
        """保留现场（失败归因用），返回保留的路径。"""
        self.kept = True
        return str(self.root)

    def __enter__(self) -> "SandboxWorkspace":
        return self

    def __exit__(self, *exc) -> None:
        self.cleanup()


def create(task, *, keep_on_failure: bool = False) -> SandboxWorkspace:
    """按任务快照建一个隔离工作目录。

    `task` 是 `LoadedTask`（有 `.repo` / `.tests`）。
    验证：复制后必须能确实看到快照文件，否则抛错 ——
    「复制了个空目录」会让 Agent 在空气上干活，而那不是会被发现的那种错。
    """
    src_repo = Path(getattr(task, "repo", ""))
    src_tests = Path(getattr(task, "tests", ""))
    if not src_repo.is_dir():
        raise RuntimeError(f"任务快照 repo/ 不存在: {src_repo}")

    root = Path(tempfile.mkdtemp(prefix="nsk-run-"))
    repo_path = root / "repo"
    try:
        shutil.copytree(src_repo, repo_path, ignore=_SKIP)
        # tests/ 并进工作目录根部 —— 见模块头的说明
        if src_tests.is_dir():
            shutil.copytree(src_tests, repo_path / "tests", ignore=_SKIP,
                            dirs_exist_ok=True)
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise

    files = [p for p in repo_path.rglob("*.py")]
    if not files:
        shutil.rmtree(root, ignore_errors=True)
        raise RuntimeError(
            f"快照副本里没有任何 .py 文件: {src_repo} —— "
            "多半是 gitlink 问题（快照源码没入库），Agent 会在空目录上干活"
        )

    ws = SandboxWorkspace(root=root, repo_path=repo_path)
    # ⚠️ **必须在 Agent 动手之前**建立基线 commit。若在事后才 init+commit
    # （此前的做法），提交进去的是「已经被改过的内容」，`git diff HEAD`
    # 恒为空 —— Agent 改了文件但 diff_text 是空的，而 diff 是 Diff Reducer
    # 全部指标（冗余率/保真度）的唯一输入。
    ws.pristine = _baseline_commit(repo_path)
    return ws


def _baseline_commit(repo_path: Path) -> str:
    """在快照副本里建立一个「原始状态」commit，返回其 SHA（失败返回空）。

    复制时排除了 `.git`，所以这里要新建一个 —— 但**只能提交未改动的快照**，
    因此调用时机必须在 Agent 运行之前。
    """
    import subprocess

    try:
        for args in (["init", "-q"],
                     ["config", "user.email", "eval@necessity"],
                     ["config", "user.name", "necessity-eval"],
                     ["add", "-A"],
                     ["commit", "-qm", "pristine snapshot"]):
            r = subprocess.run(["git", *args], cwd=repo_path,
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            if r.returncode != 0 and args[0] == "commit":
                # 空仓库（没有任何可提交文件）无法建基线 —— 如实返回空，
                # 让 diff_of 走「取不到 diff」的路径而不是伪造内容。
                logger.warning("无法建立快照基线 commit: %s", (r.stderr or "")[:200])
                return ""
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_path,
                             capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
        return (sha.stdout or "").strip()
    except Exception as e:
        logger.warning("快照 git 初始化失败（diff 将不可用）: %s", e)
        return ""


def diff_of(workdir: str | Path) -> str:
    """取工作目录相对**快照基线**的改动（unified diff）。

    与 `_baseline_commit` 配套：基线在 Agent 运行前建立，这里取增量。
    取不到就返回空字符串并记录 —— 绝不伪造 diff，因为它直接喂给冗余率指标。

    ⚠️ `encoding="utf-8"` **必须显式给**：`text=True` 在 Windows 上会用
    系统 locale（GBK）解码，而 `git diff` 输出的是 UTF-8。中文注释/路径
    一旦出现在 diff 里就会抛 UnicodeDecodeError，被下面的 except 吞掉后
    返回空串 —— 「Agent 改了文件但 diff 恒为空」，而 diff 是 Diff Reducer
    全部指标的输入。实测踩过。
    """
    import subprocess

    wd = Path(workdir)
    for args in (["diff", "HEAD"], ["diff"]):
        try:
            r = subprocess.run(["git", *args], cwd=wd, capture_output=True,
                               text=True, timeout=60,
                               encoding="utf-8", errors="replace")
            if r.returncode == 0:
                return r.stdout or ""
        except Exception as e:
            logger.warning("git %s 失败: %s", " ".join(args), e)
    return ""
