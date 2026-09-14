"""`necessity reduce analyze` —— Diff Reducer 的离线入口。

DIFF_REDUCER.md §6.4 把「离线工具」列为三种集成方式中**首选**的一种：
    「零侵入，可独立验证，也最容易跑出实验数字」。
本模块就是那个工具：输入一个 unified diff + 仓库路径，输出 §5.5 报告。

与主循环的关系（§1）：
    钩子（core/reduce/hooks.py）只采集 diff；最小化要跑上百次测试，
    绝不放在主循环里。所以分析只能从这里手动/脚本触发。

⚠️ 收敛状态是**一等公民**：
    §7 总原则「宁可返回未收敛的最优解，也不要返回错误结论」。
    因此未收敛时仍打印报告，但**退出码为 1** —— 调用方必须能从退出码
    分辨「这是结论」还是「这是预算内搜到的最好结果」。把它藏进 stdout
    等于让脚本把半成品当结论用。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable

# 测试 / 外部集成注入点。
#
# ⚠️ runner 契约：`(subset: list) -> "pass" | "fail" | "error"`
#    subset 的元素是**一致性组**（即 Hunk 列表），不是单个 Hunk ——
#    ddmin 搜索的单位是组（DIFF_REDUCER.md §3.1）。子集的语义是
#    「保留这些组」，取消勾选的组会被撤销后再跑测试。
#    把参数当成 Hunk 列表会让判定逻辑整个错位（且不报错，只是结论全错）。
#
# 非 None 时完全跳过沙箱与 pytest（测试离线、无 pyright）。
# 生产用法走 --test-runner <module:func>，见 load_injected_runner。
INJECTED_RUNNER: Callable[[list], str] | None = None


def read_diff(spec: str) -> str:
    """读 diff。`-` 表示 stdin（管道友好）。"""
    if spec == "-":
        return sys.stdin.read()
    p = Path(spec)
    if not p.is_file():
        raise FileNotFoundError(spec)
    return p.read_text(encoding="utf-8", errors="replace")


def load_injected_runner(spec: str) -> Callable[[list], str]:
    """按 `module:func` 加载外部判定器（给集成方用，测试一般直接设 INJECTED_RUNNER）。"""
    mod_name, _, attr = spec.partition(":")
    if not attr:
        raise ValueError("--test-runner 需要 <module>:<func> 形式")
    import importlib

    mod = importlib.import_module(mod_name)
    fn = getattr(mod, attr)
    return fn


def _resolve_runner(args) -> Callable[[list], str] | None:
    if INJECTED_RUNNER is not None:
        return INJECTED_RUNNER
    if getattr(args, "test_runner", ""):
        return load_injected_runner(args.test_runner)
    return None


def _pick_direction(args) -> str | None:
    """把 --direction 与两个 only 开关归一成一个方向；冲突返回 None。"""
    if args.minimize_necessary_only and args.culprit_only:
        return None
    forced = "necessary" if args.minimize_necessary_only else (
        "culprit" if args.culprit_only else "")
    if forced and args.direction and args.direction != forced:
        return None
    return forced or args.direction or "necessary"


def cmd_reduce_analyze(args) -> int:
    return _run_analyze(args)


def _run_analyze(args) -> int:
    from backend.necessity.reduce import (
        Budget,
        Sandbox,
        all_hunks,
        build_coherence_groups,
        build_culprit_report,
        build_report,
        format_text,
        minimize_culprit,
        minimize_necessary,
        parse_unified_diff,
    )

    direction = _pick_direction(args)
    if direction is None:
        return _usage("--direction 与 --minimize-necessary-only/--culprit-only 冲突")

    try:
        diff_text = read_diff(args.diff_file)
    except FileNotFoundError:
        return _usage(f"读不到 diff 文件: {args.diff_file}")

    files = parse_unified_diff(diff_text)
    hunks = all_hunks(files)
    if not hunks:
        return _usage("diff 里没有可解析的 hunk（需要标准 unified diff）")

    # §6.3：hunk 数超上限 → 先做文件级粗最小化（保守，组偏大不误判）
    exceeded = len(hunks) > args.max_hunk_count
    if exceeded:
        groups = build_coherence_groups(hunks, symbols_by_file=None)
    else:
        groups = build_coherence_groups(hunks)

    budget = Budget(
        max_test_runs=args.max_test_runs,
        max_wall_time=args.max_wall_time,
        max_hunk_count=args.max_hunk_count,
    )
    budget.start()

    runner = _resolve_runner(args)
    baseline = "n/a"
    sandbox_mode, sandbox_degraded = "injected", False

    if runner is not None:
        # 注入判定器：不建沙箱、不跑真实测试（离线测试路径）
        result = _search(direction, groups.groups, runner, budget,
                         minimize_necessary, minimize_culprit)
    else:
        result, baseline, sandbox_mode, sandbox_degraded = _sandbox_search(
            args, direction, files, hunks, groups.groups, budget,
            Sandbox, minimize_necessary, minimize_culprit,
        )

    # ⚠️ 形状差异：minimize 在「一致性组」上搜索，best 是 list[list[Hunk]]；
    # 而 report 模块按**扁平 hunk 列表**计算行数与冗余率（id 比较也用 hunk.id）。
    # core 自己的 hooks.analyze_last 也存在这个不匹配。本层不能改 core，
    # 因此在交给报告前把分组拍平 —— 否则 build_report 会把每个「组」当成
    # 一个没有 .id 的对象，行数统计全部错成 0。
    result = _flatten_best(result)

    common = dict(
        task_id=getattr(args, "task_id", "") or Path(args.diff_file).name,
        baseline_test=baseline,
        sandbox_mode=sandbox_mode,
        sandbox_degraded=sandbox_degraded,
        max_hunk_count_exceeded=exceeded,
    )
    if direction == "culprit":
        # build_culprit_report 的签名比 build_report 窄（只有 task_id/
        # baseline_test/final_test/notes）；沙箱降级与粗最小化写进 notes，
        # 避免把它们静默丢掉。
        notes = []
        if sandbox_degraded:
            notes.append(f"隔离降级为目录复制（{sandbox_mode}）—— 结果等价但更慢（§7 边界 1）")
        if exceeded:
            notes.append("hunk 数超过 max_hunk_count，已先做文件级粗最小化（§6.3）")
        rep = build_culprit_report(
            hunks, result,
            task_id=common["task_id"], baseline_test=baseline, notes=notes,
        )
    else:
        final = "pass" if result.best else ("n/a" if result.converged else "unknown")
        rep = build_report(hunks, result, final_test=final, **common)

    if args.json:
        print(json.dumps(rep.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(format_text(rep))

    # ⚠️ 未收敛必须体现在退出码上，不能只写在报告里（§7 总原则）
    return 0 if rep.metrics.get("converged") else 1


def _search(direction, groups, runner, budget, nec_fn, culp_fn):
    fn = culp_fn if direction == "culprit" else nec_fn
    return fn(groups, runner, budget)


def _flatten_best(result):
    """把 SearchResult.best 从「组列表」拍平成 hunk 列表（见上方的形状说明）。

    `converged` / `test_runs` 等状态字段原样保留 —— 只改 best 的形状。
    """
    best = getattr(result, "best", None) or []
    if not best or not isinstance(best[0], (list, tuple)):
        return result  # 已经是扁平的（某些调用路径直接传 hunk 列表）
    flat = [h for group in best for h in group]
    try:
        result.best = flat
    except Exception:
        from dataclasses import replace

        result = replace(result, best=flat)
    return result


def _sandbox_search(args, direction, files, hunks, groups, budget,
                    Sandbox, nec_fn, culp_fn):
    """真实路径：在隔离副本里撤销子集并跑 pytest。

    语义来自 hooks.analyze_last（必要性最小化 = 撤销不在子集里的 hunk），
    这里额外支持致败方向：撤销子集里的 hunk，看测试是否由 fail 转 pass。
    """
    from backend.necessity.reduce import apply_text_patch
    from backend.necessity.reduce.premise import check_applied

    repo = str(Path(args.repo).resolve())
    with Sandbox(repo, getattr(args, "base_commit", "") or "") as sb:
        info = sb.create()

        # ⚠️ **前提校验**：隔离环境必须真的处于「改动已应用」的状态。
        # 它按 `git worktree add --detach <base_commit>` 建立，默认 base=HEAD；
        # 若改动还在**工作区**（未提交），worktree 里是旧代码 ——
        # 于是「撤销某 hunk」变成恒等变换，所有改动被误判为冗余（实测冗余率恒 1.0）。
        # 这里宁可拒绝回答，也不给一个看起来合理的错数字（DIFF_REDUCER.md §7）。
        premise = check_applied(files, sb.read)
        if not premise.ok:
            raise RuntimeError(
                "反事实实验前提不成立，已中止（宁可不给结论，也不给错结论）\n\n"
                + premise.reason
            )

        baseline, _ = sb.run_tests(args.targets or None)

        post_state: dict[str, str] = {}
        for f in files:
            if not f.is_deleted:
                post_state[f.path] = sb.read(f.path)

        def _apply(drop):
            sb.revert_all()
            grouped: dict[str, list] = {}
            for h in drop:
                grouped.setdefault(h.file, []).append(h)
            for fpath, hs in grouped.items():
                base = post_state.get(fpath)
                if base is None:
                    continue
                sb.apply(fpath, apply_text_patch(base, hs, reverse=True))

        # subset 的元素是组（Hunk 列表）—— 见模块顶部的 runner 契约说明。
        # 两个方向在「撤销非子集」这一步上同义：保留 = 子集，其余撤销。
        #   necessary：撤销后仍 pass ⇒ 被撤销的是冗余
        #   culprit ：撤销后由 fail 转 pass ⇒ 被撤销的是致败
        def _runner(subset):
            keep: set[str] = set()
            for item in subset:
                if isinstance(item, (list, tuple)):
                    keep.update(str(getattr(h, "id", h)) for h in item)
                else:
                    keep.add(str(getattr(item, "id", item)))
            _apply([h for h in hunks if str(h.id) not in keep])
            res, _out = sb.run_tests(args.targets or None)
            return res

        result = _search(direction, groups, _runner, budget, nec_fn, culp_fn)
        return result, baseline, info.mode, info.degraded


def _usage(msg: str) -> int:
    print(msg, file=sys.stderr)
    return 2


def add_parser(top) -> None:
    r = top.add_parser("reduce", help="Diff Reducer（离线最小化，能力 3）")
    rsub = r.add_subparsers(dest="cmd", required=True)

    a = rsub.add_parser("analyze", help="分析一个 diff：找冗余 / 定位致败")
    a.add_argument("diff_file", help="unified diff 路径，或 `-` 读 stdin")
    a.add_argument("--repo", required=True, help="仓库路径（沙箱基于它创建）")
    a.add_argument("--base-commit", default="", help="沙箱基线 commit（默认 HEAD）")
    a.add_argument("--direction", choices=("necessary", "culprit"), default=None,
                   help="necessary=找冗余（默认）；culprit=定位致败改动")
    a.add_argument("--minimize-necessary-only", action="store_true",
                   help="只做必要性最小化（等价 --direction necessary）")
    a.add_argument("--culprit-only", action="store_true",
                   help="只做致败定位（等价 --direction culprit）")
    a.add_argument("--max-test-runs", type=int, default=200, help="判定次数上限")
    a.add_argument("--max-wall-time", type=float, default=15 * 60.0,
                   help="墙钟上限（秒）")
    a.add_argument("--max-hunk-count", type=int, default=60,
                   help="超过则先做文件级粗最小化")
    a.add_argument("--targets", nargs="*", default=None, help="pytest 目标（默认 tests/）")
    a.add_argument("--test-runner", default="",
                   help="外部判定器 <module>:<func>，给出则跳过沙箱（集成用）")
    a.add_argument("--task-id", default="", help="报告里的任务名")
    a.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    a.set_defaults(fn=cmd_reduce_analyze)
