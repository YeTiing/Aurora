"""A2 契约注入 guard —— 规范 §4.7 的三档策略。

从 `interceptor.py` 拆出（该文件触 300 行上限）。职责上也该分：
    interceptor.py    Guard 的**拦截/后检/回滚**主流程
    contracts_in.py   把 A2 挖出的契约**注入**到约束列表里

后者是「能力间的单向接线」（A2 -> Guard），不是 Guard 自身的逻辑。
单独成文件让这条依赖看得见 —— 否则读 interceptor 的人不会知道
它的约束列表里可能混着自动挖掘来的条目。
"""
from __future__ import annotations

import logging

logger = logging.getLogger("necessity.guard.contracts_in")


def inject_contracts(constraints: list, notes: list, task: dict) -> None:
    """把 task 里带的契约注入 `constraints`（原地修改）。

    **不自己挖掘** —— 契约挖掘是离线分析（要读测试/调用图/git 历史），
    在 `on_task_start`（主循环路径）上跑会拖慢每个任务。
    本函数只消费调用方**已经有**的契约集（`task["contracts"]`）。

    三档（规范 §4.7）：
        auto    置信度 ≥0.8 且 ≥2 条独立来源 -> 注入，未确认时动作为 warn
        review  0.5~0.8 -> 不注入，写 review_queue
        drop    <0.5 -> 不提

    任何异常都吞掉并记进 notes（fail-open，与所有钩子契约一致）。
    """
    raw = (task or {}).get("contracts")
    if not raw:
        return
    try:
        from backend.necessity.contract.compile import to_guard_constraints
        from backend.necessity.contract.review import ReviewQueue
        from backend.necessity.contract.schema import ContractCandidate

        cands = [c if isinstance(c, ContractCandidate) else ContractCandidate(**c)
                 for c in raw]

        queue = ReviewQueue()
        auto = [c for c in cands
                if getattr(c, "injection", "") == "auto" and not queue.is_off(c.id)]
        review = [c for c in cands if getattr(c, "injection", "") == "review"]

        cons, problems = to_guard_constraints(auto)
        # 逐条按用户的确认状态决定动作：**未确认 -> warn**。
        # 规范 §4.7：「自动注入的契约首次被违反 -> 不直接 block
        # （用户还没确认过它）」。默认 block 会拦住用户不知情的改动。
        for sc in cons:
            sc.on_violation = queue.effective_action(sc.id, default="warn")
        constraints.extend(cons)

        if cons:
            notes.append(
                f"注入了 {len(cons)} 条自动挖掘的隐式契约"
                "（未确认的按 warn 处理，确认后升级为 block）")
        if review:
            queue.write_queue(cands)
            notes.append(f"{len(review)} 条契约置信度不足，已写入 review_queue（未注入）")
        for pr in problems[:3]:
            # 编译失败要可见 —— 静默跳过会让「契约没生效」无从解释
            notes.append(f"契约未注入：{pr}")
    except Exception as e:
        notes.append(f"契约注入失败（不影响任务）：{type(e).__name__}: {e}")


__all__ = ["inject_contracts"]
