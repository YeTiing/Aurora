"""指标聚合与统计报告 —— EVAL.md §2（指标）/ §6（统计与口径）。

报告里最容易作弊的三处，本文件的对应做法：

1. **先按 (任务, arm) 取中位数，再做配对比较**（§2.3 + §6.2）。
   直接对原始 3 次重复做检验会虚增 n（把 90 次当成 90 个独立样本），
   配对结构被抹掉 —— 这是最隐蔽的一种注水。
2. **配对差**，不是两组各自的中位数相减。
3. **措辞由 stats.make_verdict 统一生成**，报告不手写「显著」二字，
   避免 §6.3 的限定语在复制粘贴中丢失。

指标方向：完成率/ρ 越高越好；重复读取率/token/冗余率越低越好。
效应量统一乘上方向再进 make_verdict，这样 delta>0 恒表示「改善」。
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.necessity.eval.records import ARMS, Attempt, read_attempts  # noqa: E402
from backend.necessity.eval.stats import (  # noqa: E402
    cliffs_delta,
    iqr,
    make_verdict,
    median,
    median_diff,
    wilcoxon_signed_rank,
)

# 指标定义（name, 取值函数, 越高越好?）
# 与 EVAL.md §2.1 的指标表一一对应，只取可从 Attempt 直接算出的那几个
METRICS: tuple[tuple[str, object, bool], ...] = (
    ("完成率",      lambda a: 1.0 if a.status == "pass" else 0.0, True),
    ("重复读取率",   lambda a: a.gates.waste_ratio,              False),
    ("token",       lambda a: float(a.tokens),                   False),
    ("约束保持率ρ",  lambda a: a.gates.constraint_rho,            True),
    ("冗余率",      lambda a: a.gates.redundancy_ratio,           False),
)

# §6.3 要求这段口径写进报告，面试被问到时照实说
DISCLAIMER = (
    "口径说明（EVAL.md §6.3）：样本量小（n=30），统计功效低，只能检测大效应。\n"
    "  仅当效应量大（Cliff's delta > 0.47）且 p < 0.05 时，才声称「在该任务集上\n"
    "  观察到显著差异」；效应量小但方向一致时只说「观察到趋势，样本量不足以确认」。\n"
    "  p < 0.05 不等于「普遍有效」—— 以上结论严格限于本任务集，不外推。"
)


# ── 聚合 ─────────────────────────────────────────────────────────

@dataclass
class ArmSummary:
    """一个 arm 的聚合画像（每任务先取 3 次的中位数）。"""
    arm: str
    n_tasks: int = 0
    n_attempts: int = 0
    medians: dict[str, float] = field(default_factory=dict)
    iqrs: dict[str, tuple[float, float]] = field(default_factory=dict)
    completion_rate: float = 0.0
    statuses: dict[str, int] = field(default_factory=dict)


def group_median(attempts: list[Attempt], fn) -> float:
    """一个 arm 在一个任务上的指标中位数（§6.2：每组每任务跑 3 次取中位数）。

    单独抽出来是为了让配对检验的输入**只有一个数/任务**，避免把
    3 次重复当成 3 个独立样本 —— 那会把 n 从 30 虚增到 90。
    """
    vals = [fn(a) for a in attempts]
    return median(vals) if vals else 0.0


def summarize_arm(arm: str, by_task: dict[str, list[Attempt]]) -> ArmSummary:
    s = ArmSummary(arm=arm, n_tasks=len(by_task),
                   n_attempts=sum(len(v) for v in by_task.values()))
    for name, fn, _higher in METRICS:
        vals = [group_median(runs, fn) for runs in by_task.values()]
        s.medians[name] = median(vals) if vals else 0.0
        s.iqrs[name] = iqr(vals) if vals else (0.0, 0.0)
    passes = sum(1 for runs in by_task.values()
                 if any(a.status == "pass" for a in runs))
    s.completion_rate = passes / len(by_task) if by_task else 0.0
    for runs in by_task.values():
        for a in runs:
            s.statuses[a.status] = s.statuses.get(a.status, 0) + 1
    return s


def summaries(attempts: list[Attempt]) -> dict[str, ArmSummary]:
    grouped: dict[str, dict[str, list[Attempt]]] = {}
    for a in attempts:
        grouped.setdefault(a.arm, {}).setdefault(a.task_id, []).append(a)
    return {arm: summarize_arm(arm, by_task) for arm, by_task in grouped.items()}


# ── 配对比较 ─────────────────────────────────────────────────────

@dataclass
class Comparison:
    metric: str
    arm: str
    baseline: str
    n_pairs: int
    delta: float            # 已按指标方向调整：>0 = 改善
    p_value: float | None
    median_diff: float      # 已按方向调整，与 delta 同号
    claim: str
    text: str


def compare(arm_attempts: dict[str, list[Attempt]],
            base_attempts: dict[str, list[Attempt]],
            arm: str, baseline: str, metric: str,
            fn, higher_is_better: bool) -> Comparison | None:
    """按任务配对比较 arm 与 baseline。只在两边都有数据的任务上比。"""
    common = sorted(set(arm_attempts) & set(base_attempts))
    if not common:
        return None
    x = [group_median(arm_attempts[t], fn) for t in common]
    y = [group_median(base_attempts[t], fn) for t in common]
    sign = 1.0 if higher_is_better else -1.0

    raw_delta = cliffs_delta(x, y)
    delta = raw_delta * sign          # >0 恒为「改善」，供 verdict 用
    # 配对检验用方向调整后的差：对「越低越好」的指标，x-y 的改善是负的，
    # 乘 sign 后统一，p 值不受影响但 median_diff 的读法一致
    diffs = [(x[i] - y[i]) * sign for i in range(len(common))]
    w = wilcoxon_signed_rank(diffs)
    v = make_verdict(delta, w.p_value, w.n, label=arm, metric=metric)
    md = median_diff([xi * sign for xi in x], [yi * sign for yi in y])
    return Comparison(metric=metric, arm=arm, baseline=baseline, n_pairs=w.n,
                      delta=delta, p_value=w.p_value, median_diff=md,
                      claim=v.claim, text=v.text)


def all_comparisons(attempts: list[Attempt], baseline: str = "A",
                    vs_extra: str = "A_prime") -> list[Comparison]:
    """每个 arm 对比 baseline（默认 A），并额外把 E 对比最关键的 A′（§3.1）。"""
    grouped: dict[str, dict[str, list[Attempt]]] = {}
    for a in attempts:
        grouped.setdefault(a.arm, {}).setdefault(a.task_id, []).append(a)

    out: list[Comparison] = []
    for arm, by_task in grouped.items():
        for base in {baseline, vs_extra if arm == "E" else baseline}:
            if base not in grouped or base == arm:
                continue
            for name, fn, higher in METRICS:
                c = compare(by_task, grouped[base], arm, base, name, fn, higher)
                if c is not None:
                    out.append(c)
    return out


# ── 输出 ─────────────────────────────────────────────────────────

def render_table(sums: dict[str, ArmSummary]) -> str:
    arms = [a for a in ARMS if a in sums] + [a for a in sums if a not in ARMS]
    head = (f"{'arm':<9}{'任务':>5}{'运行':>5}{'完成率':>9}"
            f"{'重复读取率':>12}{'token':>9}{'ρ':>8}{'冗余率':>9}   说明")
    lines = [head, "-" * len(head)]
    for arm in arms:
        s = sums[arm]
        lines.append(
            f"{arm:<9}{s.n_tasks:>5}{s.n_attempts:>5}{s.completion_rate:>9.1%}"
            f"{s.medians['重复读取率']:>12.2%}{s.medians['token']:>9.0f}"
            f"{s.medians['约束保持率ρ']:>8.2f}{s.medians['冗余率']:>9.2%}"
            f"   {ARMS.get(arm, '')}"
        )
    return "\n".join(lines)


def render_comparisons(comps: list[Comparison], baseline: str = "A") -> str:
    lines = [f"配对比较（基线 {baseline} / E 额外对比 A_prime；配对数 = 两边都有数据的任务数）", ""]
    for c in comps:
        # 效应量可忽略的不刷屏（含全零差这种「无法计算」的情形）
        if abs(c.delta) < 0.147:
            continue
        lines.append(f"  [{c.arm} vs {c.baseline}] {c.metric}: {c.text}")
    if len(lines) == 2:
        lines.append("  （没有达到可报告量级的差异）")
    return "\n".join(lines)


def build_report(attempts: list[Attempt], baseline: str = "A",
                 extra: str = "A_prime") -> dict:
    sums = summaries(attempts)
    comps = all_comparisons(attempts, baseline=baseline, vs_extra=extra)
    return {
        "disclaimer": DISCLAIMER,
        "n_attempts": len(attempts),
        "arms": {
            arm: {
                "n_tasks": s.n_tasks, "n_attempts": s.n_attempts,
                "completion_rate": round(s.completion_rate, 4),
                "medians": {k: round(v, 6) for k, v in s.medians.items()},
                "iqr": {k: [round(x, 6) for x in v] for k, v in s.iqrs.items()},
                "statuses": s.statuses,
            } for arm, s in sums.items()
        },
        "comparisons": [
            {"metric": c.metric, "arm": c.arm, "baseline": c.baseline,
             "n_pairs": c.n_pairs, "cliffs_delta": round(c.delta, 4),
             "p_value": None if c.p_value is None else round(c.p_value, 6),
             "median_diff": round(c.median_diff, 6),
             "claim": c.claim, "text": c.text}
            for c in comps
        ],
    }


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认 GBK：不重设会在打印中文表格时中断
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    p = argparse.ArgumentParser(prog="python -m eval.report",
                                description="跑分报告（EVAL.md §6 口径）")
    p.add_argument("--in", dest="inp", default=str(ROOT / "eval" / "runs" / "attempts.jsonl"))
    p.add_argument("--baseline", default="A")
    p.add_argument("--extra", default="A_prime", help="除基线外额外对比的 arm（E 用）")
    p.add_argument("--json", default="", help="把结构化报告写到此路径")
    args = p.parse_args(argv)

    attempts = read_attempts(args.inp)
    if not attempts:
        print(f"没有读到任何记录：{args.inp}")
        print("先跑：python -m eval.runner --gate0  或  python -m eval.runner")
        return 2

    sums = summaries(attempts)
    print(f"记录数 {len(attempts)}    文件 {args.inp}")
    print()
    print(render_table(sums))
    print()
    print(render_comparisons(all_comparisons(attempts, args.baseline, args.extra),
                             args.baseline))
    print()
    print(DISCLAIMER)

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(
            json.dumps(build_report(attempts, args.baseline, args.extra),
                       ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n结构化报告：{args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
