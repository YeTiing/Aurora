"""`necessity eval skill` —— Skill 效果评测与准入（规范 §7）。

## 为什么是 CLI 子命令而不是自动执行

A5 是**离线对照实验**：每臂 ≥ 8 个任务 × N 臂 × 3 次重复，跑一轮要数小时
（规范 §7.11 的成本特征：「离线评测，不占运行时预算」）。
把它挂进运行时会毁掉每个任务的延迟，所以它只在人显式调用时跑。

## 这个命令的存在意义

在此之前 `eval/skill_arms.py` 与 `eval/skill_registry.py` 是**孤立代码**：
接口正确、测试全过，但没有任何东西调用它们 —— 那是「写了但没生效」。
本命令把它们接上真实数据（`attempts.jsonl`），并写入注册表。

## 退出码（与 gate0 一样承重）

    0  评测完成，且 verdict 是 adopt / reject（有结论）
    1  评测完成，但 verdict 是 inconclusive（**样本不足**，不是出错）
    2  输入不可用（文件缺失 / 记录为空 / 臂不匹配）

第 1 与第 2 必须分开：「样本不够所以没结论」与「命令用错了」是完全不同的
事，混在一起会让自动化脚本无法判断该重跑还是该修参数。
"""
from __future__ import annotations

import sys
from pathlib import Path


def cmd_eval_skill(args) -> int:
    from backend.necessity.eval.records import read_attempts
    from backend.necessity.eval.skill_arms import evaluate_skill
    from backend.necessity.eval.skill_registry import SkillRegistry

    path = Path(args.attempts_file)
    if not path.is_file():
        print(f"文件不存在: {path}", file=sys.stderr)
        return 2

    attempts = read_attempts(path)
    if not attempts:
        print(f"没有可用的记录: {path}", file=sys.stderr)
        return 2

    baseline = [a for a in attempts if a.arm == args.baseline_arm]
    treatment = [a for a in attempts if a.arm == args.treatment_arm]
    if not baseline or not treatment:
        # 明确指出缺哪一边 —— 只报「没有数据」会让人无从下手
        print(
            f"臂不匹配：baseline={args.baseline_arm!r} 有 {len(baseline)} 条，"
            f"treatment={args.treatment_arm!r} 有 {len(treatment)} 条。\n"
            f"文件里出现的臂：{sorted({a.arm for a in attempts})}",
            file=sys.stderr)
        return 2

    result = evaluate_skill(args.skill, args.version, baseline, treatment,
                            min_tasks=args.min_tasks)

    print(f"Skill 评测：{result.skill}@{result.version}")
    print(f"  臂：baseline={args.baseline_arm}（{len(baseline)} 条记录）"
          f"  treatment={args.treatment_arm}（{len(treatment)} 条）")
    print(f"  效应量（Cliff's delta）: {result.effect_size:+.4f}")
    print(f"  p 值                   : {result.p_value:.4f}")
    print(f"  任务成功率  baseline={result.task_success[0]:.2%}"
          f"  treatment={result.task_success[1]:.2%}")
    print(f"  安全违规    baseline={result.security_violations[0]}"
          f"  treatment={result.security_violations[1]}")
    print(f"  判定：{result.verdict}")

    if result.verdict == "inconclusive":
        print()
        print("  ⚠️ 样本不足或有正效应但未达显著 —— **不得准入**"
              "（规范 §7.8：effect_size > 0 但 p ≥ 0.05 属 inconclusive）。")
        print(f"     规范要求每臂 ≥ {args.min_tasks} 个任务；"
              "扩大任务集后重跑才有结论。")

    # 登记（只有显式给了注册表路径才写盘 —— 不隐式改状态）
    if args.registry:
        reg = SkillRegistry(args.registry)
        reg.record(result)
        state = reg.get(result.skill)
        print()
        print(f"  已登记到 {args.registry}：adopted={getattr(state, 'adopted', False)}")
        if getattr(reg, "_entries", None) and result.verdict == "inconclusive":
            print("  （inconclusive 不改变准入状态 —— 证据不足既不该准入，"
                  "也不该撤销已通过的版本）")

    return 1 if result.verdict == "inconclusive" else 0


def add_parser(esub) -> None:
    """挂到 `necessity eval` 下。"""
    s = esub.add_parser("skill", help="Skill 效果评测与准入（规范 §7）")
    s.add_argument("attempts_file", help="runner 产出的 attempts.jsonl")
    s.add_argument("--skill", required=True, help="Skill 名")
    s.add_argument("--version", required=True, help="Skill 版本")
    s.add_argument("--baseline-arm", default="A_prime",
                   help="基线臂（默认 A_prime：零能力 + 叮嘱）")
    s.add_argument("--treatment-arm", required=True,
                   help="实验臂（形如 S_my_skill_v1）")
    s.add_argument("--min-tasks", type=int, default=8,
                   help="每臂最少任务数（规范 §7.7 硬要求）")
    s.add_argument("--registry", default="",
                   help="skill_registry.json 路径；给了才写盘")
    s.set_defaults(fn=cmd_eval_skill)


__all__ = ["add_parser", "cmd_eval_skill"]
