"""隔离分支管理 —— 规范 §8.2「`reduce/sandbox.py` 的 git worktree 隔离」白送。

## 为什么复用而不是新建

规范 §8.2 把「git worktree 隔离」列为可复用资产，理由是它的语义正好够用：
每个候选在自己的 worktree 里生成与验证，**互不干扰、不影响主工作区**。

而 `reduce/sandbox.py` 已经踩过这条路上的坑（隔离模式判定、worktree 清理、
非 git 仓库降级），重写一遍等于重踩一遍。

## 一条安全要求（规范 §8.9）

    「所有候选在独立 worktree 中，丢弃即清理，**不影响主工作区**」

所以本模块**只读**主工作区，所有写操作都发生在 worktree 里。
`verify_isolation()` 提供可断言的检查 —— 那句「不影响主工作区」
必须是能验证的事实，而不是一句设计说明。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("necessity.compete.arena")


@dataclass
class ArenaStats:
    """竞争场地的生命周期统计（规范 §8.6 要求「worktree 清理记录」）。"""

    created: list[str] = field(default_factory=list)
    cleaned: list[str] = field(default_factory=list)
    mode: str = ""
    degraded: bool = False

    @property
    def leaked(self) -> list[str]:
        """创建了但没清理的 —— 非空说明有资源泄漏。"""
        return [c for c in self.created if c not in self.cleaned]

    def to_dict(self) -> dict:
        return {"created": list(self.created), "cleaned": list(self.cleaned),
                "mode": self.mode, "degraded": self.degraded,
                "leaked": self.leaked}


class Arena:
    """为每个候选提供一个隔离环境。

    用法：
        arena = Arena(repo)
        with arena.candidate("minimal") as workdir:
            ... 生成与验证 ...
        print(arena.stats().leaked)     # 应为空

    上下文管理器保证**异常路径也清理** —— 否则一个候选崩了就会留下垃圾
    worktree，而下一次运行会因为「同名分支已存在」而失败（且报错信息
    指向分支冲突，与真因无关）。
    """

    def __init__(self, repo: str | Path, *, base_commit: str = "") -> None:
        self.repo = str(Path(repo).resolve())
        self.base_commit = base_commit
        self._stats = ArenaStats()

    def stats(self) -> ArenaStats:
        return self._stats

    def candidate(self, candidate_id: str) -> "_CandidateSandbox":
        return _CandidateSandbox(self, candidate_id)


class _CandidateSandbox:
    """单个候选的隔离环境（`with` 语句用）。"""

    def __init__(self, arena: Arena, candidate_id: str) -> None:
        self.arena = arena
        self.candidate_id = candidate_id
        self.sandbox = None
        self.info = None
        self.error = ""

    def __enter__(self):
        try:
            from backend.necessity.reduce.sandbox import Sandbox

            self.sandbox = Sandbox(self.arena.repo, self.arena.base_commit)
            self.info = self.sandbox.create()
            self.arena._stats.created.append(self.candidate_id)
            self.arena._stats.mode = getattr(self.info, "mode", "")
            self.arena._stats.degraded = bool(getattr(self.info, "degraded", False))
            if self.arena._stats.degraded:
                logger.warning(
                    "候选 %s 的隔离降级为目录复制（非 git 仓库或 worktree 失败）"
                    "—— 功能等价但更慢", self.candidate_id)
            return self
        except Exception as e:
            # 建不起来不抛 —— 让该候选被标记为 error，其余候选继续。
            # 规范 §8.6 要求给出「失败候选的淘汰原因」。
            self.error = f"{type(e).__name__}: {e}"
            logger.warning("候选 %s 的隔离环境创建失败: %s", self.candidate_id, self.error)
            return self

    def __exit__(self, *exc) -> None:
        self.cleanup()

    @property
    def workdir(self) -> Path | None:
        """隔离环境的工作目录。建不起来时为 None。"""
        if self.sandbox is None or self.info is None:
            return None
        return Path(getattr(self.info, "path", ""))

    def cleanup(self) -> None:
        """清理。**必须成功** —— 泄漏的 worktree 会让下次运行出现
        与真因无关的「分支已存在」错误。"""
        if self.sandbox is None:
            return
        try:
            self.sandbox.cleanup()
        except Exception as e:
            logger.warning("候选 %s 的隔离环境清理失败: %s", self.candidate_id, e)
        finally:
            self.arena._stats.cleaned.append(self.candidate_id)
            self.sandbox = None

    def run_tests(self, targets: list[str] | None = None):
        """在隔离环境里跑测试。返回 `(结果, 输出)`。

        没有可用环境时返回 `("error", 原因)` —— 与「测试失败」严格区分：
        前者是「这次没测成」，后者是「测了、没过」。
        """
        if self.sandbox is None:
            return "error", self.error or "隔离环境不可用"
        try:
            return self.sandbox.run_tests(targets)
        except Exception as e:
            return "error", f"{type(e).__name__}: {e}"

    def read(self, relpath: str) -> str:
        if self.sandbox is None:
            return ""
        try:
            return self.sandbox.read(relpath)
        except Exception:
            return ""


def verify_isolation(repo: str | Path, *, before: str = "") -> tuple[bool, str]:
    """确认主工作区未被竞争影响（规范 §8.9 的可断言形式）。

    `before` 是竞争**之前**的 `git status --porcelain` 输出；
    不传则只检查当前状态是否干净。

    为什么需要这个：规范 §8.9 说「丢弃即清理，**不影响主工作区**」。
    那句保证必须是能验证的事实 —— 否则某个候选偷偷改了主工作区，
    没有任何检查会发现，而症状（用户发现代码被改了）与真因（A6 泄漏）
    相隔很远。
    """
    from backend.necessity.reduce.sandbox import user_tree_is_clean

    ok, dirty = user_tree_is_clean(str(repo))
    if not ok:
        return False, f"主工作区有未预期改动：{dirty[:200]}"
    if before:
        import subprocess
        r = subprocess.run(["git", "status", "--porcelain"], cwd=str(repo),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30)
        now = (r.stdout or "").strip()
        if now != before.strip():
            return False, "主工作区状态与竞争前不一致"
    return True, "主工作区未被影响"


__all__ = ["Arena", "ArenaStats", "verify_isolation"]
