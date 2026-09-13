# 会话级计划存储 —— 打通 plan_update 工具与 AgentState.plan
"""为什么需要单独一个 store：

工具 handler 的签名是 (arguments, workspace)，拿不到 AgentState，因此
plan_update 此前只能拼一段文本返回，改不到真正的计划 —— 而 agent 的循环
退出条件依赖 state.plan[*].status，导致"计划漂移"：面板显示的内容与
驱动循环的状态是两份互不相干的数据。

这里以 session_id 为键存放计划，由 AgentGraph 在每轮开始前注入、在
工具写完后同步回 state。store 只做数据搬运，不持有 agent 逻辑。
"""
from __future__ import annotations

import threading

_lock = threading.Lock()
_plans: dict[str, list[dict]] = {}

# 防止无 session_id 的调用（如直接裸调工具）无限增长
_MAX_SESSIONS = 256


def _key(session_id: str) -> str:
    return session_id or "__default__"


def set_plan(session_id: str, plan: list[dict]) -> None:
    """由 AgentGraph 在每轮 tool_select 之前把 state.plan 同步进来。"""
    if not session_id and not plan:
        return
    with _lock:
        _plans[_key(session_id)] = plan
        if len(_plans) > _MAX_SESSIONS:
            # 淘汰最早插入的会话（dict 保持插入顺序）
            for k in list(_plans.keys())[: len(_plans) - _MAX_SESSIONS]:
                _plans.pop(k, None)


def get_plan(session_id: str) -> list[dict]:
    with _lock:
        return list(_plans.get(_key(session_id), []))


def update_step(
    session_id: str,
    step_id: int,
    status: str,
    notes: str = "",
    new_steps: list[dict] | None = None,
) -> tuple[bool, str]:
    """更新某一步的状态，并可选在其后插入新步骤。

    返回 (是否命中, 说明)。step_id 是 PlanStep.step（1 基），与
    plan_update 工具的 schema 一致。
    """
    with _lock:
        plan = _plans.get(_key(session_id))
        if not plan:
            return False, "plan is empty"

        hit = None
        for step in plan:
            if int(step.get("step", -1)) == int(step_id):
                hit = step
                break
        if hit is None:
            return False, f"step {step_id} not found (plan has {len(plan)} steps)"

        hit["status"] = status
        if notes:
            hit["result"] = notes

        if new_steps:
            insert_at = plan.index(hit) + 1
            next_num = max(int(s.get("step", 0)) for s in plan) + 1
            added = []
            for ns in new_steps:
                added.append({
                    "step": next_num,
                    "description": str(ns.get("description", "")),
                    "tool": ns.get("tool"),
                    "status": "pending",
                    "estimated_turns": 1,
                    "result": None,
                })
                next_num += 1
            plan[insert_at:insert_at] = added

        _plans[_key(session_id)] = plan
        return True, "ok"


def clear(session_id: str) -> None:
    with _lock:
        _plans.pop(_key(session_id), None)
