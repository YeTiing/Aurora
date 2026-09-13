# Agent 检查点系统 — 快照保存、恢复、历史回溯

from __future__ import annotations
import logging
logger = logging.getLogger("aurora")

import json, os, time, glob, shutil

from dataclasses import dataclass, field

from pathlib import Path

from typing import Any

from .state import AgentState



@dataclass

class Checkpoint:

    id: str

    state_dict: dict

    created_at: float = field(default_factory=time.time)

    step: int = 0

    label: str = ""

    metadata: dict = field(default_factory=dict)



class CheckpointManager:

    """管理 Agent 状态快照 — 文件系统 + 内存双存储"""



    def __init__(self, storage_dir: str | None = None, max_checkpoints: int = 50):

        self.storage_dir = Path(storage_dir) if storage_dir else Path.home() / ".aurora" / "checkpoints"

        self.storage_dir.mkdir(parents=True, exist_ok=True)

        self.max_checkpoints = max_checkpoints

        self._memory: dict[str, Checkpoint] = {}

        self._undo_stack: list[str] = []

        self._redo_stack: list[str] = []

        self._workspace_states: dict[str, dict] = {}



    def save(self, state: AgentState, label: str = "") -> str:

        """保存当前状态快照，返回 checkpoint_id"""

        cid = f"ckpt_{state.session_id}_{state.total_turns}_{int(time.time()*1000)}"

        checkpoint = Checkpoint(

            id=cid, state_dict=state.to_dict(),

            step=state.total_turns, label=label,

            metadata={"plan_progress": state.plan_progress(), "message_count": len(state.messages)}

        )

        self._memory[cid] = checkpoint



        # 写入文件

        try:

            ckpt_path = self.storage_dir / f"{cid}.json"

            ckpt_path.write_text(json.dumps(checkpoint.state_dict, ensure_ascii=False, indent=2), "utf-8")

        except Exception as e:

            logger.debug(f"checkpoint save failed for {cid}: {e}", exc_info=True)



        # 清理旧检查点

        self._prune()

        return cid



    def load(self, checkpoint_id: str) -> AgentState | None:

        """从检查点恢复状态"""

        # 先查内存

        ckpt = self._memory.get(checkpoint_id)

        if not ckpt:

            # 从文件加载

            ckpt_path = self.storage_dir / f"{checkpoint_id}.json"

            if ckpt_path.exists():

                try:

                    data = json.loads(ckpt_path.read_text("utf-8"))

                    ckpt = Checkpoint(id=checkpoint_id, state_dict=data)

                    self._memory[checkpoint_id] = ckpt

                except Exception:

                    return None

            else:

                return None



        return AgentState.from_dict(ckpt.state_dict)



    def get_latest(self, session_id: str) -> Checkpoint | None:

        """获取某会话的最新检查点"""

        prefix = f"ckpt_{session_id}_"
        matches = [c for c in self._memory.values() if c.id.startswith(prefix)]

        if matches:

            return max(matches, key=lambda c: c.created_at)

        # 从文件查找

        pattern = str(self.storage_dir / f"ckpt_{session_id}_*.json")

        files = glob.glob(pattern)

        if files:

            latest = max(files, key=os.path.getmtime)

            try:

                cid = Path(latest).stem

                data = json.loads(Path(latest).read_text("utf-8"))

                return Checkpoint(id=cid, state_dict=data, created_at=os.path.getmtime(latest))

            except Exception as e:

                logger.debug(f"checkpoint load failed: {e}", exc_info=True)

        return None



    def list_for_session(self, session_id: str) -> list[dict]:

        """列出某会话的所有检查点"""

        results = []

        for c in self._memory.values():

            if c.id.startswith(f"ckpt_{session_id}_"):

                results.append({"id": c.id, "step": c.step, "label": c.label, "created_at": c.created_at})

        return sorted(results, key=lambda r: r["created_at"])



    def _prune(self):

        """清理超出上限的检查点"""

        if len(self._memory) <= self.max_checkpoints:

            return

        sorted_ckpts = sorted(self._memory.values(), key=lambda c: c.created_at)

        to_remove = sorted_ckpts[:len(sorted_ckpts) - self.max_checkpoints]

        for c in to_remove:

            self._memory.pop(c.id, None)

            ckpt_path = self.storage_dir / f"{c.id}.json"

            if ckpt_path.exists():

                try: ckpt_path.unlink()

                except Exception: logger.debug("unexpected error", exc_info=True)





    def save_workspace_state(self, label: str = "", paths: list[str] | None = None,
                             workspace: str = ".") -> str:
        """记录即将被修改的文件的真实内容，供 undo 还原。

        paths: 本轮会被改动的文件（相对或绝对路径）。只快照这些文件，不做
        全工作区拷贝 —— 全量拷贝在大仓库上不可接受。

        此前该方法的 files_snapshot 直接等于 label 字符串（即根本不存内容），
        且 undo() 只移动栈指针不还原任何文件，于是 /checkpoint/undo 会返回
        undone=True 却什么都没回滚 —— 一个数据安全上的假象。
        """
        import hashlib, base64

        files: dict[str, dict] = {}
        for rel in (paths or []):
            try:
                fp = Path(rel)
                if not fp.is_absolute():
                    fp = Path(workspace) / fp
                fp = fp.resolve()
                if fp.is_file():
                    # 文本按 utf-8 存；二进制内容用 base64，避免 json 序列化破坏
                    raw_bytes = fp.read_bytes()
                    try:
                        text = raw_bytes.decode("utf-8")
                        files[str(fp)] = {"exists": True, "encoding": "utf-8", "content": text}
                    except UnicodeDecodeError:
                        files[str(fp)] = {"exists": True, "encoding": "base64",
                                          "content": base64.b64encode(raw_bytes).decode("ascii")}
                else:
                    # 记录"当时不存在"：undo 需要据此删除新建的文件
                    files[str(fp)] = {"exists": False, "encoding": "utf-8", "content": ""}
            except Exception:
                logger.debug(f"snapshot failed for {rel}", exc_info=True)

        state_summary = {
            "label": label,
            "timestamp": time.time(),
            "files": files,
            "file_count": len(files),
        }

        cid = f"ws_{int(time.time()*1000)}_{hashlib.md5(label.encode()).hexdigest()[:6]}"
        self._workspace_states[cid] = state_summary
        self._undo_stack.append(cid)
        self._redo_stack.clear()

        ws_path = self.storage_dir / f"{cid}.json"
        try:
            ws_path.write_text(json.dumps(state_summary, ensure_ascii=False), "utf-8")
        except Exception:
            logger.debug("workspace checkpoint persist failed", exc_info=True)

        return cid

    @staticmethod
    def _restore_files(state_summary: dict) -> dict:
        """按快照还原文件。返回 {restored, removed, failed} 统计。"""
        files = state_summary.get("files") or {}
        restored = removed = failed = 0

        for path_str, info in files.items():
            try:
                fp = Path(path_str)
                if not info.get("exists"):
                    # 快照时文件不存在 -> 该文件是本次新建的，回滚即删除
                    if fp.exists():
                        fp.unlink()
                        removed += 1
                    continue

                content = info.get("content", "")
                if info.get("encoding") == "base64":
                    import base64
                    data = base64.b64decode(content.encode("ascii"))
                    fp.parent.mkdir(parents=True, exist_ok=True)
                    fp.write_bytes(data)
                else:
                    fp.parent.mkdir(parents=True, exist_ok=True)
                    fp.write_text(content, encoding="utf-8")
                restored += 1
            except Exception as e:
                failed += 1
                logger.warning(f"restore failed for {path_str}: {e}")

        return {"restored": restored, "removed": removed, "failed": failed}



    def undo(self) -> str | None:

        """回滚最近一次工作区改动：真正把文件内容写回快照状态。

        返回 checkpoint_id；栈为空时返回 None。
        """
        if not self._undo_stack:
            return None

        cid = self._undo_stack.pop()
        self._redo_stack.append(cid)

        detail = self._workspace_states.get(cid)
        if detail is None:
            # 进程重启后内存态丢失，从磁盘恢复快照内容再还原
            detail = self._load_workspace_summary(cid)

        if detail and detail.get("files"):
            # 先记下"撤销前的当前内容"，redo 才有东西可恢复
            try:
                import base64
                after: dict[str, dict] = {}
                for path_str in detail["files"]:
                    fp = Path(path_str)
                    if fp.is_file():
                        raw_bytes = fp.read_bytes()
                        try:
                            after[path_str] = {"exists": True, "encoding": "utf-8",
                                               "content": raw_bytes.decode("utf-8")}
                        except UnicodeDecodeError:
                            after[path_str] = {"exists": True, "encoding": "base64",
                                               "content": base64.b64encode(raw_bytes).decode("ascii")}
                    else:
                        after[path_str] = {"exists": False, "encoding": "utf-8", "content": ""}
                detail["after_undo"] = after
            except Exception:
                logger.debug("failed to capture after-undo state", exc_info=True)

            stats = self._restore_files(detail)
            self._last_restore = stats
            logger.info(f"checkpoint undo {cid}: {stats}")
        else:
            # 没有任何文件信息：如实标注，避免调用方误以为已回滚
            self._last_restore = None
            logger.warning(f"checkpoint undo {cid}: no file snapshot available, nothing restored")

        return cid

    def _load_workspace_summary(self, cid: str) -> dict | None:
        """从磁盘读回工作区快照（内存态丢失时使用）。"""
        try:
            fp = self.storage_dir / f"{cid}.json"
            if fp.exists():
                data = json.loads(fp.read_text(encoding="utf-8"))
                self._workspace_states[cid] = data
                return data
        except Exception:
            logger.debug(f"load workspace summary failed for {cid}", exc_info=True)
        return None

    def last_restore(self) -> dict | None:
        """最近一次 undo 的还原统计，供路由如实回报。"""
        return getattr(self, "_last_restore", None)



    def redo(self) -> str | None:

        """重做被 undo 撤销的那次改动。

        实现方式：undo 时在快照里同时记下"撤销前的当前内容"，redo 即把
        那些内容写回。若没有这份记录则无法重做，返回 None 而不是假装成功。
        """
        if not self._redo_stack:
            return None

        cid = self._redo_stack.pop()
        self._undo_stack.append(cid)

        detail = self._workspace_states.get(cid) or self._load_workspace_summary(cid)
        after = (detail or {}).get("after_undo")
        if not after:
            logger.warning(f"checkpoint redo {cid}: no after-undo snapshot, nothing restored")
            self._last_restore = None
            return cid

        stats = self._restore_files({"files": after})
        self._last_restore = stats
        logger.info(f"checkpoint redo {cid}: {stats}")
        return cid



    def list_history(self) -> list[dict]:

        """List checkpoint history with undo/redo stack info."""

        history = []

        for cid in self._undo_stack:

            info = self._workspace_states.get(cid, {})

            history.append({

                "id": cid,

                "label": info.get("label", ""),

                "timestamp": info.get("timestamp", 0),

                "type": "undo_stack"

            })

        for cid in reversed(self._redo_stack):

            info = self._workspace_states.get(cid, {})

            history.append({

                "id": cid,

                "label": info.get("label", ""),

                "timestamp": info.get("timestamp", 0),

                "type": "redo_stack"

            })

        return history



    def clear_session(self, session_id: str):

        """清除某会话的所有检查点"""

        to_remove = [cid for cid in self._memory if session_id in cid]

        for cid in to_remove:

            self._memory.pop(cid, None)

            fpath = self.storage_dir / f"{cid}.json"

            if fpath.exists():

                try: fpath.unlink()

                except Exception: logger.debug("unexpected error", exc_info=True)



    def clear_all(self):

        self._memory.clear()

        if self.storage_dir.exists():

            shutil.rmtree(self.storage_dir, ignore_errors=True)

            self.storage_dir.mkdir(parents=True, exist_ok=True)



    def stats(self) -> dict:

        return {"memory_count": len(self._memory), "storage_dir": str(self.storage_dir)}


_checkpoint_manager: CheckpointManager | None = None


def get_checkpoint_manager() -> CheckpointManager:
    """进程级 CheckpointManager 单例。

    undo/redo/workspace 栈全部是实例内存（见 __init__），若每个请求 new 一个，
    路由看到的栈永远为空 —— 这正是 /checkpoint/undo 恒返回 "Nothing to undo" 的根因。
    AgentGraph 默认也复用同一实例，这样执行期 save_workspace_state 落的快照，
    路由才能读到。
    """
    global _checkpoint_manager
    if _checkpoint_manager is None:
        _checkpoint_manager = CheckpointManager()
    return _checkpoint_manager