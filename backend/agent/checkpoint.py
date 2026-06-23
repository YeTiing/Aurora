# Agent 检查点系统 — 快照保存、恢复、历史回溯
from __future__ import annotations
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
        except Exception:
            pass

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
        matches = [c for c in self._memory.values() if session_id in c.id]
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
            except Exception:
                pass
        return None

    def list_for_session(self, session_id: str) -> list[dict]:
        """列出某会话的所有检查点"""
        results = []
        for c in self._memory.values():
            if session_id in c.id:
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
                except: pass


    def save_workspace_state(self, label: str = "") -> str:
        """Save current workspace state snapshot. Returns checkpoint_id."""
        import hashlib
        state_summary = {
            "label": label,
            "timestamp": time.time(),
            "files_snapshot": label
        }
        cid = f"ws_{int(time.time()*1000)}_{hashlib.md5(label.encode()).hexdigest()[:6]}"
        self._workspace_states[cid] = state_summary
        self._undo_stack.append(cid)
        self._redo_stack.clear()
        # Also save to disk
        ws_path = self.storage_dir / f"{cid}.json"
        try:
            ws_path.write_text(__import__('json').dumps(state_summary, ensure_ascii=False), "utf-8")
        except Exception:
            pass
        return cid

    def undo(self) -> str | None:
        """Undo last action. Returns checkpoint_id or None."""
        if not self._undo_stack:
            return None
        cid = self._undo_stack.pop()
        self._redo_stack.append(cid)
        return cid

    def redo(self) -> str | None:
        """Redo last undone action. Returns checkpoint_id or None."""
        if not self._redo_stack:
            return None
        cid = self._redo_stack.pop()
        self._undo_stack.append(cid)
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
                except: pass

    def clear_all(self):
        self._memory.clear()
        if self.storage_dir.exists():
            shutil.rmtree(self.storage_dir, ignore_errors=True)
            self.storage_dir.mkdir(parents=True, exist_ok=True)

    def stats(self) -> dict:
        return {"memory_count": len(self._memory), "storage_dir": str(self.storage_dir)}