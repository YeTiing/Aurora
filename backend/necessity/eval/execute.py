"""单次运行的生命周期 —— 隔离 → Agent → 真跑验收测试 → 取 diff。

从 `runner.py` 拆出（该文件触碰 300 行上限）。职责上是独立的一层：
    runner.py   按矩阵调度、落盘、续跑（**什么时候跑**）
    execute.py  一次运行里发生什么（**跑了什么**）

它实现 EVAL.md §3.2 的四步：
    1. 复制快照到临时目录（`snapshot.create`）—— 绝不就地改仓库里的快照
    2. 让 Agent 在该目录干活
    3. **跑 tests/ 判定**（`verify.verify_workdir`）—— 不是看 LLM 回没回话
    4. 取 diff、删临时目录（失败时按策略保留现场）

第 1 步与第 3 步此前都是缺失的，且都不报错：
  - 缺 1：Agent 就地改快照，第 1 次运行就永久污染基线，后续 167 次全在脏基线上
  - 缺 3：`status = "pass" if LLM 回了文本`，主指标（破坏调用方次数）完全不可测
"""
from __future__ import annotations

import logging
import time
from typing import Any

from backend.necessity.eval.agents import AgentRunResult, AgentUnavailable
from backend.necessity.eval.harness import load_arm_hooks, prompt_for_arm
from backend.necessity.eval.plan import spec_of

logger = logging.getLogger("necessity.eval.execute")

# 判定不可被「Agent 自己怎么报」翻案的终局状态。
# timeout = §7.3 早停（超轮次上限），error = 运行期异常 ——
# 这两次运行都没给 Agent 公平机会，若因「测试恰好通过」记成 pass 就是虚增通过率。
_TERMINAL = ("timeout", "error")


def run_once(task: Any, arm: str, run_index: int, agent,
             *, turn_limit: int, verify_timeout: int,
             keep_failures: bool = False) -> tuple[AgentRunResult, dict]:
    """跑一次，返回 (结果, verify 详情)。调用方负责转成 Attempt。

    **快照准备不在 try 内**：它失败是配置/数据问题，不是「这次运行失败」。
    此前它被兜成一条 status=error 的记录，于是空 repo、复制失败、
    gitlink 导致没源码……全都看起来像「跑过了、只是这次没过」，
    而实际是整批数字都不可信。那正是反向前置检查要拦的问题，不该被降级。
    """
    from backend.necessity.eval import snapshot as _snap
    from backend.necessity.eval import verify as _verify

    task_id = spec_of(task).task_id
    session_id = f"{task_id}-{arm}-{run_index}"
    verify_info: dict = {}

    ws = _snap.create(task)
    workdir = ws.workdir
    result = AgentRunResult(status="error")

    try:
        hooks, _caps = load_arm_hooks(
            arm, workspace=str(workdir), session_id=session_id)
        text = task.task_text() if hasattr(task, "task_text") else ""
        result = agent.run(
            task_text=prompt_for_arm(arm, text),
            repo=workdir, arm=arm, run_index=run_index,
            session_id=session_id, hooks=hooks, turn_limit=turn_limit,
        )

        # 真实判定：跑验收测试（EVAL.md §Phase 3「不要用 LLM 当裁判」）
        verify_res = _verify.verify_workdir(workdir, timeout=verify_timeout)
        verify_info = verify_res.to_dict()
        if result.status not in _TERMINAL:
            result.status = verify_res.status
            if verify_res.status != "pass" and not result.error:
                result.error = verify_res.diagnosis
        result.meta = {
            **(result.meta or {}), "verify": verify_info,
            "verdict_source": ("agent_terminal" if result.status in _TERMINAL
                               else "acceptance_tests"),
        }

        # diff 必须从**工作目录**取，而不是快照（快照里没有改动）
        if not result.diff_text:
            result.diff_text = _snap.diff_of(workdir)
    except AgentUnavailable:
        ws.cleanup()
        raise                     # 环境问题必须冒泡，不能被记成「任务失败」
    except Exception as e:
        result = AgentRunResult(status="error",
                                error=f"{type(e).__name__}: {e}")
    finally:
        if result.status == "error" and keep_failures:
            # 没有现场就无法归因（§7.4）
            path = ws.keep()
            result.meta = {**(result.meta or {}), "workdir_kept": path}
        else:
            ws.cleanup()

    return result, verify_info


def run_and_measure(task: Any, arm: str, run_index: int, agent, *, turn_limit: int,
                    verify_timeout: int, keep_failures: bool = False,
                    runtime_feed=None):
    """`run_once` + 换算成 Attempt —— runner 只需要这一个入口。

    `runtime_feed` 是可选注入（规范 §1.4 的「观测」段）：给了就把本次
    `GateMetrics` 喂进运行时闭环，窗口满时判定退化。默认 `None` ——
    单次运行与闭环解耦，测试与一次性跑分不必背上跨运行状态。
    """
    from backend.necessity.eval.measure import measure

    started = time.time()
    result, verify_info = run_once(
        task, arm, run_index, agent, turn_limit=turn_limit,
        verify_timeout=verify_timeout, keep_failures=keep_failures)
    attempt = measure(task, arm, run_index, result, started, time.time())
    if verify_info:
        attempt.meta = {**(attempt.meta or {}), "verify": verify_info}

    if runtime_feed is not None:
        # 闭环判定失败**不能**影响本次运行的结果 —— 它是观测，不是判定者
        try:
            verdicts = runtime_feed.observe(attempt)
            if verdicts:
                attempt.meta = {**(attempt.meta or {}),
                                "runtime_verdicts": verdicts}
        except Exception as e:
            logger.warning("运行时闭环观测失败（不影响本次结果）: %s", e)
    return attempt
