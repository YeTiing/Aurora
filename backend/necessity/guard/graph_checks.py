"""graph_checks.py —— 依赖调用图的两类结构约束：call_chain / impact_limit。

拆出来的理由：这两个是**唯二真正用到结构语义**的检查器（GUARD.md 的
核心卖点 H4「结构验证优于文本验证」），单独成文件便于对照实验里把
C 组（结构）与 C′ 组（纯正则）区分开 —— 后者不 import 本模块。

两者都依赖 core/index/impact.py。图不可用时一律记 `unsupported` 并
返回 None（§8.2：LSP 不可用 → Guard 不做结构类约束），**绝不猜**。
"""
from __future__ import annotations

from backend.necessity.hooks import FileChange

from .checker import CheckContext, Violation, matches_any
from .spec import StructuredConstraint

__all__ = ["CHAIN_DEPTH", "check_call_chain", "check_impact_limit", "reachable_files"]

#: call_chain 的 BFS 深度上限 —— 再深也超出「改动是否在范围内」的判定需要
CHAIN_DEPTH = 6


def check_call_chain(sc: StructuredConstraint, changes: list[FileChange],
                     ctx: CheckContext) -> Violation | None:
    """只准改 root 可达的文件 —— 图上可达性。"""
    if ctx.store is None:
        ctx.unsupported.append(sc.id)
        return None
    root = sc.scope.get("root")
    reachable = reachable_files(ctx.store, root, sc.scope.get("direction", "callees"))
    if reachable is None:
        ctx.unsupported.append(sc.id)
        return None
    bad = [c for c in changes if c.path and not matches_any(c.path, reachable)]
    if not bad:
        return None
    return Violation(sc.id, sc.type,
                     sc.message or f"改动了 {root}() 不可达的文件",
                     paths=[c.path for c in bad],
                     lines=sum(c.added + c.removed for c in bad),
                     detail=f"可达文件数: {len(reachable)}")


def check_impact_limit(sc: StructuredConstraint, changes: list[FileChange],
                       ctx: CheckContext) -> Violation | None:
    """影响面不超过 N 个文件 —— 用 core/index/impact.analyze 的调用方集合。"""
    if ctx.store is None:
        ctx.unsupported.append(sc.id)
        return None
    max_files = int(sc.scope.get("max_files") or 0)
    if max_files <= 0:
        return None
    affected: set[str] = {c.path for c in changes}
    anchors = sc.scope.get("symbols") or []
    try:
        from backend.necessity.index import impact as _impact
        from .checks import sig_key
        for spec in anchors:
            key = spec.get("id") or sig_key(spec.get("file", ""), spec.get("name", ""))
            imp = _impact.analyze(ctx.store, key, depth=2)
            if imp.degraded:
                ctx.unsupported.append(sc.id)
                return None
            for n in imp.callers:
                if n.file:
                    affected.add(str(n.file).replace("\\", "/"))
        if not anchors:
            # 文件起点：直接把被改文件的影响面按「改动文件数」计。
            # 没有符号锚点时无法做图扩展 —— 如实记 unsupported 并只用
            # 已确定的改动文件数判定（保守，不虚增影响面）。
            ctx.unsupported.append(f"{sc.id}(no-symbol-anchor)")
    except Exception:
        ctx.unsupported.append(sc.id)
        return None
    if len(affected) <= max_files:
        return None
    return Violation(sc.id, sc.type,
                     sc.message or f"影响面 {len(affected)} 个文件，超过上限 {max_files}",
                     paths=sorted(affected), detail=f"max_files={max_files}")


# ── 图能力 ──────────────────────────────────────────────────────

def reachable_files(store, root: str, direction: str) -> list[str] | None:
    """从 root 符号出发 BFS，返回可达文件的 glob 列表。None = 不可用。"""
    try:
        from backend.necessity.index import impact as _impact
        root_id = _resolve_root(store, root)
        if not root_id:
            return None
        imp = _impact.analyze(store, root_id, depth=CHAIN_DEPTH)
        if imp.degraded:
            return None
        nodes = imp.callees if direction != "callers" else imp.callers
        files = {str(n.file).replace("\\", "/") for n in nodes if n.file}
        files.add(_root_file(store, root_id) or "")
        return [f for f in files if f] or None
    except Exception:
        return None


def _resolve_root(store, root: str) -> str | None:
    """把 root 名字解析为符号 id。

    store 无按名字反查接口（core/index/store.py 只有 get_symbol(id)），
    因此只接受调用方直接给出符号 id；解析失败 → None → unsupported，不猜。
    """
    try:
        if not root:
            return None
        if store.get_symbol(root):
            return root
        finder = getattr(store, "find_symbol_by_name", None)
        if callable(finder):
            got = finder(root)
            if isinstance(got, dict):
                return got.get("id")
            return str(got) if got else None
        return None
    except Exception:
        return None


def _root_file(store, root_id: str) -> str | None:
    try:
        row = store.get_symbol(root_id)
        return str(row.get("file")) if row and row.get("file") else None
    except Exception:
        return None
