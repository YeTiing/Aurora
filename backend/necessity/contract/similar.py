"""相似实现提取 —— 规范 §4.5 的「同类模块 3+ 个采用同一模式」（⭐⭐）。

从 `extract.py` 拆出（该文件触 300 行上限）。职责上也该分：
    extract.py  基于**单文件 AST** 提取（tests / exceptions / types）
    similar.py  基于**跨模块模式比较**（需要看到多个模块才能判「普遍」）

后者需要「多处重复才算数」的判据，与前者的单点证据语义不同。
"""
from __future__ import annotations

import re

from .schema import ContractCandidate, compute_confidence

# 相似实现被判为「同一模式」所需的最少重复次数（规范 §4.5：「3+ 个」）
SIMILAR_MIN_OCCURRENCES = 3

# 判为「软删除」的命名模式。规范 §4.3 的例子是「删除必须软删除」。
_SOFT_DELETE_HINT = re.compile(r"(delete|remove|destroy)", re.I)
_HARD_DELETE_CALL = re.compile(
    r"\.delete\s*\(|DELETE\s+FROM|rmtree|unlink\s*\(|os\.remove\s*\(", re.I)


def _cand(kind: str, statement: str, sources: list, **kw) -> ContractCandidate:
    cid = f"c-{kind}-{abs(hash(statement)) % 10**8}"
    conf = compute_confidence(sources, kw.pop("evidence_count", 1))
    return ContractCandidate(id=cid, statement=statement, sources=list(sources),
                             confidence=conf, **kw)


# ── 来源 5：相似实现（⭐⭐）────────────────────────────────────

def from_similar(module_sources: dict[str, str]) -> list[ContractCandidate]:
    """同类模块中 3+ 个采用同一模式 -> 该模式是约定。

    目前实现的是**「软删除」模式**：若 ≥3 个模块里同时出现
    「delete/remove 类函数名」且调用 `.delete(`/`unlink(` 等硬删除，
    或 ≥3 个模块都用 `.delete(` 而**没有**硬删除 —— 后者才是「软删除」约定。

    ⚠️ 刻意只做这一种模式：泛化的「模式发现」需要真正的代码克隆检测，
    那几个数量级的复杂度换来的召回率提升并不可靠。规范 §4.12 的态度是
    「挖不到可接受」。
    """
    soft: list[str] = []
    hard: list[str] = []
    for path, src in (module_sources or {}).items():
        if not _SOFT_DELETE_HINT.search(src or ""):
            continue
        (hard if _HARD_DELETE_CALL.search(src or "") else soft).append(str(path))

    out: list[ContractCandidate] = []
    if len(soft) >= SIMILAR_MIN_OCCURRENCES and len(hard) < len(soft):
        out.append(_cand(
            "soft_delete",
            f"{len(soft)} 个模块的删除操作不使用硬删除（{', '.join(soft[:3])}）"
            " —— 可能是「必须软删除」的约定",
            ["similar"], evidence_count=len(soft),
            guard_type="symbol_scope",
            guard_scope={"kind": "symbol", "pattern": "*delete*",
                         "qualifier": "public"},
        ))
    return out
