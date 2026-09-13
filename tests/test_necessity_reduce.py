"""Diff Reducer 测试 —— 一致性组 / 双向搜索 / 隔离 / 预算。

重点验证文档记录过的两个坑：
  §3.2  一致性组不能用两两枚举（O(n²) 秒级检查）
  §1.2  两个方向**相反**，写反会得到完全错误的结论
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.necessity.index.symbols import Symbol, make_symbol_id  # noqa: E402
from backend.necessity.reduce.report import (  # noqa: E402
    build_culprit_report,
    build_report,
    format_text,
)
from backend.necessity.reduce.sandbox import Sandbox, user_tree_is_clean  # noqa: E402
from backend.necessity.reduce.search import (  # noqa: E402
    Budget,
    Direction,
    ERROR,
    FAIL,
    PASS,
    minimize,
    minimize_culprit,
    minimize_necessary,
    redundancy_ratio,
)
from backend.necessity.reduce.split import (  # noqa: E402
    Hunk,
    all_hunks,
    apply_text_patch,
    build_coherence_groups,
    parse_unified_diff,
)

WS = "D:/proj"


def mk(i, file="a.py", added=5, removed=2, new_start=None):
    return Hunk(id=f"h{i}", file=file, old_start=i + 1, new_start=new_start or i + 1,
                old_count=1, new_count=1, added=added, removed=removed)


# ══ 切分 ═════════════════════════════════════════════════════════

def test_parse_diff_multiple_files_and_hunks():
    diff = (
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
        "@@ -1,1 +1,1 @@\n-x\n+y\n@@ -10,1 +10,2 @@\n-p\n+q\n+r\n"
        "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n"
        "@@ -5,1 +5,1 @@\n-a\n+b\n"
    )
    files = parse_unified_diff(diff)
    assert len(files) == 2
    assert len(all_hunks(files)) == 3
    assert files[0].path == "a.py"
    assert files[0].hunks[1].added == 2


def test_parse_new_file_marked():
    diff = ("diff --git a/n.py b/n.py\n--- /dev/null\n+++ b/n.py\n"
            "@@ -0,0 +1,2 @@\n+import os\n+x = 1\n")
    f = parse_unified_diff(diff)[0]
    assert f.is_new is True
    assert f.hunks[0].is_new_file is True


# ══ 一致性组：文档 §3.1 的关键场景 ═══════════════════════════════

def test_coherence_joins_signature_change_with_call_site():
    """改签名 + 改调用点必须同组 —— 文档 §3.1 的核心场景。

    两者触及**不同符号**（parse vs caller），阶段 1 合并不了，
    必须靠阶段 2 的调用边传播。若这里失败，最小化必然误判。
    """
    diff = (
        "diff --git a/src/parser.py b/src/parser.py\n--- a/src/parser.py\n+++ b/src/parser.py\n"
        "@@ -10,1 +10,1 @@\n-def parse(text):\n+def parse(text, strict=False):\n"
        "diff --git a/src/caller.py b/src/caller.py\n--- a/src/caller.py\n+++ b/src/caller.py\n"
        "@@ -5,1 +5,1 @@\n-parse(t)\n+parse(t, strict=True)\n"
    )
    hs = all_hunks(parse_unified_diff(diff))
    syms = {
        "src/parser.py": [Symbol(id=make_symbol_id("src/parser.py", "parse", WS), workspace=WS,
                                 file="src/parser.py", qualified_name="parse", name="parse",
                                 kind="function", start_line=9, start_col=0, end_line=20, end_col=0)],
        "src/caller.py": [Symbol(id=make_symbol_id("src/caller.py", "caller", WS), workspace=WS,
                                 file="src/caller.py", qualified_name="caller", name="caller",
                                 kind="function", start_line=4, start_col=0, end_line=8, end_col=0)],
    }
    parse_id = make_symbol_id("src/parser.py", "parse", WS)
    caller_id = make_symbol_id("src/caller.py", "caller", WS)

    def callers_of(sid):
        return [{"src_id": caller_id}] if sid == parse_id else []

    g = build_coherence_groups(hs, symbols_by_file=syms, callers_of=callers_of)
    assert len(g.groups) == 1, f"改签名与改调用点未合并: {[[h.id for h in x] for x in g.groups]}"
    assert g.level == "L1"


def test_coherence_does_not_merge_unrelated():
    """无调用关系的两个 hunk 不得被合并（组偏大只是精度差，但无谓偏大也是错）。"""
    diff = (
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -2,1 +2,1 @@\n-x\n+y\n"
        "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -50,1 +50,1 @@\n-p\n+q\n"
    )
    hs = all_hunks(parse_unified_diff(diff))
    syms = {
        "a.py": [Symbol(id="a::f", workspace=WS, file="a.py", qualified_name="f", name="f",
                        kind="function", start_line=0, start_col=0, end_line=5, end_col=0)],
        "b.py": [Symbol(id="b::g", workspace=WS, file="b.py", qualified_name="g", name="g",
                        kind="function", start_line=48, start_col=0, end_line=55, end_col=0)],
    }
    g = build_coherence_groups(hs, symbols_by_file=syms, callers_of=lambda s: [])
    assert len(g.groups) == 2


def test_coherence_degrades_to_file_level_without_symbols():
    """无符号表 -> L2 文件级合并。**宁可组偏大（保守）也不要组偏小（误判）**。"""
    diff = (
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,1 @@\n-x\n+y\n"
        "@@ -50,1 +50,1 @@\n-p\n+q\n"
    )
    hs = all_hunks(parse_unified_diff(diff))
    g = build_coherence_groups(hs, symbols_by_file=None)
    assert g.level == "L2"
    assert len(g.groups) == 1, "同文件应保守合并"
    assert g.reason


def test_new_file_hunks_stay_together():
    """新文件的多个 hunk 视为一个组（§7 边界 10）。"""
    diff = ("diff --git a/n.py b/n.py\n--- /dev/null\n+++ b/n.py\n"
            "@@ -0,0 +1,1 @@\n+import os\n@@ -0,0 +20,1 @@\n+def f():\n")
    hs = all_hunks(parse_unified_diff(diff))
    g = build_coherence_groups(hs, symbols_by_file={"n.py": []})
    assert len(g.groups) == 1


# ══ 双向搜索 ════════════════════════════════════════════════════

def test_minimize_necessary_finds_redundant_hunk():
    hs = [mk(0), mk(1), mk(2), mk(3)]

    def test(subset):
        return PASS if "h2" not in {h.id for h in subset} else FAIL

    r = minimize_necessary(hs, test)
    ids = {h.id for h in r.best}
    assert "h2" not in ids, f"冗余 hunk 未被剔除: {ids}"
    assert r.converged is True


def test_minimize_culprit_finds_breaking_hunk():
    """致败定位：最小**失败**集 —— 与必要性方向相反。"""
    hs = [mk(0), mk(1), mk(2), mk(3)]

    def test(subset):
        return FAIL if "h3" in {h.id for h in subset} else PASS

    r = minimize_culprit(hs, test)
    assert {h.id for h in r.best} == {"h3"}, f"致败集错误: {[h.id for h in r.best]}"
    assert r.converged is True


def test_two_directions_do_not_get_swapped():
    """同一 fixture 走两个方向，必须得到**相反**的答案。

    这是防「把谓词写反」的护栏 —— 写反会得到完全错误的结论。
    """
    hs = [mk(0), mk(1), mk(2)]

    def test(subset):
        ids = {h.id for h in subset}
        if "h1" in ids:          # h1 会破坏测试
            return FAIL
        return PASS

    nec = minimize_necessary(hs, test)
    culprit = minimize_culprit(hs, test)
    assert "h1" not in {h.id for h in nec.best}, "必要性不应保留致败改动"
    assert {h.id for h in culprit.best} == {"h1"}, "致败应精确定位到 h1"


def test_culprit_refuses_when_baseline_fails():
    """§7 边界 5：T(∅)=fail 时无法判定，必须拒绝而不是猜。"""
    hs = [mk(0), mk(1)]
    r = minimize_culprit(hs, lambda s: FAIL)
    assert r.converged is False
    assert "无法判定" in r.stopped_reason


def test_culprit_refuses_when_baseline_errors():
    hs = [mk(0)]
    r = minimize_culprit(hs, lambda s: ERROR)
    assert r.converged is False
    assert "不可判定" in r.stopped_reason


def test_budget_exhaustion_returns_best_so_far():
    """§6.3 / §7 总原则：超预算返回当前最优 + 未收敛，**不抛异常、不给错结论**。"""
    hs = [mk(i) for i in range(8)]
    b = Budget(max_test_runs=3)

    def test(subset):
        return PASS if len(subset) <= 2 else FAIL

    r = minimize_necessary(hs, test, budget=b)
    assert r.test_runs <= 4, f"超出预算: {r.test_runs}"
    assert r.converged is False
    assert r.best, "未收敛也必须返回当前最优"


def test_test_exception_becomes_error_and_is_conservative():
    """§7 边界 8：测试抛异常记为 error，保守视为不可移除。"""
    hs = [mk(0), mk(1)]

    def boom(subset):
        raise RuntimeError("test harness crashed")

    r = minimize_necessary(hs, boom)
    # 全程 error -> 无法缩小 -> 保留全部（保守）
    assert len(r.best) == 2


def test_cache_avoids_repeat_runs():
    hs = [mk(i) for i in range(6)]
    seen = []

    def test(subset):
        seen.append(_key_of(subset))
        return PASS if len(subset) <= 1 else FAIL

    r = minimize_necessary(hs, test)
    assert r.cache_hits >= 0   # 缓存命中可观测
    assert len(seen) == len(set(seen)), "同一子集被重复测试"


def _key_of(subset):
    return tuple(sorted(h.id for h in subset))


# ══ 冗余率（EVAL.md §2.4 的定义）═════════════════════════════════

def test_redundancy_ratio_uses_lines_not_hunk_count():
    """必须是**行数**比，不是 hunk 数比。

    文档 §8.5 与 EVAL.md §2.4 的定义都是「冗余改动行数 / 总改动行数」。
    用 hunk 计数会让 50 行的 hunk 和 1 行的 hunk 权重相同，指标失真。
    """
    all_hs = [mk(0, added=100, removed=0), mk(1, added=1, removed=0)]
    necessary = [all_hs[0]]           # 保留大 hunk，冗余的是小 hunk
    ratio = redundancy_ratio(all_hs, necessary)
    assert abs(ratio - 1 / 101) < 1e-6, f"应按行数计算，实际 {ratio}"


def test_redundancy_ratio_zero_when_all_necessary():
    hs = [mk(0), mk(1)]
    assert redundancy_ratio(hs, hs) == 0.0


def test_redundancy_ratio_handles_empty():
    assert redundancy_ratio([], []) == 0.0


# ══ patch 应用（正/反向）════════════════════════════════════════

def test_apply_and_reverse_patch_roundtrip():
    orig = "def parse(text):\n    return text\n\nx = 1\n"
    diff = ("diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n"
            "@@ -1,2 +1,2 @@\n-def parse(text):\n-    return text\n"
            "+def parse(text, strict=False):\n+    return text\n")
    hs = all_hunks(parse_unified_diff(diff))

    fwd = apply_text_patch(orig, hs, reverse=False)
    assert "strict=False" in fwd

    back = apply_text_patch(orig, hs, reverse=True)
    assert back.splitlines()[0] == "def parse(text):", f"反向还原失败: {back!r}"


def test_reverse_output_has_no_diff_prefixes():
    """反向结果必须是纯源码 —— 带 +/- 前缀会污染文件。"""
    diff = ("diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n"
            "@@ -1,1 +1,1 @@\n-old_line\n+new_line\n")
    hs = all_hunks(parse_unified_diff(diff))
    out = apply_text_patch("new_line\n", hs, reverse=True)
    assert not out.lstrip().startswith(("+", "-")), f"含 diff 前缀: {out!r}"
    assert "old_line" in out


# ══ 隔离（真实 git 仓库）════════════════════════════════════════

@pytest.fixture
def tiny_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "tests" / "test_mod.py").write_text(
        "from mod import VALUE\n\n\ndef test_v():\n    assert VALUE == 1\n", encoding="utf-8"
    )
    for args in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"],
                 ["git", "config", "user.name", "t"], ["git", "add", "-A"],
                 ["git", "commit", "-qm", "init"]):
        subprocess.run(args, cwd=repo, capture_output=True)
    return repo


def test_sandbox_uses_worktree_and_leaves_user_tree_clean(tiny_repo):
    """§5.4 / INTEGRATION.md §7.2：必须 worktree 隔离，且用户工作区必须不变。"""
    clean_before, _ = user_tree_is_clean(str(tiny_repo))
    assert clean_before

    with Sandbox(str(tiny_repo)) as sb:
        info = sb.create()
        assert info.mode == "git-worktree", f"应使用 worktree，实际 {info.mode}"
        assert info.degraded is False

        sb.apply("mod.py", "VALUE = 999\n")
        res, _ = sb.run_tests(["tests/"], timeout=180)
        assert res == "fail", "改坏后应 fail"

        sb.revert_all()
        res2, _ = sb.run_tests(["tests/"], timeout=180)
        assert res2 == "pass", "还原后应 pass"

    clean_after, detail = user_tree_is_clean(str(tiny_repo))
    assert clean_after, f"用户工作区被污染: {detail}"
    assert (tiny_repo / "mod.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert not Path(info.path).exists(), "worktree 未清理"


def test_sandbox_degrades_to_copy_for_non_git(tmp_path):
    """§7 边界 1：非 git 仓库降级为目录复制，且**必须标注**。"""
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "a.py").write_text("x = 1\n", encoding="utf-8")

    with Sandbox(str(plain)) as sb:
        info = sb.create()
        assert info.mode == "copy"
        assert info.degraded is True
        assert info.note, "降级必须给出说明"


# ══ 报告 ════════════════════════════════════════════════════════

def test_report_shape_matches_doc_5_5():
    hs = [mk(0), mk(1), mk(2)]

    def test(subset):
        return PASS if "h2" not in {h.id for h in subset} else FAIL

    r = minimize_necessary(hs, test)
    rep = build_report(hs, r, task_id="t1", baseline_test="pass", final_test="pass")
    d = rep.to_dict()

    for key in ("task_id", "original_diff", "baseline_test", "final_test",
                "necessary", "metrics"):
        assert key in d, f"缺字段 {key}"
    assert "redundant" in d
    assert d["original_diff"]["hunks"] == 3
    assert "converged" in d["metrics"], "未收敛状态必须暴露给调用方"


def test_report_flags_unconverged_loudly():
    hs = [mk(i) for i in range(8)]
    r = minimize_necessary(hs, lambda s: PASS if len(s) <= 1 else FAIL,
                           budget=Budget(max_test_runs=2))
    rep = build_report(hs, r)
    assert rep.metrics["converged"] is False
    assert any("未收敛" in n for n in rep.notes), "未收敛必须在 notes 里说明"


def test_report_warns_when_everything_redundant():
    """§7 边界 4：全部改动都冗余 -> 必须警告 Agent 的改动对目标无贡献。"""
    hs = [mk(0), mk(1)]

    def test(subset):
        return PASS   # 删光了也 pass

    r = minimize_necessary(hs, test)
    rep = build_report(hs, r)
    assert any("无贡献" in n for n in rep.notes)


def test_culprit_report_shape():
    hs = [mk(0), mk(1)]

    def test(subset):
        return FAIL if "h1" in {h.id for h in subset} else PASS

    r = minimize_culprit(hs, test)
    rep = build_culprit_report(hs, r, task_id="t2")
    d = rep.to_dict()
    assert "culprit" in d
    assert "redundant" not in d, "失败场景输出 culprit 而非 redundant（§5.5）"
    assert d["culprit"]["hunks"] == ["h1"]


def test_format_text_is_readable():
    hs = [mk(0), mk(1)]

    def test(subset):
        return PASS if "h1" not in {h.id for h in subset} else FAIL

    r = minimize_necessary(hs, test)
    txt = format_text(build_report(hs, r, task_id="demo"))
    assert "冗余率" in txt
    assert "收敛" in txt
