"""checks.py —— 8 种可验证约束的具体检查器（GUARD.md §3.1）。

**本模块不得导入任何 LLM 客户端**（见 checker.py 顶部说明）。所有函数
签名统一为 (constraint, changes, ctx) -> Violation | None，由 checker.py
的 `check_constraints` 分派 —— 统一签名让预检/后检共用同一套逻辑。

「证据不足」时的纪律：记 `ctx.unsupported` 并返回 None（不判违反），
绝不把「没查」当成「没问题」。
"""
from __future__ import annotations

import fnmatch
import re

from backend.necessity.hooks import FileChange

from .checker import CheckContext, Violation, matches_any
from .spec import StructuredConstraint

__all__ = ["HANDLERS", "sig_key"]

_DEP_FILES = frozenset({
    "requirements.txt", "requirements-dev.txt", "pyproject.toml", "setup.py",
    "setup.cfg", "package.json", "go.mod", "Cargo.toml",
})

_STDLIB = frozenset({
    "os", "sys", "re", "json", "time", "math", "pathlib", "typing", "dataclasses",
    "collections", "itertools", "functools", "logging", "subprocess", "sqlite3",
    "threading", "contextlib", "hashlib", "tempfile", "shutil", "io", "abc",
    "enum", "copy", "uuid", "random", "string", "traceback", "asyncio", "unittest",
    "ast", "glob", "fnmatch", "textwrap", "difflib", "inspect", "warnings", "csv",
    "datetime", "urllib", "http", "socket", "struct", "base64", "secrets", "stat",
    "operator", "weakref", "types", "importlib", "platform", "getpass", "signal",
    "errno", "filecmp", "fileinput", "statistics", "decimal", "fractions", "queue",
    "heapq", "bisect", "array", "pickle", "marshal", "shelve", "dbm", "zlib",
    "gzip", "bz2", "lzma", "tarfile", "zipfile", "configparser", "argparse",
})


# ── file_scope ───────────────────────────────────────────────────

def check_file_scope(sc: StructuredConstraint, changes: list[FileChange],
                     ctx: CheckContext) -> Violation | None:
    patterns = list(sc.scope.get("patterns") or [])
    exclude = list(sc.scope.get("exclude") or [])
    bad = [c for c in changes
           if not matches_any(c.path, patterns) and not matches_any(c.path, exclude)]
    if not bad:
        return None
    return Violation(sc.id, sc.type, sc.message or "改动越出允许的文件范围",
                     paths=[c.path for c in bad],
                     lines=sum(c.added + c.removed for c in bad),
                     detail=f"允许范围: {patterns}")


# ── symbol_scope ─────────────────────────────────────────────────

def _symbol_allowed(name: str, pattern: str, qualifier: str) -> bool:
    if qualifier == "public" and name.startswith("_"):
        return False
    if qualifier == "private" and not name.startswith("_"):
        return False
    if pattern in ("*", ""):
        return True
    return fnmatch.fnmatch(name, pattern) or bool(re.search(pattern, name))


def check_symbol_scope(sc: StructuredConstraint, changes: list[FileChange],
                       ctx: CheckContext) -> Violation | None:
    """只准/不准改某类符号（pattern + qualifier 描述对象集合）。

    诚实说明：scan_workspace 只给到文件级改动，没有 hunk 级符号归属。
      - allowlist（只准改 X 类）：改动文件里出现**不在** scope 的符号 → 可疑
      - denylist（不准改 X 类）：出现 scope 内的符号 → 违反
    无符号索引时记 unsupported（§8.2 LSP 不可用降级）。
    """
    if not ctx.symbols:
        ctx.unsupported.append(sc.id)
        return None
    pat = sc.scope.get("pattern") or "*"
    qualifier = (sc.scope.get("qualifier") or "").lower()
    predicate = (sc.predicate or {}).get("kind", "allowlist")
    offenders: list[str] = []
    for c in changes:
        for sym in ctx.symbols.get(c.path, []) or []:
            name = str(sym.get("name") or "")
            if str(sym.get("kind") or "").lower() in ("module", "file"):
                continue
            inside = _symbol_allowed(name, pat, qualifier)
            if (predicate == "denylist" and inside) or (
                    predicate != "denylist" and not inside):
                offenders.append(f"{c.path}::{name}")
    if not offenders:
        return None
    return Violation(sc.id, sc.type,
                     sc.message or f"改动了 scope 外的符号（pattern={pat}）",
                     paths=sorted({o.split("::")[0] for o in offenders}),
                     detail=", ".join(offenders[:10]))


# ── signature_stable ─────────────────────────────────────────────

def sig_key(file: str, name: str) -> str:
    f = (file or "").replace("\\", "/")
    return f"{f}::{name}" if f else name


def _norm_sig(sig: str) -> str:
    return re.sub(r"\s+", "", str(sig or "")).rstrip(",")


def check_signature_stable(sc: StructuredConstraint, changes: list[FileChange],
                           ctx: CheckContext) -> Violation | None:
    """签名前后对比：只用 baseline_signatures vs signatures，纯字符串比较。"""
    if not ctx.signatures:
        ctx.unsupported.append(sc.id)
        return None
    bad: list[str] = []
    for spec in sc.scope.get("symbols") or []:
        key = spec.get("id") or sig_key(spec.get("file", ""), spec.get("name", ""))
        old = ctx.baseline_signatures.get(key)
        new = ctx.signatures.get(key)
        if old is None or new is None:
            # 只关心「签名被改动」；符号消失/新增不由此约束判定
            continue
        if _norm_sig(old) != _norm_sig(new):
            bad.append(f"{key}: {old!r} -> {new!r}")
    if not bad:
        return None
    return Violation(sc.id, sc.type, sc.message or "受限符号的签名被改动",
                     paths=[c.path for c in changes], detail="; ".join(bad[:5]))


# ── dependency_frozen ────────────────────────────────────────────

def check_dependency_frozen(sc: StructuredConstraint, changes: list[FileChange],
                            ctx: CheckContext) -> Violation | None:
    """不新增第三方依赖：manifest 差分 + import 差分。

    两条证据来源，任一命中即违反：
      1. ctx.extra["manifest_added"]（interceptor 从 diff 抽取的 {path: [pkg]})
      2. ctx.extra["imports_added"] —— 过滤标准库后仍有新增即为第三方

    两者都没提供时**不能算通过**（没证据 ≠ 没问题），记 unsupported。
    """
    manifest_added = ctx.extra.get("manifest_added")
    imports_added = ctx.extra.get("imports_added")
    if not manifest_added and imports_added is None:
        ctx.unsupported.append(sc.id)
        return None

    bad: list[str] = []
    for path, pkgs in (manifest_added or {}).items():
        if pkgs:
            bad.append(f"{path} 新增依赖: {', '.join(sorted(pkgs)[:5])}")
    if isinstance(imports_added, list):
        ext = [m for m in imports_added
               if str(m).split(".")[0] not in _STDLIB
               and not str(m).startswith("core.")]
        if ext:
            bad.append("新增第三方 import: " + ", ".join(sorted(set(ext))[:8]))
    if not bad:
        return None
    return Violation(sc.id, sc.type, sc.message or "新增了第三方依赖",
                     paths=[c.path for c in changes], detail="; ".join(bad[:5]))


# ── test_preserved ───────────────────────────────────────────────

def check_test_preserved(sc: StructuredConstraint, changes: list[FileChange],
                         ctx: CheckContext) -> Violation | None:
    """只有拿到测试结果才能判定 —— 没跑过不等于通过，记 unsupported。"""
    selectors = list(sc.scope.get("selectors") or [])
    if not selectors:
        return None
    failed = [s for s in selectors if ctx.test_results.get(s) is False]
    if failed:
        return Violation(sc.id, sc.type, sc.message or "受保护的测试不再通过",
                         paths=[], detail="失败: " + ", ".join(failed[:5]))
    missing = [s for s in selectors if s not in ctx.test_results]
    if missing:
        ctx.unsupported.append(sc.id)
    return None


# ── size_limit ───────────────────────────────────────────────────

def check_size_limit(sc: StructuredConstraint, changes: list[FileChange],
                     ctx: CheckContext) -> Violation | None:
    added = sum(c.added for c in changes)
    removed = sum(c.removed for c in changes)
    nfiles = len({c.path for c in changes})
    why: list[str] = []
    for key, actual, label in (("max_added", added, "新增"),
                               ("max_removed", removed, "删除"),
                               ("max_files", nfiles, "改动")):
        limit = sc.scope.get(key)
        if limit is not None and actual > int(limit):
            why.append(f"{label} {actual} > {limit}")
    if not why:
        return None
    return Violation(sc.id, sc.type, sc.message or "改动规模超过上限",
                     paths=[c.path for c in changes], lines=added + removed,
                     detail="; ".join(why))


def _lazy(name: str):
    """延迟绑定图检查器。

    为什么不在模块顶层直接 import graph_checks：graph_checks 反向依赖本
    模块的 sig_key，顶层 import 会成环。用函数包装后，导入推迟到真正要
    验证 call_chain / impact_limit 的时刻。
    """
    from . import graph_checks
    return getattr(graph_checks, name)


def _call_chain(sc, changes, ctx):
    return _lazy("check_call_chain")(sc, changes, ctx)


def _impact_limit(sc, changes, ctx):
    return _lazy("check_impact_limit")(sc, changes, ctx)


#: 类型 → 检查器。spec.validate 已保证只放行白名单类型；
#: 这里少一个 key 会显式进 unsupported，不会静默放行。
HANDLERS = {
    "file_scope": check_file_scope,
    "symbol_scope": check_symbol_scope,
    "signature_stable": check_signature_stable,
    "call_chain": _call_chain,
    "impact_limit": _impact_limit,
    "dependency_frozen": check_dependency_frozen,
    "test_preserved": check_test_preserved,
    "size_limit": check_size_limit,
}
