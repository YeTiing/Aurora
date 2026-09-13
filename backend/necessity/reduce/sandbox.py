"""测试执行环境 —— worktree 隔离。

对应 DIFF_REDUCER.md §5.4 与 INTEGRATION.md §7.2。

⚠️ 文档的硬性要求（原文）：
    「**绝不原地切换**（`git stash` / `git checkout`）—— 会污染用户工作区」

所以只用 `git worktree add`。非 git 仓库降级为目录复制，并在报告里标注
（§5.4 / §7 边界 1）。

为什么隔离必须严格：
    本工具每次判定都要「应用某个改动子集 → 跑测试」。若在用户工作区原地
    操作，一次中断就会把用户未提交的改动冲掉。这是**不可逆的数据损失**，
    比分析结果错误严重得多。
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("necessity.reduce.sandbox")


@dataclass
class SandboxInfo:
    path: str = ""
    mode: str = ""            # git-worktree | copy
    degraded: bool = False
    note: str = ""


class Sandbox:
    """在隔离副本里应用改动子集并跑测试。

    生命周期：create() -> apply()/revert()/run() -> cleanup()
    用上下文管理器保证 cleanup 一定执行。
    """

    def __init__(self, repo: str, base_commit: str = "", workdir: str | None = None):
        self.repo = str(Path(repo).resolve())
        self.base_commit = base_commit
        self._workdir = workdir
        self.info = SandboxInfo()
        self._originals: dict[str, str | None] = {}   # relpath -> 原内容（None=原不存在）
        self._created = False

    # ── 创建 / 清理 ──────────────────────────────────────────────

    def create(self) -> SandboxInfo:
        if self._created:
            return self.info
        tmp = self._workdir or tempfile.mkdtemp(prefix="necessity-reduce-")

        if self._is_git():
            ok = self._create_worktree(tmp)
            if ok:
                self.info = SandboxInfo(path=str(Path(tmp) / "wt"), mode="git-worktree")
                self._created = True
                return self.info
            # worktree 失败 -> 降级复制（不报错，如实标注）
            logger.warning("git worktree 创建失败，降级为目录复制")

        dest = Path(tmp) / "copy"
        self._copy_tree(dest)
        self.info = SandboxInfo(
            path=str(dest), mode="copy", degraded=True,
            note="非 git 仓库或 worktree 创建失败，降级为目录复制（更慢、更占空间，但结果等价）",
        )
        self._created = True
        return self.info

    def _is_git(self) -> bool:
        """`repo` 自身是不是一个 git 仓库的根。

        ⚠️ **不要**改回 `rev-parse --is-inside-work-tree`：它对任何子目录都
        返回 true（只要祖先里有 .git）。实测踩过 —— 任务快照嵌在 Aurora
        仓库里时，该判据为真，于是 `git worktree add` 建出来的是
        **Aurora 自己的** worktree（内容里有 Aurora 的顶层文件），
        而不是任务仓库。全程不报错，只是 Diff Reducer 在分析错对象。

        判据必须是「toplevel 等于自己」。
        """
        try:
            r = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=self.repo, capture_output=True, text=True, timeout=15,
            )
            if r.returncode != 0:
                return False
            top = (r.stdout or "").strip()
            if not top:
                return False
            return Path(top).resolve() == Path(self.repo).resolve()
        except Exception:
            return False

    def _create_worktree(self, tmp: str) -> bool:
        wt = str(Path(tmp) / "wt")
        args = ["git", "worktree", "add", "--detach", wt]
        if self.base_commit:
            args.append(self.base_commit)
        try:
            r = subprocess.run(args, cwd=self.repo, capture_output=True,
                               text=True, timeout=120)
            if r.returncode != 0:
                logger.warning("git worktree add 失败: %s", r.stderr[:200])
                return False
            return True
        except Exception as e:
            logger.warning("git worktree add 异常: %s", e)
            return False

    def _copy_tree(self, dest: Path) -> None:
        """目录复制降级。跳过 .git / node_modules / 虚拟环境等。"""
        SKIP = shutil.ignore_patterns(
            ".git", "node_modules", "__pycache__", ".venv", "venv",
            ".mypy_cache", ".pytest_cache", ".tox", "dist", "build", "target",
        )
        shutil.copytree(self.repo, dest, ignore=SKIP, dirs_exist_ok=True)

    def cleanup(self) -> None:
        """清理。**必须保证用户工作区不受影响**。

        worktree：用 `git worktree remove --force`（保留其元数据一致性）；
        失败时退回手工删目录 + `git worktree prune`，避免留下悬空记录。
        """
        if not self._created or not self.info.path:
            return
        p = Path(self.info.path)
        try:
            if self.info.mode == "git-worktree":
                r = subprocess.run(
                    ["git", "worktree", "remove", "--force", str(p)],
                    cwd=self.repo, capture_output=True, text=True, timeout=60,
                )
                if r.returncode != 0:
                    shutil.rmtree(p, ignore_errors=True)
                    subprocess.run(["git", "worktree", "prune"], cwd=self.repo,
                                   capture_output=True, timeout=30)
            else:
                shutil.rmtree(p.parent, ignore_errors=True)
        except Exception as e:
            logger.warning("sandbox cleanup 失败: %s", e)
        finally:
            self._created = False

    def __enter__(self) -> "Sandbox":
        self.create()
        return self

    def __exit__(self, *exc) -> None:
        self.cleanup()

    # ── 应用 / 撤销改动 ──────────────────────────────────────────

    def apply(self, relpath: str, content: str) -> None:
        """把文件设成指定内容，并记住原值以便 revert。"""
        rel = relpath.replace("\\", "/")
        target = Path(self.info.path) / rel
        if rel not in self._originals:
            self._originals[rel] = (
                target.read_text(encoding="utf-8", errors="replace")
                if target.is_file() else None
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def remove(self, relpath: str) -> None:
        rel = relpath.replace("\\", "/")
        target = Path(self.info.path) / rel
        if rel not in self._originals:
            self._originals[rel] = (
                target.read_text(encoding="utf-8", errors="replace")
                if target.is_file() else None
            )
        if target.is_file():
            target.unlink()

    def revert_all(self) -> None:
        """把所有 apply/remove 过的文件恢复原状（子集之间复用同一 sandbox）。"""
        for rel, original in list(self._originals.items()):
            target = Path(self.info.path) / rel
            try:
                if original is None:
                    if target.is_file():
                        target.unlink()
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(original, encoding="utf-8")
            except Exception as e:
                logger.warning("revert 失败 %s: %s", rel, e)
        self._originals.clear()

    def read(self, relpath: str) -> str:
        rel = relpath.replace("\\", "/")
        p = Path(self.info.path) / rel
        return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""

    # ── 跑测试 ──────────────────────────────────────────────────

    def run_tests(self, targets: list[str] | None = None,
                  timeout: float = 300.0) -> tuple[str, str]:
        """跑 pytest，返回 (结果, 输出)。

        结果取值与 search.py 的 PASS/FAIL/ERROR 对齐：
          全绿 -> pass；有失败 -> fail；超时/无法运行 -> error
        """
        args = ["python", "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider"]
        args.extend(targets or ["tests/"])
        try:
            r = subprocess.run(
                args, cwd=self.info.path, capture_output=True, text=True,
                timeout=timeout, encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired:
            return "error", f"测试超时（{timeout}s）"
        except Exception as e:
            return "error", f"无法运行测试: {type(e).__name__}: {e}"

        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode == 0:
            return "pass", out
        # pytest 退出码 1 = 有测试失败；2+ = 收集/运行错误
        return ("fail" if r.returncode == 1 else "error"), out


def user_tree_is_clean(repo: str) -> tuple[bool, str]:
    """确认用户工作区未被本工具改动（测试用的独立断言辅助）。

    返回 (是否干净, 说明)。非 git 仓库返回 (True, "非 git，无法校验")。
    """
    try:
        r = subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return True, "非 git 仓库"
        dirty = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
        # worktree 元数据目录不算工作区改动
        dirty = [d for d in dirty if ".necessity" not in d]
        return (not dirty), ("\n".join(dirty[:10]) if dirty else "")
    except Exception as e:
        return True, f"校验失败: {e}"
