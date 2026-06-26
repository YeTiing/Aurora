"""git-worktree plugin — manage isolated git worktrees."""
from __future__ import annotations
import asyncio, os, json
from pathlib import Path

async def _git(*args: str, cwd: str = ".") -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode or 0, stdout.decode(), stderr.decode()

async def list_worktrees(repo_path: str = ".") -> list[dict]:
    """List all git worktrees."""
    code, out, err = await _git("worktree", "list", "--porcelain", cwd=repo_path)
    if code != 0:
        return []
    worktrees = []
    current = {}
    for line in out.split("\n"):
        line = line.strip()
        if not line:
            if current:
                worktrees.append(current)
                current = {}
            continue
        if line.startswith("worktree "):
            current["path"] = line[9:]
        elif line.startswith("HEAD "):
            current["head"] = line[5:]
        elif line.startswith("branch "):
            current["branch"] = line[19:]
        elif line.startswith("detached"):
            current["detached"] = True
        elif line.startswith("bare"):
            current["bare"] = True
    if current:
        worktrees.append(current)
    return worktrees

async def create_worktree(repo_path: str, branch: str, path: str) -> dict:
    """Create a new git worktree."""
    code, out, err = await _git("worktree", "add", "-b", branch, path, cwd=repo_path)
    return {"success": code == 0, "branch": branch, "path": path, "output": out, "error": err}

async def remove_worktree(worktree_path: str) -> dict:
    """Remove a git worktree."""
    code, out, err = await _git("worktree", "remove", "--force", worktree_path)
    return {"success": code == 0, "output": out, "error": err}

async def prune_worktrees(repo_path: str = ".") -> dict:
    """Prune stale worktree references."""
    code, out, err = await _git("worktree", "prune", cwd=repo_path)
    return {"success": code == 0, "output": out, "error": err}

__all__ = ["list_worktrees", "create_worktree", "remove_worktree", "prune_worktrees"]
