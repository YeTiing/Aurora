"""反事实实验的「真跑测试」判定器 —— 复用 `eval/verify.py` 的口径。

## 为什么需要它（补的是功能 2 一直没接上的那一环）

`reduce/search.py` 的 ddmin 需要一个 `test_runner(subset) -> PASS|FAIL|ERROR`
谓词，但生产路径上**没有**可用的实现：

  - `hooks.analyze_last` 与 `cli/reduce_cmd._sandbox_search` 都走
    `reduce/sandbox.py` 建隔离环境，而它 `copytree` 时排除 `.git`、
    且默认 checkout `base_commit`（默认 HEAD）—— 于是只在「改动已提交」
    时才对。**未提交的工作区改动会让「撤销」退化成恒等变换**：
    实测一个真正必要的改动被判成「冗余率 1.0」，且不报错。
  - `sandbox.run_tests` 自己判退出码（`0=pass / 1=fail / 2+=error`），
    与 `eval/verify.py` 是**两套口径**。口径分叉会出现
    「评测说这条测试失败、反事实实验说它通过」这类矛盾，
    而那不是算法问题（DIFF_REDUCER.md §8.4 强调必须同一口径）。

本模块改为**直接在带改动的工作目录里**做反事实：
撤销用 `apply_text_patch` 按 diff 行号反向应用，判定复用 `verify_workdir`。
配合 `premise.check_applied` 先验前提 —— 前提不成立就拒绝出结论。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .apply import apply_text_patch
from .premise import check_applied
from .search import ERROR, FAIL, PASS
from .split import DiffFile, all_hunks

logger = logging.getLogger("necessity.reduce.verify_runner")


def _norm(text: str) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


@dataclass
class RevertRunner:
    """ddmin 判定器：撤销子集外的 hunk，重跑验收测试。

    元素是「一致性组」（Hunk 列表）—— 组内 hunk 必须同进同退，否则
    改签名 + 改调用点会被拆开，产生「撤销签名后调用点仍传新参数」这种
    假失败（DIFF_REDUCER.md §5.2 记录的早期误判）。
    """

    workdir: Path
    files: list[DiffFile]
    timeout: int = 300
    stats: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.workdir = Path(self.workdir)
        self.hunks = all_hunks(self.files)
        self.stats = {"calls": 0, "pass": 0, "fail": 0, "error": 0}
        # 「全量改动已应用」时每个文件的内容 —— 撤销以它为基准。
        # 前提校验保证此刻读到的**确实是改动后**的内容。
        self._post: dict[str, str] = {}
        for f in self.files:
            if f.is_deleted:
                continue
            p = self.workdir / f.path
            if p.is_file():
                self._post[f.path] = _norm(p.read_text(encoding="utf-8", errors="replace"))

    # ── 前提 ─────────────────────────────────────────────────────

    def check_premise(self):
        """确认工作目录确实处于「改动已应用」状态。不成立时必须拒绝出结论。"""
        return check_applied(
            self.files,
            lambda rel: (self.workdir / rel).read_text(encoding="utf-8", errors="replace")
            if (self.workdir / rel).is_file() else "",
        )

    # ── 撤销 ─────────────────────────────────────────────────────

    def revert(self, drop: list) -> bool:
        """把 `drop` 里的 hunk 从工作目录撤销。返回是否成功。"""
        grouped: dict[str, list] = {}
        for h in drop:
            grouped.setdefault(h.file, []).append(h)
        for fpath, hs in grouped.items():
            post = self._post.get(fpath)
            if post is None:
                logger.warning("无法撤销 %s：没有基线内容", fpath)
                return False
            try:
                (self.workdir / fpath).write_text(
                    apply_text_patch(post, hs, reverse=True), encoding="utf-8")
            except Exception as e:
                logger.warning("撤销 %s 失败: %s", fpath, e)
                return False
        return True

    def restore_all(self) -> None:
        """恢复到「全量改动已应用」（每次判定前调用）。"""
        for fpath, text in self._post.items():
            p = self.workdir / fpath
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        for f in self.files:
            if f.is_deleted:
                p = self.workdir / f.path
                if p.is_file():
                    p.unlink()

    # ── 判定 ─────────────────────────────────────────────────────

    def __call__(self, subset) -> str:
        """ddmin 谓词。`subset` 是要**保留**的组（或裸 hunk）列表。

        语义（DIFF_REDUCER.md §1.2）：求最小 D'⊆D 使 T(D')=pass，
        等价于「撤销其余改动后仍 pass」。
        """
        from backend.necessity.eval.verify import verify_workdir

        keep: set[str] = set()
        for item in (subset or []):
            if isinstance(item, (list, tuple)):
                keep.update(str(getattr(h, "id", h)) for h in item)
            else:
                keep.add(str(getattr(item, "id", item)))
        drop = [h for h in self.hunks if str(h.id) not in keep]

        self.restore_all()
        self.stats["calls"] += 1
        if drop and not self.revert(drop):
            self.stats["error"] += 1
            return ERROR

        res = verify_workdir(self.workdir, timeout=self.timeout)
        if res.status not in (PASS, FAIL, ERROR):
            self.stats["error"] += 1
            return ERROR
        self.stats[res.status] += 1
        return res.status


def run_counterfactual(workdir, diff_text: str, *,
                       timeout: int = 300, keep: bool = False):
    """在一个带改动的目录上跑反事实实验（必要性最小化）。

    ⚠️ **绝不原地跑**：ddmin 会反复撤销/恢复文件，直接改调用方的目录等于
    破坏性地改写它的工作区（实测踩过 —— 跑完 `git diff` 变成 0 行，
    改动全被吃掉了）。这里先复制到临时目录再分析。

    返回 `(SearchResult, stats, premise)`。前提不成立时 **raise** ——
    由调用方决定怎么呈现，绝不返回一个看起来合理的错结论。
    """
    import shutil
    import tempfile

    from .groups import build_coherence_groups
    from .search import Budget, minimize_necessary
    from .split import parse_unified_diff

    src = Path(workdir)
    files = parse_unified_diff(diff_text)
    hunks = all_hunks(files)
    if not hunks:
        raise RuntimeError("diff 里没有可分析的改动")

    # 复制到临时目录：排除 .git（不需要历史，且能避免 worktree 不一致）
    tmp = Path(tempfile.mkdtemp(prefix="nsk-cf-"))
    work = tmp / "work"
    try:
        shutil.copytree(src, work, ignore=shutil.ignore_patterns(
            ".git", "__pycache__", "*.pyc", ".pytest_cache", ".mypy_cache"))
        runner = RevertRunner(workdir=work, files=files, timeout=timeout)
        premise = runner.check_premise()
        if not premise.ok:
            raise RuntimeError(
                "反事实实验前提不成立，已中止（宁可不给结论，也不给错结论）\n\n"
                + premise.reason)

        groups = build_coherence_groups(hunks)
        result = minimize_necessary(groups.groups, runner, Budget())
        return result, dict(runner.stats), premise
    finally:
        if not keep:
            shutil.rmtree(tmp, ignore_errors=True)
