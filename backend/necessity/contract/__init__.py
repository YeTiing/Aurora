"""A2 隐式行为契约挖掘 —— 规范 §4。

把「我记得的约定」变成「机器可校验的约束」。

## 核心洞察（规范 §4.3）

**不新建执行机制。** 挖出的契约编译成 `guard` 已有的 8 种类型之一，
于是拦截 / 回滚 / 账本全部白送。所以本包只做三件事：
**提取 → 分级 → 编译**，不碰执行。

## 全套件最重要的一条纪律（规范 §4.12）

    「不追求完备 —— **挖不到可接受，挖错不可接受**」

一条错误的契约会拦住**正确的改动**，比没有契约更糟。
所以每个提取器都偏保守，置信度不够的进人工队列而不是自动注入。

## 结构

    schema.py   候选数据结构 + 置信度折算（对齐 §4.7 的门槛）
    extract.py  7 个来源的提取器（tests/callgraph/exceptions/types/similar/…）
    compile.py  契约 → guard 的 8 类型（「执行层白送」的落地点）
    judge.py    git 历史裁判法（§4.6，ground truth 的客观来源）
    review.py   三档注入 + 首次触达即审批（§4.7）

⚠️ 子模块显式 import：本仓库 `__init__.py` 的约定是列出子模块，
不列会让 `from backend.necessity.contract import extract` 失败（踩过两次）。
"""
from . import compile as _compile_mod, extract, judge, review, schema
from .compile import CompileError, DEFAULT_ON_VIOLATION, to_guard_constraints
from .extract import extract_all, from_callgraph, from_exceptions, from_similar, from_tests, from_types
from .judge import JudgeResult, ViolationEvent, evaluate, find_violation_events
from .review import (
    CONFIRMED,
    DEFAULT_QUEUE,
    IGNORED_ALWAYS,
    IGNORED_ONCE,
    ReviewItem,
    ReviewQueue,
)
from .schema import (
    AUTO_INJECT_AT,
    CONTRACT_KINDS,
    MIN_SOURCES_FOR_AUTO,
    REVIEW_AT,
    SOURCE_STRENGTH,
    ContractCandidate,
    ContractSet,
    compute_confidence,
)

__all__ = [
    # 子模块
    "compile", "extract", "judge", "review", "schema",
    # §4.5 提取
    "extract_all", "from_callgraph", "from_exceptions", "from_similar",
    "from_tests", "from_types",
    # §4.3 编译
    "CompileError", "DEFAULT_ON_VIOLATION", "to_guard_constraints",
    # §4.6 裁判
    "JudgeResult", "ViolationEvent", "evaluate", "find_violation_events",
    # §4.7 审批
    "CONFIRMED", "DEFAULT_QUEUE", "IGNORED_ALWAYS", "IGNORED_ONCE",
    "ReviewItem", "ReviewQueue",
    # §4.5 数据
    "AUTO_INJECT_AT", "CONTRACT_KINDS", "MIN_SOURCES_FOR_AUTO", "REVIEW_AT",
    "SOURCE_STRENGTH", "ContractCandidate", "ContractSet", "compute_confidence",
]

# `compile` 是 Python 内建名，直接 `from . import compile` 会遮蔽它。
# 这里用别名导入后再挂回正确名字，既能 `from backend.necessity.contract import compile`
# 又不会在包命名空间里把内建 compile 覆盖掉。
compile = _compile_mod
