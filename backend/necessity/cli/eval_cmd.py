"""`necessity eval` —— 评测入口（Gate 0 与跑分汇总）。

两个子命令刻意分工：
    gate0    薄包装 eval/gate0.py::main，**保留其退出码**。Gate 0 的阈值
             决定要不要继续做 Context Paging，退出码是承重的：
                 0 = PASS（重复读取率 >15%）
                 1 = 有数据但未过阈值（MARGINAL/FAIL）
                 2 = 没采集到任何轨迹 —— 埋点未生效，不是「没问题」
             绝不能把 2 与 1 混为一谈，否则会把「采集没做」误读成「无需优化」。
    summary  读 runner 产出的 attempts.jsonl，打 per-arm 表。
             统计归 eval/report.py（并发 agent 负责）；本模块只做展示，
             不复制任何指标计算。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 各 arm 的展示名兜底（records.ARMS 才是真源）
_FALLBACK_ARM_NAMES = {
    "A": "裸 Agent", "B": "A + Context Paging", "C": "A + Constraint Guard",
    "D": "A + Diff Reducer", "E": "全部启用",
    "A_prime": "A + 提示词叮嘱", "B_prime": "A + 状态表", "C_prime": "A + 文本检查",
}


def cmd_eval_gate0(args) -> int:
    """薄包装 eval/gate0.py::main —— **原样保留它的退出码**。

    退出码（gate0 自己的契约，不能改写）：
        0 = PASS（重复读取率 >15%，继续 Context Paging）
        1 = 有数据但 MARGINAL/FAIL
        2 = 没采集到任何轨迹 → 埋点未生效（**不是**「重复读取率为 0」）
    把 2 和 1 区分开是关键：否则会把「采集没做」误判成「不存在问题」。

    --db 的处理：gate0.main 内部自己 `TraceStore()`（无参，不读磁盘），
    所以 --db 无法经由参数或单例传到它。这里在**调用期间**把 gate0 模块
    命名空间里的 TraceStore 换成绑定了本层 TraceDB 适配器的版本，调用
    结束立即还原 —— 不改 eval/**，也不留全局副作用。
    --db 缺省时是纯透传，行为与 `python eval/gate0.py` 完全一致。
    """
    try:
        from eval import gate0
    except ImportError as e:  # 评测包缺失属于「缺依赖」，不是存储错误
        print(f"无法导入 eval.gate0: {e}", file=sys.stderr)
        return 3

    # gate0.main 接受位置参数 session_id（argv[0]）
    argv = [args.session] if args.session else []
    if not args.db:
        return gate0.main(argv)

    from cli.attribution_cmd import TraceDB

    original = gate0.TraceStore
    db = TraceDB(args.db)  # 预建，避免表未初始化时 events() 静默返回空
    gate0.TraceStore = lambda *a, **k: original(db=db, **k)  # type: ignore[assignment]
    try:
        return gate0.main(argv)
    finally:
        gate0.TraceStore = original  # type: ignore[assignment]


def cmd_eval_summary(args) -> int:
    from backend.necessity.eval.records import read_attempts

    path = Path(args.attempts_file)
    if not path.is_file():
        print(f"文件不存在: {path}", file=sys.stderr)
        return 4
    attempts = read_attempts(path)
    if not attempts:
        print(f"没有可用的 attempt 记录（文件为空或全部坏行）: {path}", file=sys.stderr)
        return 1

    # 统计口径的唯一真源是 eval/report.py —— 本命令**只做展示**。
    # 若它尚未落地（并发开发中），降级为原始计数表并显式声明不是最终指标。
    try:
        from eval import report as report_mod
    except ImportError:
        return _fallback_summary(attempts, path)

    if args.json:
        print(json.dumps(report_mod.build_report(attempts), ensure_ascii=False,
                         indent=2))
        return 0

    sums = report_mod.summaries(attempts)
    print(f"记录数 {len(attempts)}    文件 {path}")
    print()
    print(report_mod.render_table(sums))
    print()
    print(report_mod.render_comparisons(report_mod.all_comparisons(attempts)))
    print()
    print(report_mod.DISCLAIMER)
    return 0


def _fallback_summary(attempts, path) -> int:
    """report.py 缺席时的最小表（仅原始计数，不做任何口径判断）。

    保留这条退路是为了「并发 agent 未落地时命令仍可用」；一旦 report.py
    存在（现在已存在），正常路径完全走它，这里不会被执行。
    """
    rows: dict[str, dict] = {}
    for a in attempts:
        arm = getattr(a, "arm", "") or "?"
        r = rows.setdefault(arm, {"n": 0, "pass": 0, "fail": 0, "error": 0,
                                  "timeout": 0, "skipped": 0})
        r["n"] += 1
        st = str(getattr(a, "status", "skipped"))
        r[st if st in r else "error"] += 1
    print(f"attempts: {len(attempts)}")
    print(f"{'arm':<8}{'n':>4}{'pass':>6}{'fail':>6}{'error':>6}  说明")
    from backend.necessity.eval.records import ARMS

    for arm in sorted(rows):
        r = rows[arm]
        name = ARMS.get(arm, _FALLBACK_ARM_NAMES.get(arm, arm))
        print(f"{arm:<8}{r['n']:>4}{r['pass']:>6}{r['fail']:>6}{r['error']:>6}  {name}")
    print()
    print("注：未找到 eval/report.py，本表仅是原始计数，不是评测结论（口径待补）。")
    return 0


def add_parser(top) -> None:
    e = top.add_parser("eval", help="评测（Gate 0 / 跑分汇总）")
    esub = e.add_subparsers(dest="cmd", required=True)

    g = esub.add_parser("gate0", help="Gate 0：重复读取率测量（阈值决定是否做 Context Paging）")
    g.add_argument("--db", default="", help="轨迹 SQLite 库路径（默认 TraceStore 单例）")
    g.add_argument("--session", default="", help="只统计该会话（默认全部）")
    g.set_defaults(fn=cmd_eval_gate0)

    s = esub.add_parser("summary", help="汇总 attempts.jsonl（per-arm 表）")
    s.add_argument("attempts_file", help="eval/records.py 产出的 JSONL")
    s.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    s.set_defaults(fn=cmd_eval_summary)

    # skill 子命令拆到独立模块（本文件已接近 300 行上限；
    # 且「跑分汇总」与「Skill 准入」是两件寿命不同的事）
    from backend.necessity.cli.skill_cmd import add_parser as add_skill

    add_skill(esub)
