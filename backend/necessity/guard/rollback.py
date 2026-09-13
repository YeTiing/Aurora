"""rollback.py —— 违反后的回滚（INTEGRATION.md §7.1 的五条硬性约束）。

回滚会**写用户的文件系统**，是全系统唯一有破坏性的操作，所以每条限制
都在代码里显式落实，不依赖调用方的善意：

  1. 只能回滚**本任务内 Agent 写过的**文件 → `_write_log`
  2. 回滚前**先备份**到 `.necessity/backup/<session>/<path>`
  3. **不得删除用户文件** → 回滚 = 恢复内容；只有「本次 Agent 新建的
     文件」才允许恢复回「不存在」的状态
  4. **不得触碰工作区外路径** → `contain()`，按路径分量判断
     （绝不用 str.startswith：`/data/proj-evil` 曾因此逃逸 `/data/proj`）
  5. **回滚失败 → 告警 + 转人工**，绝不静默失败

写入日志的语义（谁算 Agent）：
  * 后检扫到的变更默认 by_agent=True（窗口内 = Agent 引起）
  * `after_write(writer='other')` / git HEAD 变化会覆盖为 other
  * 一旦标记 agent 就不再被 other 覆盖 —— 宁可保守（少回滚）也不误伤
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from .intent import contain

logger = logging.getLogger("necessity.guard.rollback")

__all__ = ["RollbackManager"]

#: 回滚结果状态
STATUS_RESTORED = "restored"              # 内容已恢复到基线
STATUS_RESTORED_ABSENT = "restored_absent"  # 本任务新建 → 恢复为「不存在」
STATUS_SKIPPED = "skipped"                # 不该回滚（非 Agent / 已不存在）
STATUS_REFUSED = "refused"                # 越界，拒绝操作
STATUS_ESCALATED = "escalated"            # 失败，转人工


class RollbackManager:
    """维护任务级写入日志，并执行受约束的回滚。"""

    def __init__(self, workspace: str, session_id: str,
                 backup_dir: str = ".necessity/backup"):
        self.workspace = str(workspace or ".")
        self.session_id = str(session_id or "default")
        self.backup_root = Path(self.workspace).resolve() / backup_dir / self.session_id
        self._write_log: dict[str, str] = {}
        self._initial_paths: set[str] = set()
        self._explicit: set[str] = set()

    def reset(self) -> None:
        self._write_log.clear()
        self._initial_paths.clear()
        self._explicit.clear()

    def set_initial_paths(self, paths: set[str]) -> None:
        """记录任务开始时已存在的文件 —— 用于区分「Agent 新建」与「原有」。"""
        self._initial_paths = set(paths or ())

    def note_writer(self, path: str, writer: str, *, explicit: bool = False) -> None:
        """登记某路径的写入者。writer: 'agent' | 'other'。

        默认优先级 agent > other：一旦判定为 Agent 写入就不再被其它来源
        覆盖，避免 git 操作把真正的越界改动「洗白」成外部变更。

        `explicit=True` 用于**宿主直接声明**写入者（after_write 钩子、
        git HEAD 变化判定）—— 此时宿主知道得比扫描器更准，允许覆盖。
        自动扫描走默认的保守合并策略。
        """
        if not path:
            return
        if explicit:
            self._explicit.add(path)
        if writer == "agent":
            self._write_log[path] = "agent"
        elif explicit or self._write_log.get(path) != "agent":
            self._write_log[path] = "other"

    def is_explicit(self, path: str) -> bool:
        """该路径的归因是否由宿主直接声明（优先级最高）。"""
        return path in self._explicit

    def is_agent_written(self, path: str) -> bool:
        return self._write_log.get(path) == "agent"

    # ── 主流程 ──────────────────────────────────────────────────

    def rollback(self, paths: list[str], constraint_id: str,
                 baseline: Callable[[str], str | None]) -> list[dict]:
        """回滚一组路径。baseline(rel) 返回该文件的原始内容（可能为 None）。"""
        return [self._one(p, constraint_id, baseline)
                for p in dict.fromkeys(paths or [])]

    def _one(self, relpath: str, cid: str,
             baseline: Callable[[str], str | None]) -> dict:
        rel = str(relpath).replace("\\", "/").lstrip("./")
        base = {"path": rel, "constraint_id": cid}

        # ① 路径必须落在工作区内（先于任何文件操作）
        try:
            target = contain(self.workspace, rel)
        except PermissionError as e:
            logger.warning("回滚拒绝越界路径: %s", rel)
            return {**base, "status": STATUS_REFUSED, "escalate": True,
                    "reason": str(e)}

        # ② 只能是本任务 Agent 写过的文件
        if not self.is_agent_written(rel):
            return {**base, "status": STATUS_SKIPPED,
                    "reason": "该文件当前归因为非 Agent 写入"
                              "（可能是用户/git/外部进程改动），不满足「只回滚"
                              "本任务 Agent 写过的文件」"}
        if not target.exists():
            return {**base, "status": STATUS_SKIPPED, "reason": "文件已不存在"}

        # ③ 回滚前先备份（备份失败就不敢动原文件）
        try:
            backup = self._backup(target, rel)
        except OSError as e:
            logger.warning("备份失败，拒绝回滚 %s: %s", rel, e)
            return {**base, "status": STATUS_ESCALATED, "escalate": True,
                    "reason": f"备份失败，拒绝回滚: {e}"}

        # ④ 恢复内容（不是删除）
        original = baseline(rel)
        try:
            if original is not None:
                target.write_text(original, encoding="utf-8")
                return {**base, "status": STATUS_RESTORED, "backup": str(backup)}
            if rel not in self._initial_paths:
                target.unlink()      # Agent 新建的 → 恢复为「不存在」
                return {**base, "status": STATUS_RESTORED_ABSENT,
                        "backup": str(backup)}
            # 原有文件却没有基线 → 无法安全恢复，转人工而非乱写
            return {**base, "status": STATUS_ESCALATED, "escalate": True,
                    "backup": str(backup),
                    "reason": "无基线内容可恢复，已告警转人工（不静默失败）"}
        except OSError as e:
            logger.error("回滚写回失败 %s: %s", rel, e)
            return {**base, "status": STATUS_ESCALATED, "escalate": True,
                    "backup": str(backup), "reason": f"写回失败: {e}"}

    def _backup(self, target: Path, rel: str) -> Path:
        """把文件**当前（被改后）**的内容留档，便于事后审计与人工复原。"""
        root = self.backup_root.resolve()
        dest = (root / rel.replace("/", "/")).resolve()
        if not dest.is_relative_to(root):
            raise OSError(f"备份路径越界: {rel}")   # 纵深防御
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(target.read_bytes())
        return dest
