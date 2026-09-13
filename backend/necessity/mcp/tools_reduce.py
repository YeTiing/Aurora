"""Diff Reducer 的两个 MCP 工具。

INTEGRATION.md §1.3 把 Diff Reducer 列为 ✅ 完全可以 MCP 化：输入是
diff + 仓库路径，离线、只读，不需要宿主配合。

与 CLI（cli/reduce_cmd.py）的关系：本层是同一核心的 MCP 出口，不复制
逻辑；沙箱使用、方向选择、报告构造全部复用 core.reduce。
"""

from __future__ import annotations

from pathlib import Path

from .base import Tool, ToolOutcome, ToolError, obj_schema, optional, read_diff_arg, require, str_list

__all__ = ["reduce_minimize", "reduce_redundancy", "REDUCE_TOOLS"]

# hunk 数超过此值 -> 先做文件级粗合并（DIFF_REDUCER.md §6.3，保守不误判）
MAX_HUNKS_BEFORE_COARSE = 60


def _prepare(hunks):
    from backend.necessity.reduce import build_coherence_groups

    if not hunks:
        raise ToolError("diff 里没有可解析的 hunk（需要标准 unified diff）")
    if len(hunks) > MAX_HUNKS_BEFORE_COARSE:
        return build_coherence_groups(hunks, symbols_by_file=None), True
    return build_coherence_groups(hunks), False


def reduce_minimize(params: dict) -> ToolOutcome:
    """§5.5 完整报告：最小 diff / 冗余清单 / 致败定位。

    会真实建 worktree 沙箱并跑 pytest —— 这是四个工具里唯一有副作用
    （写临时目录）且耗时的。任何异常都在 server 层被转成 isError，
    不会让 MCP 进程退出。
    """
    from backend.necessity.reduce import (
        Budget,
        Sandbox,
        all_hunks,
        build_culprit_report,
        build_report,
        format_text,
        minimize_culprit,
        minimize_necessary,
        parse_unified_diff,
    )
    from backend.necessity.reduce.apply import apply_text_patch

    repo = require(params, "repo", str)
    if not Path(repo).is_dir():
        raise ToolError(f"repo 不是目录: {repo}")
    diff_text = read_diff_arg(params)
    direction = optional(params, "direction", str, "necessary")
    if direction not in ("necessary", "culprit"):
        raise ToolError("'direction' 只能是 'necessary' 或 'culprit'，"
                        f"收到 {direction!r}")
    targets = str_list(params, "targets")
    max_runs = optional(params, "max_test_runs", int, 200)
    if max_runs <= 0:
        raise ToolError("'max_test_runs' 必须为正整数")

    files = parse_unified_diff(diff_text)
    hunks = all_hunks(files)
    groups, exceeded = _prepare(hunks)
    budget = Budget(max_test_runs=max_runs)
    budget.start()
    minimizer = minimize_culprit if direction == "culprit" else minimize_necessary

    with Sandbox(repo) as sb:
        info = sb.create()
        baseline, _ = sb.run_tests(targets or None)
        post_state = {f.path: sb.read(f.path) for f in files if not f.is_deleted}

        def _runner(subset):
            """判定器：把「不在子集里的 hunk 全部撤销」后跑测试。

            两个方向共用同一撤销语义 —— 与 CLI `_sandbox_search` 一致：
            必要性求最小通过集；致败求最小失败集（搜索算法内部处理差异）。
            """
            keep = {str(getattr(h, "id", h)) for h in subset}
            sb.revert_all()
            grouped: dict[str, list] = {}
            for h in hunks:
                if str(h.id) not in keep:
                    grouped.setdefault(h.file, []).append(h)
            for fpath, hs in grouped.items():
                base = post_state.get(fpath)
                if base is not None:
                    sb.apply(fpath, apply_text_patch(base, hs, reverse=True))
            res, _out = sb.run_tests(targets or None)
            return res

        result = minimizer(groups.groups, _runner, budget)

    common = dict(baseline_test=baseline, sandbox_mode=info.mode,
                  sandbox_degraded=info.degraded, max_hunk_count_exceeded=exceeded)
    if direction == "culprit":
        rep = build_culprit_report(hunks, result, **common)
    else:
        final = "pass" if result.best else ("n/a" if result.converged else "unknown")
        rep = build_report(hunks, result, final_test=final, **common)
    return ToolOutcome(text=format_text(rep), structured=rep.to_dict())


def reduce_redundancy(params: dict) -> ToolOutcome:
    """只算冗余率（cheap 路径）—— 不跑测试、不建沙箱。

    ⚠️ 可证明性说明（必须保留，否则会误导）：
        DIFF_REDUCER.md §8.5 的可证明冗余率以「删掉后测试仍通过」为 oracle。
        不跑测试就拿不到那个数。所以本工具返回的是**结构性估计**，并把
        `confirmed=false` 显式写进结果 —— 调用方不得把它当 §5.5 指标用。
        保留它的理由：Agent 常常只是想要一个量级，跑上百次测试不划算。
    """
    from backend.necessity.reduce import all_hunks, parse_unified_diff

    require(params, "repo", str)
    diff_text = read_diff_arg(params)
    str_list(params, "targets")   # 接受但未使用；为接口一致性保留

    hunks = all_hunks(parse_unified_diff(diff_text))
    if not hunks:
        raise ToolError("diff 里没有可解析的 hunk（需要标准 unified diff）")

    # 估计口径：完全相同的 hunk（同文件、同增删数、同内容）视为疑似冗余。
    # 新文件的 hunk 排除在外 —— 新文件整体是必要的，重复的初始化块不算冗余。
    seen: set[tuple] = set()
    suspected: list[str] = []
    for h in hunks:
        key = (h.file, h.added, h.removed, tuple(h.body))
        if key in seen and not h.is_new_file:
            suspected.append(h.id)
        seen.add(key)

    est = len(suspected) / len(hunks)
    structured = {
        "redundancy_ratio_estimate": round(est, 4),
        "confirmed": False,
        "hunks": len(hunks),
        "suspected_duplicate_hunks": suspected,
        "note": (
            "结构性估计（重复 hunk 占比），不是 §5.5 的可证明冗余率。"
            "可证明值需跑测试：用 reduce_minimize。"
        ),
    }
    text = (
        f"冗余率估计: {est:.2%}（{len(suspected)}/{len(hunks)} 个疑似重复 hunk）\n"
        "注意: 未跑测试，confirmed=false；可证明值请用 reduce_minimize。"
    )
    return ToolOutcome(text=text, structured=structured)


_DIFF_PROPS = {
    "diff": {"type": "string", "description": "unified diff 文本"},
    "diff_path": {"type": "string", "description": "或改为给 diff 文件路径"},
}

REDUCE_TOOLS = [
    Tool(
        name="reduce_minimize",
        description=(
            "Diff Reducer：对 unified diff 做必要性最小化或致败定位，返回 §5.5 报告"
            "（冗余率、最小必要集 / 最小致败集、收敛状态、隔离模式）。会跑测试，可能耗时。"
        ),
        input_schema=obj_schema({
            "repo": {"type": "string", "description": "仓库路径（须为目录）"},
            **_DIFF_PROPS,
            "direction": {"type": "string", "enum": ["necessary", "culprit"],
                          "default": "necessary",
                          "description": "necessary=找冗余；culprit=定位致败改动"},
            "max_test_runs": {"type": "integer", "minimum": 1, "default": 200,
                              "description": "判定次数硬预算（超限返回未收敛的最优解）"},
            "targets": {"type": "array", "items": {"type": "string"},
                        "description": "pytest 目标路径，缺省 tests/"},
        }, ["repo"]),
        fn=reduce_minimize,
        annotations={"readOnlyHint": False, "destructiveHint": False},
    ),
    Tool(
        name="reduce_redundancy",
        description=(
            "Diff Reducer（cheap）：只返回结构性冗余率估计，不跑测试、不建沙箱。"
            "结果是 estimate（confirmed=false）；可证明冗余率请用 reduce_minimize。"
        ),
        input_schema=obj_schema({
            "repo": {"type": "string", "description": "仓库路径"},
            **_DIFF_PROPS,
            "targets": {"type": "array", "items": {"type": "string"},
                        "description": "为接口一致性保留，当前未使用"},
        }, ["repo"]),
        fn=reduce_redundancy,
        annotations={"readOnlyHint": True},
    ),
]
