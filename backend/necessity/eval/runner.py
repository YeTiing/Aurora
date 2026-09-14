"""多 arm 跑分调度 —— EVAL.md §3（对照矩阵）/ §7（流程与算力预算）。

本文件只做三件事：调度、落盘、续跑。其余已拆到：
    harness.py       arm → 能力接线（含 A′ 的 prompt 叮嘱、C′ 的文本检查）
    agents.py        可注入 Agent 契约 + 离线 ScriptedAgent
    aurora_agent.py  真实 Aurora 驱动（需要 LLM key）
    plan.py          矩阵展开、D 组裁剪、**算力预检**
    measure.py       单次产出 → Attempt（gate 指标）
    stats.py / report.py

三条不可妥协的纪律：

1. **算力预检**（§7.3）：任何人启动前先看到「这次要跑多少次、多久」。
   不声不响跑 690 次是事故，不是实验。
2. **增量落盘 + 续跑**（§7.4）：每跑完一次立刻 append JSONL；
   重跑时已完成的 (task, arm, run) 直接跳过。690 次的长任务靠它才活得下来。
3. **轨迹全量**：events 少一条，归因就不成立，必须重跑 —— 所以 events
   原样写入，不在采集侧做任何裁剪。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.necessity.eval.agents import (  # noqa: E402
    DEFAULT_TURN_LIMIT,
    AgentRunner,
    AgentUnavailable,
)
from backend.necessity.eval.harness import load_arm_hooks, prompt_for_arm  # noqa: E402
from backend.necessity.eval.measure import measure  # noqa: E402
from backend.necessity.eval.plan import (  # noqa: E402
    ARMS_TASK_FILTER,
    Plan,
    completed_keys,
    is_large_diff,
    plan_matrix,
    repo_of,
    spec_of,
    tasks_for_arm,
)
from backend.necessity.eval.records import ARMS, Attempt, read_attempts, write_attempts  # noqa: E402

__all__ = [
    "ARMS_TASK_FILTER", "EvalRunner", "Plan", "completed_keys", "gate0_plan",
    "is_large_diff", "main", "measure", "plan_matrix", "run_gate0_mode",
    "spec_of", "tasks_for_arm",
]


class EvalRunner:
    """按矩阵跑，按 JSONL 续跑。"""

    def __init__(self, agent: AgentRunner | None, out_path: str | Path,
                 *, tasks: Sequence[Any] | None = None,
                 turn_limit: int = DEFAULT_TURN_LIMIT,
                 dry_run: bool = False, retry_errors: bool = False,
                 check_baseline: bool = True, baseline_workers: int = 4,
                 verify_timeout: int = 300, keep_failures: bool = False,
                 log=print):
        self.agent = agent
        self.out_path = Path(out_path)
        self.tasks = list(tasks or [])
        self.turn_limit = turn_limit
        self.dry_run = dry_run
        self.retry_errors = retry_errors
        # 验收测试的超时（秒）。长任务（跨文件重构）需要更久，可调。
        self.verify_timeout = verify_timeout
        # 失败时保留临时工作目录 —— 没有现场就无法归因（§7.4）。
        # 默认关：168 次长跑若次次保留现场会堆满磁盘。
        self.keep_failures = keep_failures
        # 反向前置检查（EVAL.md §1.2 第 5 步）默认开。只有「已在一轮里
        # 验过、只想续跑」的场景才该关掉；关掉时 caller 必须自己承担。
        self.check_baseline = check_baseline
        self.baseline_workers = baseline_workers
        self.log = log

    def load_tasks(self, tasks_dir: str | Path) -> list[Any]:
        """加载任务集并**在开跑前**验掉「基线必须失败」这条不变量。

        为什么放在这里而不是 run() 里：这是**配置**问题，不是运行问题。
        放 run() 里会与「agent 能不能用」的检查混在一起，而两者必须分开 ——
        任务集坏了在无 LLM key 的机器上也该被查出来。
        """
        from backend.necessity.eval.tasks.loader import load_all

        self.tasks = load_all(tasks_dir)
        if self.check_baseline and self.tasks:
            from backend.necessity.eval.tasks.baseline import assert_discriminates

            self.log(f"反向前置检查（§1.2 第 5 步）：{len(self.tasks)} 个任务 ...")
            assert_discriminates(self.tasks, workers=self.baseline_workers,
                                 log=self.log)
            self.log("  ✓ 全部任务在基线状态下测试失败（有区分度）")
        return self.tasks

    def _is_done(self, key: tuple[str, str, int], done: set,
                 status_of: dict) -> bool:
        """续跑判据。retry_errors 时只有 error 的记录才重跑。"""
        if key not in done:
            return False
        if self.retry_errors:
            return status_of.get(key) != "error"
        return True

    def run(self, plan: Plan) -> dict:
        """执行计划。返回 {ran, skipped, failed, elapsed}。"""
        if self.dry_run:
            return {"ran": 0, "skipped": plan.total, "failed": 0,
                    "elapsed": 0.0, "dry_run": True}

        if self.agent is None:
            raise AgentUnavailable("未提供 Agent")
        ok, why = self.agent.available()
        if not ok:
            # 缺 key / 缺宿主：整批中止，给出可执行指引，绝不产出假数字
            raise AgentUnavailable(why)

        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        existing = read_attempts(self.out_path)
        done = completed_keys(existing)
        status_of = {(a.task_id, a.arm, a.run_index): a.status for a in existing}

        ran = skipped = failed = 0
        t0 = time.time()
        for task, arm, run_index in plan.entries:
            key = (spec_of(task).task_id, arm, run_index)
            if self._is_done(key, done, status_of):
                skipped += 1
                continue
            attempt = self.run_one(task, arm, run_index)
            write_attempts(self.out_path, [attempt])   # 增量落盘（§7.4）
            done.add(key)
            status_of[key] = attempt.status
            ran += 1
            if attempt.status != "pass":
                failed += 1
            self.log(f"[{ran + skipped}/{plan.total}] {attempt.attempt_id} "
                     f"→ {attempt.status} turns={attempt.turns} "
                     f"tokens={attempt.tokens} waste={attempt.gates.waste_ratio:.2f}")

        return {"ran": ran, "skipped": skipped, "failed": failed,
                "elapsed": time.time() - t0, "dry_run": False}

    def run_one(self, task: Any, arm: str, run_index: int) -> Attempt:
        """跑一次；任何**运行期**异常都变成一条 status=error 的记录，不中断整批。

        实际生命周期在 `eval/execute.py`（隔离 → Agent → 跑验收测试 → diff），
        本方法只负责把它接到 runner 的配置上。拆出去的理由见该模块头。

        ⚠️ 快照准备失败**不**在这里被吞掉 —— 那是配置/数据问题（空快照、
        gitlink 导致没源码），必须中止整批而不是伪装成「这次运行没过」。
        """
        from backend.necessity.eval.execute import run_and_measure

        return run_and_measure(
            task, arm, run_index, self.agent,
            turn_limit=self.turn_limit, verify_timeout=self.verify_timeout,
            keep_failures=self.keep_failures,
        )


# ── Gate 0 模式 ──────────────────────────────────────────────────

def gate0_plan(tasks: Sequence[Any]) -> Plan:
    """§7.3 分层跑：Gate 0 只跑 A 组、每任务 1 次（30 runs）。"""
    return plan_matrix(tasks, arms=["A"], runs=1)


def run_gate0_mode(tasks: Sequence[Any], agent: AgentRunner, out_path: str | Path,
                   *, turn_limit: int = DEFAULT_TURN_LIMIT, dry_run: bool = False,
                   log=print) -> dict:
    """跑基线并报告重复读取率（§4 Gate 0 的判据输入）。"""
    from backend.necessity.eval.gate0 import Gate0Result

    plan = gate0_plan(tasks)
    log(plan.describe())
    runner = EvalRunner(agent, out_path, tasks=list(tasks), turn_limit=turn_limit,
                        dry_run=dry_run, log=log)
    summary = runner.run(plan)
    if dry_run:
        return summary

    # 复算：Gate 0 的核心是「ΣR_waste / ΣR」而非逐次平均
    attempts = [a for a in read_attempts(out_path) if a.arm == "A" and a.run_index == 0]
    agg = Gate0Result(
        total_reads=sum(a.gates.reads_total for a in attempts),
        waste_reads=sum(a.gates.reads_waste for a in attempts),
    )
    verdict, action = agg.verdict()
    log("")
    log(f"Gate 0 汇总：R={agg.total_reads} R_waste={agg.waste_reads} "
        f"重复读取率={agg.waste_ratio:.2%}（{len(attempts)} 个任务）")
    log(f"判定 {verdict} —— {action}")
    summary.update({"gate0": agg.to_dict(), "verdict": verdict})
    return summary


# ── CLI ──────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m eval.runner",
                                description="Necessity 跑分器（EVAL.md §3/§7）")
    p.add_argument("--tasks", default=str(ROOT / "eval" / "tasks"),
                   help="任务集目录（默认 eval/tasks）")
    p.add_argument("--out", default=str(ROOT / "eval" / "runs" / "attempts.jsonl"))
    p.add_argument("--agent", choices=("aurora", "scripted"), default="aurora")
    p.add_argument("--arms", default=",".join(ARMS))
    p.add_argument("--runs", type=int, default=3, help="每组每任务重复次数（§6.2：3）")
    p.add_argument("--gate0", action="store_true", help="只跑 Gate 0（A 组 × 1 次）")
    p.add_argument("--turn-limit", type=int, default=DEFAULT_TURN_LIMIT)
    p.add_argument("--retry-errors", action="store_true", help="重跑上次 status=error 的")
    p.add_argument("--skip-baseline-check", action="store_true",
                   help="跳过「基线必须失败」的反向前置检查（默认**不跳过**）")
    p.add_argument("--dry-run", action="store_true", help="只打印预检，不执行")
    p.add_argument("--aurora-root", default=None)
    p.add_argument("--port", type=int, default=9876)
    return p


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认 GBK，中文/符号会直接抛 UnicodeEncodeError 中断跑分
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    args = build_parser().parse_args(argv)

    if args.agent == "scripted":
        print("[警告] scripted 模式产出的是假数据，只能用于验证管线，不可写进报告。")
        from backend.necessity.eval.agents import ScriptedAgent

        agent: AgentRunner = ScriptedAgent()
    else:
        from backend.necessity.eval.aurora_agent import AuroraAgent

        agent = AuroraAgent(args.aurora_root, port=args.port)

    runner = EvalRunner(agent, args.out, turn_limit=args.turn_limit,
                        dry_run=args.dry_run, retry_errors=args.retry_errors,
                        check_baseline=not args.skip_baseline_check, log=print)
    try:
        tasks = runner.load_tasks(args.tasks)
    except Exception as e:
        # 任务集坏了（含反向前置检查不通过）—— 退出码 2 与「环境缺 key」
        # （退出码 3）区分开，便于 CI / 脚本判断该修什么。
        print(f"\n任务集不可用，已中止：\n{type(e).__name__}: {e}", file=sys.stderr)
        return 2

    try:
        if args.gate0:
            run_gate0_mode(tasks, agent, args.out, turn_limit=args.turn_limit,
                           dry_run=args.dry_run, log=print)
            return 0
        arms = [a.strip() for a in args.arms.split(",") if a.strip()]
        plan = plan_matrix(tasks, arms=arms, runs=args.runs)
        print(plan.describe())
        print()
        summary = runner.run(plan)
    except AgentUnavailable as e:
        print(f"\n无法运行真实 Agent：\n{e}", file=sys.stderr)
        return 3

    if summary.get("dry_run"):
        print("\n（--dry-run：以上仅为预检，未执行任何运行，未写入任何记录。）")
        return 0
    print(f"\n完成：运行 {summary['ran']}，跳过（已存在）{summary['skipped']}，"
          f"非 pass {summary['failed']}，耗时 {summary['elapsed']:.0f}s")
    print(f"记录：{args.out}")
    print(f"生成报告：python -m eval.report --in {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
