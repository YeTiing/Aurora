"""builder.py —— 遍历仓库 → 采符号 → 查关系 → 写图。

性能是硬约束（INDEX.md Phase 1 坑点三）：
    100 文件 × N 符号 × 2~3 次请求 = **上万次 JSON-RPC 往返**。
    串行必然超时。必须**先批量 didOpen，再并发查询**。

验收标准（INDEX.md Phase 1）：100 文件仓库全量 build < 5 分钟。

单文件失败不中断整体（INDEX.md Phase 1 要求）：跳过并记录，
因为一个语法坏掉的文件不该让整个建图失败。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from .callgraph import edges_from_incoming, build_symbol_index
from .symbols import (
    extract_file_symbols,
    file_content_hash,
    uri_to_relpath,
)

logger = logging.getLogger("necessity.index.builder")

# 并发上限：LSP server 对并发请求有上限，打太猛会被限流或丢请求。
# 8 是保守值 —— 宁可慢一点也不愿丢请求（丢请求 = 静默少边 = 图不准）。
DEFAULT_CONCURRENCY = 8

# 排除目录（与 Aurora 的 SKIP_DIRS 一致，避免遍历到虚拟环境/构建产物）
SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", ".tox",
    ".mypy_cache", ".pytest_cache", "dist", "build", ".next", "target",
    ".eggs", "site-packages",
}


@dataclass
class BuildStats:
    files_scanned: int = 0
    files_indexed: int = 0
    files_failed: int = 0
    symbols: int = 0
    edges: int = 0
    errors: list[dict] = field(default_factory=list)
    duration_sec: float = 0.0
    _t0: float = 0.0

    def to_dict(self) -> dict:
        return {
            "files_scanned": self.files_scanned,
            "files_indexed": self.files_indexed,
            "files_failed": self.files_failed,
            "symbols": self.symbols,
            "edges": self.edges,
            "duration_sec": round(self.duration_sec, 2),
            "errors": self.errors[:20],
            "error_count": len(self.errors),
        }


def find_python_files(repo: Path, respect_gitignore: bool = True) -> list[Path]:
    """遍历仓库里的 .py 文件。

    gitignore 处理用最朴素的方式（前缀匹配常见模式），不引入 pathspec 依赖
    —— INDEX.md 要求「依赖尽量少，能用标准库就用标准库」。漏掉个别忽略项
    只会多扫几个文件，不影响正确性。
    """
    repo = Path(repo).resolve()
    out: list[Path] = []
    for p in repo.rglob("*.py"):
        try:
            parts = set(p.relative_to(repo).parts)
        except ValueError:
            continue
        if parts & SKIP_DIRS:
            continue
        out.append(p)
    return sorted(out)


async def _open_all(manager, files: list[Path], repo: Path) -> int:
    """批量 didOpen。

    必须等 didOpen 完成再查关系（INDEX.md Phase 1 坑点二）：
    未 open 的文件 pyright 不知道，查 references 会返回空 —— 且不报错。
    """
    opened = 0
    for f in files:
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
            await manager.open_file(str(f), content)
            opened += 1
        except Exception as e:
            logger.debug("didOpen failed for %s: %s", f, e)
    return opened


async def build(
    manager,
    store,
    repo: str,
    rel_to: str | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    session=None,
) -> BuildStats:
    """建图主流程。

    manager: LSP server manager（需提供 document_symbol / get_references /
             prepare_call_hierarchy / incoming_calls / open_file）
    store:   core.index.store（符号索引唯一真源）
    """
    stats = BuildStats()
    stats._t0 = time.perf_counter()
    repo_path = Path(repo).resolve()
    workspace = str(repo_path)

    files = find_python_files(repo_path)
    stats.files_scanned = len(files)
    if not files:
        stats.duration_sec = time.perf_counter() - stats._t0
        return stats

    # 阶段 1：批量 didOpen（让 pyright 建立全项目视图）
    await _open_all(manager, files, repo_path)

    # 阶段 2：逐个文件采符号（documentSymbol 快，不需要并发）
    all_symbols: list = []
    rel_by_abs: dict[str, str] = {}
    for f in files:
        try:
            rel = f.relative_to(repo_path).as_posix()
            rel_by_abs[str(f)] = rel
            content = f.read_text(encoding="utf-8", errors="replace")
            doc = await manager.get_document_symbols(str(f))
            if doc is None:
                stats.files_failed += 1
                stats.errors.append({"file": rel, "error": "documentSymbol 返回空"})
                continue
            syms = extract_file_symbols(doc, workspace, rel, content)
            all_symbols.extend(syms)
            # 文件内容必须入库（INTEGRATION.md §6）：lookup 按 content_hash
            # 反查符号、Context Paging 的 L1 索引都依赖它。
            # 原先只在传入 session 时才写 —— 而 build 的调用方从不传 session，
            # 导致 file_content 恒为空、Files 统计恒为 0。
            try:
                store.put_file_content(workspace, rel, content,
                                       token_count=len(content) // 4)
            except Exception as e:
                stats.errors.append({"file": rel, "error": f"内容写入失败: {e}"})
            stats.files_indexed += 1
        except Exception as e:
            # 单文件失败跳过并记录，不中断整体（INDEX.md Phase 1 要求）
            stats.files_failed += 1
            stats.errors.append({"file": str(f), "error": f"{type(e).__name__}: {e}"})

    stats.symbols = len(all_symbols)

    # 写入符号：按文件分组，因为 content_hash 是**文件级**的
    # （INTEGRATION.md §6.3：用哈希作符号有效性判据，文件变了旧符号自动失效）
    by_file: dict[str, list] = {}
    for s in all_symbols:
        by_file.setdefault(s.file, []).append(s)
    for rel, syms in by_file.items():
        try:
            store.upsert_symbols(
                workspace, rel, [s.to_dict() for s in syms],
                content_hash=syms[0].content_hash,
            )
        except Exception as e:
            stats.errors.append({"file": rel, "error": f"符号写入失败: {e}"})

    # 阶段 3：并发查关系
    # 只对「函数/方法」查（类的 incomingCalls 意义不同，第一版不查）
    index = build_symbol_index(all_symbols)
    targets = [s for s in all_symbols if s.kind in ("function", "method")]
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _one(sym):
        async with sem:
            try:
                # 采点必须用 selectionRange（已在 symbols.py 记录）
                item = await manager.prepare_call_hierarchy(
                    str(repo_path / sym.file), sym.start_line, sym.start_col
                )
                if not item:
                    return []
                item0 = item[0] if isinstance(item, list) else item
                incoming = await manager.incoming_calls(item0)
                if not incoming:
                    return []
                edges, _ = edges_from_incoming(incoming, workspace, sym.id, index)
                return edges
            except Exception as e:
                logger.debug("relations failed for %s: %s", sym.id, e)
                return []

    results = await asyncio.gather(*(_one(s) for s in targets))

    all_edges = [e for batch in results for e in batch]
    stats.edges = len(all_edges)
    if all_edges:
        # 边按文件写入（add_edges 的签名是 per-file）
        by_src_file: dict[str, list] = {}
        for e in all_edges:
            by_src_file.setdefault(e.file, []).append(e.to_dict())
        for rel, batch in by_src_file.items():
            try:
                store.add_edges(rel, batch)
            except Exception as e:
                stats.errors.append({"file": rel, "error": f"边写入失败: {e}"})

    stats.duration_sec = time.perf_counter() - stats._t0
    logger.info("build done: %s", stats.to_dict())
    return stats
