"""Diff Reducer 的钩子实现 —— 主循环里只采集，不分析。

为什么不在钩子里直接跑最小化（§6.4）：
    最小化要跑上百次测试（预算上限 200 次判定 × 每次 2~30 秒）。
    放在主循环里会阻塞任务。文档推荐的三种集成方式里，「离线工具」
    零侵入且最容易出实验数字，钩子只负责把输入攒齐。

采集什么：
    - 任务开始时的 git HEAD（用于 worktree 基线）
    - 任务结束时的工作区 diff（`git diff`，即原始改动 D）
    - 测试结果（若宿主提供）

不采集什么（避免越界）：
    - 不写用户工作区、不改文件
    - 不触发测试（测试由离线 runner 在 worktree 里跑）
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.necessity.hooks import TaskResult

logger = logging.getLogger("necessity.reduce.hooks")


@dataclass
class CapturedDiff:
    """一次任务的采集结果 —— 离线分析的输入。"""
    task_id: str = ""
    repo: str = ""
    base_commit: str = ""
    diff_text: str = ""
    files_changed: int = 0
    test_result: str = ""
    stats: dict = field(default_factory=dict)


class ReduceHooks:
    """只实现采集相关的钩子；其余方法不定义（CompositeHooks 会跳过）。"""

    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or {}
        self.repo = str(Path(self.cfg.get("workspace") or ".").resolve())
        self._base = ""
        self._captures: list[CapturedDiff] = []

    # ── 生命周期 ─────────────────────────────────────────────────

    def on_task_start(self, task: dict) -> None:
        """记下基线 commit —— worktree 要基于它创建。

        取不到（非 git 仓库）不报错：离线分析时会降级为目录复制（§7 边界 1）。
        """
        self.repo = str(Path((task or {}).get("workspace") or self.repo).resolve())
        self._base = self._head_commit()

    def on_task_end(self, result: TaskResult) -> dict:
        """采集工作区 diff。**只读** —— 不碰用户文件。"""
        diff_text = self._diff()
        cap = CapturedDiff(
            task_id=getattr(result, "task_id", "") or "",
            repo=self.repo,
            base_commit=self._base,
            diff_text=diff_text,
            files_changed=self._count_files(diff_text),
            test_result=str((getattr(result, "diff_stats", {}) or {}).get("test_result", "")),
            stats={
                "turns": getattr(result, "turns", 0),
                "tokens": getattr(result, "tokens", 0),
            },
        )
        self._captures.append(cap)

        return {
            "reducer": {
                "captured": True,
                "files_changed": cap.files_changed,
                "hunks": self._count_hunks(diff_text),
                # 明确告知调用方：分析是离线的，不是在本轮完成的
                "note": "已采集 diff；最小化分析请离线运行 "
                        "`python -m cli.main reduce analyze`（§6.4 离线工具方式）",
            }
        }

    # ── 采集辅助 ─────────────────────────────────────────────────

    def _head_commit(self) -> str:
        try:
            r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo,
                               capture_output=True, text=True, timeout=15)
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""

    def _diff(self) -> str:
        """工作区相对 HEAD 的完整 diff（含未跟踪文件需另行 add，故不加 --cached）。

        用 `git diff HEAD` 而非 `git diff`：后者不含已 staged 的改动，
        会漏掉 Agent 通过 git add 提交前的部分。
        """
        try:
            r = subprocess.run(["git", "diff", "HEAD"], cwd=self.repo,
                               capture_output=True, text=True, timeout=60,
                               encoding="utf-8", errors="replace")
            return r.stdout or ""
        except Exception as e:
            logger.debug("采集 diff 失败: %s", e)
            return ""

    @staticmethod
    def _count_files(diff_text: str) -> int:
        return sum(1 for ln in (diff_text or "").splitlines() if ln.startswith("diff --git "))

    @staticmethod
    def _count_hunks(diff_text: str) -> int:
        return sum(1 for ln in (diff_text or "").splitlines() if ln.startswith("@@ "))

    # ── 离线分析入口 ─────────────────────────────────────────────

    def captures(self) -> list[CapturedDiff]:
        return list(self._captures)

    def last_capture(self) -> CapturedDiff | None:
        return self._captures[-1] if self._captures else None

    def analyze_last(
        self,
        test_runner=None,
        targets: list[str] | None = None,
        budget=None,
    ) -> Any:
        """对最近一次采集做必要性最小化。**离线调用**，不在钩子路径上。

        test_runner: 签名 (list[Hunk]) -> str(PASS|FAIL|ERROR)。
                     不给则用沙箱 + pytest（真实但慢）。
        """
        from .report import build_report
        from .sandbox import Sandbox
        from .search import Budget, minimize_necessary
        from .split import all_hunks, build_coherence_groups, parse_unified_diff

        cap = self.last_capture()
        if cap is None or not cap.diff_text.strip():
            return None

        files = parse_unified_diff(cap.diff_text)
        hunks = all_hunks(files)
        if not hunks:
            return None

        budget = budget or Budget()
        groups = build_coherence_groups(hunks)

        with Sandbox(cap.repo, cap.base_commit) as sb:
            info = sb.create()
            baseline, base_out = sb.run_tests(targets)

            # 记录每个文件在「已应用全部改动」状态下的内容 —— 撤销时以此为基准
            from .split import apply_text_patch
            post_state: dict[str, str] = {}
            for f in files:
                post_state[f.path] = sb.read(f.path)

            by_file: dict[str, list] = {}
            for h in hunks:
                by_file.setdefault(h.file, []).append(h)

            def _runner(subset):
                """判定：撤销所有**不在** subset 里的 hunk，然后跑测试。

                这正是必要性最小化的语义（DIFF_REDUCER.md §1.2）：
                「求最小 D'⊆D 使 T(D')=pass」等价于「撤销其余改动后仍 pass」。
                """
                keep = {str(getattr(x, "id", x)) for x in subset}
                drop = [h for h in hunks if str(h.id) not in keep]
                sb.revert_all()

                # 按文件重建：从「全量改动后」的内容里撤销要丢掉的 hunk
                grouped: dict[str, list] = {}
                for h in drop:
                    grouped.setdefault(h.file, []).append(h)
                for fpath, hs in grouped.items():
                    base = post_state.get(fpath)
                    if base is None:
                        continue
                    try:
                        sb.apply(fpath, apply_text_patch(base, hs, reverse=True))
                    except Exception:
                        return "error"

                res, _ = sb.run_tests(targets)
                return res

            if test_runner is not None:
                _runner = test_runner

            result = minimize_necessary(groups.groups, _runner, budget)
            return build_report(
                hunks, result,
                task_id=cap.task_id,
                baseline_test=baseline,
                final_test=("pass" if result.hunks else "n/a"),
                sandbox_mode=info.mode,
                sandbox_degraded=info.degraded,
                test_scope_narrowed=False,
            )
