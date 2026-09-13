"""失败分类体系 —— ATTRIBUTION.md §2。

为什么单独一个文件：
    分类体系是**设计核心**，不是实现细节。它被信号层、规则层、LLM 提示词、
    元评测（混淆矩阵）多处引用。集中一处，改分类只改这里。

三条设计原则（§2.1）：
    1. MECE —— 互斥且尽可能完备
    2. 可操作 —— 每一类对应一个明确的改进方向（**这是分类存在的意义**）
    3. 可扩展 —— 允许后续细分

关键区分（§2.3）：
    `environment` 与 `task_issue` **不是 Agent 的锅**。归因出这两类，
    要修的是环境或任务定义，而不是换模型。不先排除它们，就会把
    「环境缺依赖」误判成「模型能力不足」，白费功夫。
"""
from __future__ import annotations

# ── 十类（§2.2）────────────────────────────────────────────────────
PLANNING = "planning"
CONTEXT_MISSING = "context_missing"
CONTEXT_STALE = "context_stale"
TOOL_USE = "tool_use"
EDIT_ERROR = "edit_error"
CONSTRAINT_DRIFT = "constraint_drift"
THRASHING = "thrashing"
ENVIRONMENT = "environment"
CAPABILITY = "capability"
TASK_ISSUE = "task_issue"

# `unknown` 是合法输出（§5.3）：强行分类会污染统计。
UNKNOWN = "unknown"

CATEGORIES: tuple[str, ...] = (
    PLANNING, CONTEXT_MISSING, CONTEXT_STALE, TOOL_USE, EDIT_ERROR,
    CONSTRAINT_DRIFT, THRASHING, ENVIRONMENT, CAPABILITY, TASK_ISSUE,
)

CATEGORY_SET = frozenset(CATEGORIES)

# 不是 Agent 的锅的两类 —— 本设计最关键的区分（§2.3）。
NON_AGENT_FAULT = frozenset({ENVIRONMENT, TASK_ISSUE})

# 每类对应的改进方向（§2.2 表格最后一列）。分类存在的意义就是这个映射。
IMPROVEMENTS: dict[str, str] = {
    PLANNING: "改规划提示词 / 加 plan 校验",
    CONTEXT_MISSING: "改检索 / 启用 Context Paging，避免压缩后丢失已读内容",
    CONTEXT_STALE: "加失效校验（用 content_hash 判有效性）",
    TOOL_USE: "改工具描述与签名 / 让 Agent 处理工具失败",
    EDIT_ERROR: "加强编辑前检查",
    CONSTRAINT_DRIFT: "启用 Constraint Guard",
    THRASHING: "用 Diff Reducer 做致败定位",
    ENVIRONMENT: "修环境（非 Agent 问题）",
    CAPABILITY: "换模型 / 拆小任务",
    TASK_ISSUE: "改任务定义（非 Agent 问题）",
    UNKNOWN: "补埋点 / 补信号覆盖",
}

# 类的含义 —— 给人工标注者与 LLM 层共用，避免两边理解不一致。
CATEGORY_DESCRIPTIONS: dict[str, str] = {
    PLANNING: "任务分解错、顺序错、方向偏",
    CONTEXT_MISSING: "关键信息没进上下文（没读 / 被压缩掉）",
    CONTEXT_STALE: "用了过期信息做决策",
    TOOL_USE: "选错工具 / 参数错 / 未处理工具失败",
    EDIT_ERROR: "编辑本身写错（语法、逻辑、改错位置）",
    CONSTRAINT_DRIFT: "越界改动",
    THRASHING: "反复改-回滚，不收敛",
    ENVIRONMENT: "依赖缺失 / 网络 / 权限 / flaky 测试",
    CAPABILITY: "模型推理或知识不足",
    TASK_ISSUE: "任务本身不可完成 / 需求矛盾",
    UNKNOWN: "有轨迹但无法给出有证据的归因（合法输出）",
}

# 信号优先级（§4.3）—— 数值越小优先级越高。
# 理由：先排除「不是 Agent 的锅」，再归因到 Agent。
CATEGORY_PRIORITY: dict[str, int] = {
    ENVIRONMENT: 10,
    TASK_ISSUE: 10,
    CONSTRAINT_DRIFT: 20,
    THRASHING: 30,
    TOOL_USE: 40,
    CONTEXT_MISSING: 50,
    CONTEXT_STALE: 50,
    PLANNING: 60,
    EDIT_ERROR: 60,
    CAPABILITY: 60,
    UNKNOWN: 999,
}


def is_known(category: str) -> bool:
    """是否是分类体系里的十类之一（`unknown` 不算）。"""
    return category in CATEGORY_SET


def is_agent_fault(category: str) -> bool:
    """这一类是不是 Agent 的锅（§2.3）。

    `environment` / `task_issue` → False（该修环境或任务定义）。
    `unknown` → False（无法判断，不构成对 Agent 的指控）。
    """
    if category == UNKNOWN or not is_known(category):
        return False
    return category not in NON_AGENT_FAULT


def improvement_for(category: str) -> str:
    """该类对应的改进方向 —— 无映射时退回提示补埋点。"""
    return IMPROVEMENTS.get(category, IMPROVEMENTS[UNKNOWN])


def priority_for(category: str) -> int:
    """信号优先级，用于多信号命中时决定 primary（§4.3）。"""
    return CATEGORY_PRIORITY.get(category, CATEGORY_PRIORITY[UNKNOWN])


def describe(category: str) -> str:
    return CATEGORY_DESCRIPTIONS.get(category, category)


__all__ = [
    "CATEGORIES", "CATEGORY_SET", "CATEGORY_DESCRIPTIONS", "CATEGORY_PRIORITY",
    "IMPROVEMENTS", "NON_AGENT_FAULT", "UNKNOWN",
    "PLANNING", "CONTEXT_MISSING", "CONTEXT_STALE", "TOOL_USE", "EDIT_ERROR",
    "CONSTRAINT_DRIFT", "THRASHING", "ENVIRONMENT", "CAPABILITY", "TASK_ISSUE",
    "is_known", "is_agent_fault", "improvement_for", "priority_for", "describe",
]
