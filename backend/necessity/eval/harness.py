"""跑分装备层 —— 对照组接线（EVAL.md §3.1 的两级对照）。

拆成独立文件而不是塞进 runner.py 的理由：
    runner 负责「按矩阵调度、落盘、续跑」，本文件回答「一个 arm 到底是什么」。
    两者寿命不同 —— 实验矩阵稳定，Agent 接入（agents.py）会随宿主演进。

两个关键约定：

1. **arm → 能力必须走真实的 load_capabilities。**
   不许 runner 自己模拟能力行为，否则测的是模拟器不是系统。
   B′ 用 min_lines_for_index=10**9 关闭符号索引（只外置全文）——
   这是「无符号索引」的**唯一**现有可配置差异点。

2. **A′ 的 run 必须真的没有任何能力挂载。**
   它是最关键对照（§3.1）：「什么都不做，只在 prompt 里叮嘱」。
   若 A′ 偷偷挂了能力，整个项目的核心论证就废了。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# A′ 的提示词叮嘱（§3.1：代表现状做法）
ADMONISH_PROMPT = (
    "\n\n【重要】请不要重复读取已经读过的文件；"
    "不要修改任务范围之外的文件；"
    "只做完成本任务所必需的改动，不要顺手改动无关代码。"
)

# D 组只跑改动量大的任务（§3.3）。阈值取 §1.4 的「改动 ≥80 行」。
# 注意：真正的筛选判据在 runner.is_large_diff（优先看 meta["large_diff"]）——
# 生成器当前把 min_lines_changed 统一写成 1 占位，光看该字段会筛空 D 组。
LARGE_DIFF_MIN_LINES = 80


# ── 对照组配置 ───────────────────────────────────────────────────

def arm_capability_config(arm: str, *, workspace: str = ".",
                          session_id: str = "") -> dict:
    """返回传给 `load_capabilities` 的配置。

    "enabled": False 是**总开关默认关**，再逐能力显式打开 ——
    这样配置读起来就是 arm 的定义，不存在「继承自默认值」的隐藏差异。
    （core/capability.py 的 DEFAULT_ENABLED 里 reduce/attribution 默认关、
      context/guard 默认开；若不显式关掉总开关，裸 Agent 会莫名挂上两个能力。）
    """
    base = str(workspace or ".")
    ctx = {"enabled": True, "workspace": base, "session_id": session_id}
    # Guard 需要 workspace 做路径归一化；默认 warn（GUARD.md 的默认动作）
    guard = {"enabled": True, "default_action": "warn", "workspace": base}
    return {
        "A":         {"enabled": False},
        "B":         {"enabled": False, "context_paging": dict(ctx)},
        "C":         {"enabled": False, "guard": dict(guard)},
        "D":         {"enabled": False, "reduce": {"enabled": True}},
        "E":         {"enabled": False,
                      "context_paging": dict(ctx),
                      "guard": dict(guard),
                      "reduce": {"enabled": True},
                      "attribution": {"enabled": True}},
        # A′：没有任何能力，只有 prompt 叮嘱
        "A_prime":   {"enabled": False},
        # B′：文件状态表在，但符号索引被关闭（min_lines 设成不可能达到的值）
        "B_prime":   {"enabled": False,
                      "context_paging": {**ctx, "min_lines_for_index": 10 ** 9}},
        # C′：文本层面检查 —— 不挂 Guard，由 runner 用正则事后核对路径
        "C_prime":   {"enabled": False},
    }.get(arm, {"enabled": False})


def load_arm_hooks(arm: str, *, workspace: str = ".", session_id: str = ""):
    """装配一个 arm 的钩子。返回 (hooks, caps)。

    返回 caps 是刻意的：报告与测试需要断言「这个 arm 到底挂了什么」，
    不能只看钩子有没有反应（CompositeHooks 对空集合是静默的）。
    """
    from backend.necessity.capability import CompositeHooks, load_capabilities

    caps = load_capabilities(arm_capability_config(
        arm, workspace=workspace, session_id=session_id))
    return CompositeHooks(caps), caps


def prompt_for_arm(arm: str, task_text: str) -> str:
    """A′ 在任务描述后追加叮嘱；其余 arm 原样（§3.1）。"""
    return task_text + ADMONISH_PROMPT if arm == "A_prime" else task_text


# ── C′：文本层面约束检查（无结构信息）────────────────────────────
# 对照 C（结构化：约束编译 + scope/图检查）。C′ 只从约束句里抠路径，
# 再看 diff 里是否出现该目录之外的文件 —— 不做路径归一化、不查调用图、
# 不区分 writer，这正是「文本匹配」的典型弱点。

_PATH_TOKEN = re.compile(r"(?:[\w.\-]+/)+[\w.\-]*")


def text_scope_patterns(constraints: list[str]) -> list[str]:
    """从自然语言约束里抠出路径前缀（正则，无结构解析）。"""
    out: list[str] = []
    for c in constraints or []:
        out.extend(_PATH_TOKEN.findall(str(c)))
    return out


def text_level_violations(constraints: list[str], changed_paths: list[str]) -> list[str]:
    """返回越界文件列表（C′ 的判据）。"""
    prefixes = text_scope_patterns(constraints)
    if not prefixes:
        return []
    norm = [p.replace("\\", "/").lstrip("./") for p in changed_paths]
    return [p for p in norm
            if not any(p.startswith(pref) for pref in prefixes)]
