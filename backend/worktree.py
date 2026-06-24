"""Worktree Isolation — Git worktree for session sandboxing.

Creates isolated git worktrees per session so concurrent
agent sessions don't interfere with each other's file changes.
"""
from __future__ import annotations
import asyncio, os, subprocess, tempfile, shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class WorktreeInfo:
    path: str
    branch: str
    session_id: str
    parent_repo: str
    created_at: float = 0.0


class WorktreeManager:
    def __init__(self):
        self._worktrees: dict[str, WorktreeInfo] = {}

    async def create(self, session_id: str, repo_path: str,
                     branch: str = None) -> WorktreeInfo:
        """Create an isolated git worktree for a session."""
        import time
        branch = branch or f"aurora/{session_id[:8]}"
        wt_path = os.path.join(
            tempfile.gettempdir(), "aurora_worktrees", session_id[:12]
        )

        # Check if git repo
        git_dir = os.path.join(repo_path, ".git")
        if not os.path.isdir(git_dir) and not os.path.isfile(git_dir):
            raise ValueError(f"{repo_path} is not a git repository")

        # Create worktree
        cmd = ["git", "-C", repo_path, "worktree", "add",
               "--detach", wt_path]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                # Try with -b flag
                cmd = ["git", "-C", repo_path, "worktree", "add",
                       "-b", branch, wt_path, "HEAD"]
                proc = await asyncio.create_subprocess_exec(
                    *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await proc.communicate()
                if proc.returncode != 0:
                    raise RuntimeError(f"git worktree add failed: {stderr.decode()}")

            info = WorktreeInfo(
                path=wt_path, branch=branch, session_id=session_id,
                parent_repo=repo_path, created_at=time.time(),
            )
            self._worktrees[session_id] = info
            return info
        except FileNotFoundError:
            raise RuntimeError("git not found on system")

    async def remove(self, session_id: str):
        """Clean up a worktree when session ends."""
        info = self._worktrees.pop(session_id, None)
        if not info:
            return
        if os.path.isdir(info.path):
            cmd = ["git", "-C", info.parent_repo, "worktree", "remove",
                   "--force", info.path]
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            shutil.rmtree(info.path, ignore_errors=True)

    async def cleanup_all(self):
        for sid in list(self._worktrees.keys()):
            await self.remove(sid)

    def list_all(self) -> list[dict]:
        return [
            {"session_id": w.session_id, "path": w.path,
             "branch": w.branch, "parent_repo": w.parent_repo}
            for w in self._worktrees.values()
        ]

    def get_workspace(self, session_id: str) -> Optional[str]:
        info = self._worktrees.get(session_id)
        return info.path if info else None


worktree_manager = WorktreeManager()
