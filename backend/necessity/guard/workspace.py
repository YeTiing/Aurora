"""workspace.py —— 工作区变更扫描（GUARD.md §5.3 / §6）。

为什么是「扫描工作区」而不是「在每个工具上挂检查」（§6.2）：
    工具级检查漏一个工具就破防（Aurora 的真实教训：审批只接了
    shell_command，file_delete / network / mcp_tool / computer_use /
    code_exec 全绕过）。**工作区扫描与工具无关** —— 不管 Agent 用什么
    手段，只要文件落盘就能发现。这是权威数据源，工具级预检只是补充。

三档降级（§5.3.1）：
    L1 git（毫秒级，首选）→ L2 元数据树（非 git 仓库）→ L3 只扫参数声明
    的路径（不可靠，报告中必须标注「检测能力受限」）。
"""
from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.necessity.hooks import FileChange

logger = logging.getLogger("necessity.guard.workspace")

# 排除规则（GUARD.md §5.3.3，复用 Aurora tools/file_rw.py:28 的 SKIP_DIRS）
SKIP_DIRS: frozenset[str] = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv", ".tox",
    ".mypy_cache", ".pytest_cache", "dist", "build", ".next", "target",
    # 额外排除：Guard 自身的状态/备份/报告。
    # 不排除会导致**自我触发**：Guard 写备份 → 检测到变化 → 又触发检查。
    ".necessity",
})

SCAN_DEGRADED_L1 = "l1_git"
SCAN_DEGRADED_L2 = "l2_metadata"
SCAN_DEGRADED_L3 = "l3_declared"

# 单次扫描差异文件数超过此值 → 疑似 git 操作/外部进程，转 warn 不 rollback（§5.3.2）
BULK_DIFF_THRESHOLD = 1000
# 单次扫描耗时上限（ms），超过则建议降级为每轮一次（§5.3.2）
SCAN_TIME_BUDGET_MS = 500
# 工作区文件数上限，超过强制 L1（§5.3.2）
BIG_WORKSPACE_FILES = 50_000


@dataclass
class Snapshot:
    """一次工作区快照。L1 存 git 索引指纹，L2 存元数据树。/"""
    level: str = SCAN_DEGRADED_L2
    #: path -> (size, mtime_ns, hash?)  hash 延迟计算（§5.3.4）
    entries: dict[str, tuple[int, int, str]] = field(default_factory=dict)
    #: L1 专用：快照时刻的 git HEAD + 索引指纹，用于识别 git 操作
    git_head: str = ""
    took_ms: float = 0.0
    degraded: bool = False
    note: str = ""


def _is_skipped(rel: str) -> bool:
    parts = rel.replace("\\", "/").split("/")
    return any(p in SKIP_DIRS for p in parts[:-1]) or (parts and parts[-1] in SKIP_DIRS)


def _hash_file(p: Path) -> str:
    h = hashlib.sha256()
    try:
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


class WorkspaceScanner:
    """工作区扫描器。

    `scan()` 返回当前全量快照；`diff(before, after)` 把两个快照的差变成
    FileChange 列表。**归因由调用方的「工具调用窗口」界定**（§5.3.2）：
    窗口内 = Agent 引起，窗口外由 git 状态对比标为 external。
    """

    def __init__(self, workspace: str, *, use_git: bool = True,
                 max_hash_bytes: int = 2_000_000):
        self.workspace = str(Path(workspace).resolve()) if workspace else ""
        self.use_git = use_git and self._git_available()
        self.max_hash_bytes = max_hash_bytes

    # ── 能力探测 ─────────────────────────────────────────────────

    def _git_available(self) -> bool:
        if not self.workspace:
            return False
        try:
            r = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=self.workspace, capture_output=True, text=True, timeout=5,
            )
            return r.returncode == 0 and r.stdout.strip() == "true"
        except Exception:
            return False

    def _file_count(self) -> int:
        n = 0
        for _root, dirs, files in os.walk(self.workspace):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            n += len(files)
            if n > BIG_WORKSPACE_FILES:
                return n
        return n

    # ── 快照 ────────────────────────────────────────────────────

    def scan(self, *, with_hashes: bool = True) -> Snapshot:
        """拍快照。任何异常都返回空快照 + degraded，不抛（契约：故障放行）。"""
        if not self.workspace or not os.path.isdir(self.workspace):
            return Snapshot(level=SCAN_DEGRADED_L3, degraded=True,
                            note="工作区不存在或未配置")
        start = time.perf_counter()
        snap = Snapshot(level=SCAN_DEGRADED_L2)
        if self.use_git:
            snap.level = SCAN_DEGRADED_L1
            snap.git_head = self._git_head()
        try:
            snap.entries = self._walk(with_hashes=with_hashes)
        except Exception as e:
            logger.warning("工作区扫描失败: %s", e)
            snap.degraded = True
            snap.note = f"扫描异常 {type(e).__name__}，检测能力受限"
        snap.took_ms = (time.perf_counter() - start) * 1000
        if snap.took_ms > SCAN_TIME_BUDGET_MS:
            snap.note = (snap.note + f"; 单次扫描 {snap.took_ms:.0f}ms 超预算，"
                         "建议降级为每轮扫描一次")
        return snap

    def _git_head(self) -> str:
        try:
            r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.workspace,
                               capture_output=True, text=True, timeout=5)
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""

    def _walk(self, *, with_hashes: bool) -> dict[str, tuple[int, int, str]]:
        out: dict[str, tuple[int, int, str]] = {}
        ws = Path(self.workspace)
        for root, dirs, files in os.walk(ws):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in files:
                if name in SKIP_DIRS:
                    continue
                p = Path(root) / name
                try:
                    st = p.stat()
                except OSError:
                    continue
                rel = str(p.relative_to(ws)).replace("\\", "/")
                digest = ""
                # 延迟 hash（§5.3.4）：先只比 (size, mtime)，需要时才算内容哈希
                if with_hashes and st.st_size <= self.max_hash_bytes:
                    digest = _hash_file(p)
                out[rel] = (st.st_size, st.st_mtime_ns, digest)
        return out

    # ── 差分 ────────────────────────────────────────────────────

    def diff(self, before: Snapshot | None, after: Snapshot | None
             ) -> tuple[list[FileChange], dict[str, Any]]:
        """两个快照 → (变更列表, 归因信息)。

        归因规则（§6.3）：
          - 两边都有、内容变  → modified
          - 只有 after        → added
          - 只有 before       → deleted
          - 若期间 git HEAD 变了（checkout/stash/reset），整批标记
            by_agent=False —— 这不是 Agent 的改动
        """
        info: dict[str, Any] = {"external": False, "suspect_bulk": False, "note": ""}
        if before is None or after is None:
            info["note"] = "缺少快照，无法差分"
            return [], info

        before_e, after_e = before.entries, after.entries
        changes: list[FileChange] = []
        for rel, (size, mtime, digest) in after_e.items():
            if _is_skipped(rel):
                continue
            old = before_e.get(rel)
            if old is None:
                changes.append(FileChange(rel, "added", added=size, removed=0))
            elif (old[0], old[1]) != (size, mtime):
                if old[2] and digest and old[2] == digest:
                    continue  # 只碰了 mtime，内容没变
                changes.append(FileChange(rel, "modified", added=0, removed=0))
        for rel in before_e:
            if _is_skipped(rel) or rel in after_e:
                continue
            changes.append(FileChange(rel, "deleted", added=0,
                                      removed=before_e[rel][0]))

        # git 操作识别：HEAD 变化说明有 checkout/reset/stash 类操作介入
        if before.git_head and after.git_head and before.git_head != after.git_head:
            info["external"] = True
            info["note"] = (f"检测到 git HEAD 变化 "
                            f"{before.git_head[:8]}→{after.git_head[:8]}，"
                            "本次变更归为外部（非 Agent）")
            for c in changes:
                c.by_agent = False

        if len(changes) > BULK_DIFF_THRESHOLD:
            info["suspect_bulk"] = True
            info["note"] = (info["note"] + "; " if info["note"] else "") + \
                f"单次变更 {len(changes)} 个文件，疑似非 Agent 引起（§5.3.2 转 warn）"
        return changes, info

    # ── L3：只扫工具参数声明的路径（不可靠）──────────────────────

    @staticmethod
    def declared_paths(call_name: str, arguments: dict) -> list[str]:
        """从工具调用参数里提取「声明的写入路径」。

        L3 专用，**仅在前两档都不可用时兜底**，且必须在报告中标注
        「检测能力受限」—— Agent 完全可以通过 shell 重定向绕过。
        """
        out: list[str] = []
        if not isinstance(arguments, dict):
            return out
        for key in ("path", "file", "file_path", "filename", "target", "targets"):
            v = arguments.get(key)
            if isinstance(v, str) and v:
                out.append(v)
            elif isinstance(v, list):
                out.extend(str(x) for x in v if x)
        return out


def changes_to_dicts(changes: list[FileChange]) -> list[dict]:
    return [{"path": c.path, "kind": c.kind, "added": c.added,
             "removed": c.removed, "by_agent": c.by_agent} for c in changes or []]
