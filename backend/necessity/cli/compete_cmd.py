"""`necessity compete` —— 多方案竞争裁决（规范 §8）。

## 为什么需要 CLI 入口

A6 的 `judge()` / `diversity()` 是纯函数，但在此之前**没有任何自动化入口**
（只有库调用）—— 那是「写了但没生效」的另一种形态：功能存在，没人用得上。

本命令把「一组候选的指标 → 裁决」变成可执行的一步，用于：
  · 离线复盘（把历史候选的指标拿来重跑，验证裁决是否可解释）
  · 评测（§8.7 的「胜者显著优于随机」需要反复裁决才能统计）

## 退出码（与 gate0 / skill 一致的承重设计）

    0  有胜者
    1  **无可行候选**（全部未通过硬门禁）—— 需要人来决定，不是出错
    2  输入不可用（无候选）

1 与 2 分开的理由同前两个命令：前者是「裁决结论」，后者是「命令用错了」。
自动化脚本据此决定该找人来定还是该改参数。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _load_candidates(path: Path):
    """读候选 JSON。

    格式：`[{id, strategy, diff, metrics:{...}}, ...]`，或
    评测产出的 `{"candidates": [...]}` 包装形态。
    """
    from backend.necessity.compete.schema import Candidate, CandidateMetrics

    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("candidates") or []
    out = []
    for d in data or []:
        if not isinstance(d, dict):
            continue
        m = d.get("metrics") or {}
        out.append(Candidate(
            id=str(d.get("id", "")), strategy=str(d.get("strategy", "")),
            branch=str(d.get("branch", "")), diff=str(d.get("diff", "")),
            generator=str(d.get("generator", "")), error=str(d.get("error", "")),
            metrics=CandidateMetrics(
                tests_pass=bool(m.get("tests_pass", False)),
                tests_total=int(m.get("tests_total", 0)),
                impact_files=int(m.get("impact_files", 0)),
                impact_symbols=int(m.get("impact_symbols", 0)),
                impact_stale=bool(m.get("impact_stale", False)),
                security_findings=int(m.get("security_findings", 0)),
                security_critical=int(m.get("security_critical", 0)),
                redundancy_ratio=float(m.get("redundancy_ratio", 0.0)),
                diff_lines=int(m.get("diff_lines", 0)),
                violated_constraints=list(m.get("violated_constraints") or []),
            )))
    return out


def cmd_compete_judge(args) -> int:
    from backend.necessity.compete.judge import diversity, judge
    from backend.necessity.compete.schema import MIN_DIVERSITY

    path = Path(args.candidates_file)
    if not path.is_file():
        print(f"文件不存在: {path}", file=sys.stderr)
        return 2

    try:
        cands = _load_candidates(path)
    except Exception as e:
        print(f"候选解析失败: {type(e).__name__}: {e}", file=sys.stderr)
        return 2

    if not cands:
        print(f"没有候选: {path}", file=sys.stderr)
        return 2

    v = judge(cands)
    div = diversity(cands)

    print(f"候选 {len(cands)} 个 | 多样性 {div:.2f}（门禁 {MIN_DIVERSITY}）")
    if div < MIN_DIVERSITY:
        print("  ⚠️ 多样性不达标 —— 策略可能没起作用，『竞争』是假的"
              "（规范 §8.8：先改策略再谈竞争）")

    print()
    if v.no_viable_candidate:
        print("裁决：**无可行候选**")
        for cid, why in v.rejected_reasons.items():
            print(f"  - {cid}: {why}")
        print()
        print("  ⚠️ 规范 §8.5①：不得选『违反最少』的那个 —— "
              "违规方案需要人来决定是否接受。")
    else:
        print(f"胜者：{v.winner}")
        print(f"排序：{' > '.join(v.ranking)}")
        if v.rejected_reasons:
            print()
            print("被淘汰：")
            for cid, why in v.rejected_reasons.items():
                print(f"  - {cid}: {why}")

    print()
    print("裁决依据（逐级比较）：")
    for t in v.tie_breakers:
        print(f"  {t}")

    if v.notes:
        print()
        for n in v.notes:
            print(f"  注：{n}")

    if not v.explainable:
        print("\n⚠️ 裁决不可解释 —— 规范 §8.7 要求 100% 可解释，不可发布。",
              file=sys.stderr)
        return 2
    return 1 if v.no_viable_candidate else 0


def add_parser(top) -> None:
    c = top.add_parser("compete", help="多方案竞争裁决（规范 §8）")
    csub = c.add_subparsers(dest="cmd", required=True)

    j = csub.add_parser("judge", help="对一组候选裁决（硬门禁 + 按序比较）")
    j.add_argument("candidates_file", help="候选 JSON 路径")
    j.set_defaults(fn=cmd_compete_judge)


__all__ = ["add_parser", "cmd_compete_judge"]
