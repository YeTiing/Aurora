"""文件状态表 —— Context Paging 的失效判定（CONTEXT_PAGING.md §6.4）。

状态机（本模块是它唯一的权威实现）：

    unknown ──note_read（真读成功）──▶ fresh
    fresh   ──after_write("agent")──▶ dirty
    fresh   ──after_write("other")──▶ stale
    dirty   ──note_read（校验读）───▶ fresh
    stale   ──note_read（重读）─────▶ fresh
    stale   ──mark_missing（读失败）▶ unknown
    任意    ──after_write("other")──▶ stale

为什么 `dirty` 与 `stale` 必须分开（这是整个能力最容易被做错的地方）：

  * `dirty` = Agent 自己刚写的。内容很可能已经是新的，只是没有写进状态表；
    下次访问做一次校验读即可信任，**不需要**当作外部改动而整读。
  * `stale` = 外部（git checkout / formatter / 另一个进程）改的。缓存内容
    很可能已经过时，**不能直接服务**，必须重读。

合并两者会出现两种对称的失败：把自己的改动当外部改动 → 每次都重读（没收益）；
把外部改动当自己的 → 服务陈旧内容（正确性事故，且是静默的）。

存储复用 `core/index/store.py` 的 `file_read_log` 表（INTEGRATION.md §6.2
把它定义为会话级状态表）。本模块**不新增表、不复制存储**。
"""
from __future__ import annotations

import os
import time
from enum import Enum
from typing import Any

from ..index.store_schema import normalize_relpath, normalize_workspace
from ..index.symbols import file_content_hash

__all__ = ["FileState", "FileStateTable", "to_relpath"]


class FileState(str, Enum):
    """状态表取值的封闭集合，与 store_schema.VALIDITY_STATES 一致。"""

    FRESH = "fresh"
    STALE = "stale"
    DIRTY = "dirty"
    UNKNOWN = "unknown"


def to_relpath(path: str, workspace: str) -> str:
    """把宿主可能传来的绝对路径收敛成 workspace 相对路径。

    `normalize_relpath` 有意拒绝绝对路径（相对路径是存储契约）。但宿主
    （Aurora）的工具参数经常是绝对路径，所以这里在 workspace 内做一次
    相对化；不在 workspace 内的路径原样交给 normalize 去报错。
    """
    try:
        return normalize_relpath(path)
    except ValueError:
        pass
    try:
        rel = os.path.relpath(os.path.abspath(path), os.path.abspath(workspace))
    except (ValueError, OSError):
        raise ValueError(f"path is not workspace-relative: {path!r}") from None
    return normalize_relpath(rel.replace("\\", "/"))


class FileStateTable:
    """会话级文件状态表，落在 `file_read_log` 上。

    `hash`（内容版本）不在该表的列里，所以内存里保留一份 `_hashes` 映射，
    由 note_read / fresh 命中时填充。压缩契约需要 hash 来生成符号索引，
    而它又**不得读 file_content**（不变量 1），因此这份内存映射是必需的。
    进程重启后 hash 丢失 → 索引降级为「无 hash 的清单」，不会报错。
    """

    def __init__(self, store: Any, workspace: str, session_id: str = "default"):
        self.store = store
        self.workspace = workspace
        self.session_id = session_id
        self._hashes: dict[str, str] = {}
        # 私有 `_read` 不可用时的内存兜底（例如传入的是极简 fake store）。
        self._local: dict[str, dict] = {}

    # ── 查询 ────────────────────────────────────────────────────────

    def state(self, path: str) -> FileState:
        """当前状态。无记录 = unknown（调用方据此走「必须真读」）。"""
        rel = to_relpath(path, self.workspace)
        row = None
        if self.store is not None:
            try:
                row = self.store.get_file_read_log(self.session_id, self.workspace, rel)
            except Exception:
                row = None
        if row is None:
            row = self._local.get(normalize_relpath(rel))
        if not row:
            return FileState.UNKNOWN
        try:
            return FileState(str(row.get("validity") or "unknown"))
        except ValueError:
            return FileState.UNKNOWN

    def entry(self, path: str) -> dict | None:
        rel = to_relpath(path, self.workspace)
        if self.store is not None:
            try:
                row = self.store.get_file_read_log(self.session_id, self.workspace, rel)
                if row:
                    return row
            except Exception:
                pass
        return self._local.get(normalize_relpath(rel))

    def entries(self) -> list[dict]:
        """本会话所有状态条目，按 last_read_at 倒序（压缩注入用）。

        Store 只提供单路径查询，没有「本会话全部条目」的公开方法，所以这里
        直接查 `file_read_log`（只读，且不是 file_content —— 压缩不变量 1）。
        `_read` 不可用时退回内存兜底。
        """
        ws = normalize_workspace(self.workspace)
        read = getattr(self.store, "_read", None) if self.store is not None else None
        if read is not None:
            try:
                with read() as conn:
                    cur = conn.execute(
                        "SELECT path, read_count, validity, last_read_at FROM file_read_log "
                        "WHERE session_id=? AND workspace=? ORDER BY last_read_at DESC",
                        (self.session_id, ws),
                    )
                    rows = [dict(r) for r in cur.fetchall()]
                if rows:
                    return rows
            except Exception:
                pass
        return sorted(
            self._local.values(), key=lambda r: r.get("last_read_at") or 0.0, reverse=True
        )

    def cached_hash(self, path: str) -> str:
        return self._hashes.get(normalize_relpath(str(path)), "")

    def remember_hash(self, path: str, content_hash: str) -> None:
        if content_hash:
            self._hashes[normalize_relpath(str(path))] = content_hash

    # ── 状态迁移（唯一写入口）────────────────────────────────────────

    def note_read(self, path: str, content: str, *, mtime: float | None = None,
                  at: float | None = None) -> str:
        """真读成功：写内容 + 置 `fresh`。unknown/stale/dirty 都汇到这里。

        这是状态机的「归位」动作 —— 每次真读都必须调用它，否则状态表
        与磁盘会长期漂移（静默不一致，最危险的一类 bug）。
        """
        rel = to_relpath(path, self.workspace)
        if mtime is None:
            # 调用方常常只给内容。没有 mtime 的话 `read_file` 的快速路径
            # 就无法发现外部改动（缓存 mtime=0 会被当成「不可比」而放行），
            # 于是这里补一次 stat —— 一次 syscall 换掉一整类静默陈旧读。
            try:
                mtime = os.stat(os.path.join(self.workspace, rel)).st_mtime
            except OSError:
                mtime = None
        digest = file_content_hash(content)
        if self.store is None:
            return digest
        self.store.put_file_content(self.workspace, rel, content, mtime=mtime, indexed_at=at)
        self.store.log_file_read(
            self.session_id, self.workspace, rel, validity=FileState.FRESH.value, at=at
        )
        self.remember_hash(rel, digest)
        self._touch_local(rel, FileState.FRESH.value, at, count=1)
        return digest

    def mark_write(self, path: str, writer: str) -> FileState:
        """写文件后迁移状态。writer 的语义见 INTEGRATION.md §3。

        `writer="agent"` → dirty（自己改的，下次校验读即可）
        其它（"other"/"git"/"formatter"/…）→ stale（外部改动，下次必须重读）
        外部改动优先级最高：即使刚才是 dirty，外部又改了也必须 stale。
        """
        new = FileState.DIRTY if writer == "agent" else FileState.STALE
        self._set_validity(path, new.value)
        return new

    def mark_missing(self, path: str) -> None:
        """读失败 / 文件被删除：回退到 unknown，下次访问必须真读。

        CONTEXT_PAGING.md 的状态图把这一条画成 stale → unknown（§7 表格则
        写「标记 stale」）。这里取状态图：读失败意味着我们连「缓存对应
        哪个版本」都不确定了，unknown 比 stale 更诚实。
        """
        rel = to_relpath(path, self.workspace)
        self._hashes.pop(normalize_relpath(rel), None)
        self._set_validity(rel, FileState.UNKNOWN.value)

    # ── 落库细节 ────────────────────────────────────────────────────

    def _set_validity(self, path: str, validity: str) -> None:
        """只改 validity，**不增加 read_count**。

        为什么不用 `log_file_read`：它无条件 `read_count+1`，把「写」记成
        「读」会污染 R / R_waste 的分子分母。Store 没有公开的 setter，
        所以优先用它的 `_write` 事务做一次定点 UPDATE（不修改 index 层），
        拿不到 `_write` 时退回 `log_file_read`（可接受，只影响计数精度）。
        """
        rel = to_relpath(path, self.workspace)
        ws = normalize_workspace(self.workspace)
        write = getattr(self.store, "_write", None) if self.store is not None else None
        if write is not None:
            try:
                with write() as conn:
                    cur = conn.execute(
                        "UPDATE file_read_log SET validity=? "
                        "WHERE session_id=? AND workspace=? AND path=?",
                        (validity, self.session_id, ws, normalize_relpath(rel)),
                    )
                    if getattr(cur, "rowcount", 0) == 0:
                        conn.execute(
                            "INSERT INTO file_read_log "
                            "(session_id, workspace, path, read_count, validity, last_read_at) "
                            "VALUES (?,?,?,0,?,?)",
                            (self.session_id, ws, normalize_relpath(rel), validity, time.time()),
                        )
            except Exception:
                pass
            else:
                self._touch_local(rel, validity, None, count=0)
                return
        if self.store is not None:
            try:
                self.store.log_file_read(self.session_id, self.workspace, rel, validity=validity)
            except Exception:
                pass
        self._touch_local(rel, validity, None, count=0)

    def _touch_local(self, rel: str, validity: str, at: float | None, *, count: int) -> None:
        key = normalize_relpath(rel)
        row = self._local.setdefault(
            key, {"path": key, "read_count": 0, "validity": validity, "last_read_at": 0.0}
        )
        row["validity"] = validity
        row["read_count"] = int(row.get("read_count") or 0) + count
        row["last_read_at"] = time.time() if at is None else at
