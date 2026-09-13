"""CLI 离线命令面测试（reduce / attribution / eval + 退出码契约）。

全部离线（注入 runner、临时 SQLite，无 pyright / 无 LLM）。关键断言：
未收敛必须在**退出码**上可见（DIFF_REDUCER.md §7）；Guard 不在命令树
（INTEGRATION.md §1.3：Guard 必须拦截宿主循环，CLI 做不到）。
"""
import contextlib
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.necessity.cli import main as cli_main  # noqa: E402


def _run(argv):
    """跑 CLI 并捕获输出；main() 恒返回 int，故可直接断言退出码。"""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli_main.main(argv)
    return rc, out.getvalue(), err.getvalue()

# 同一文件两个 hunk（无符号表时 L2 会合并为组，仅用于必要方向）
TWO_HUNK = ("diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n"
            "@@ -1,2 +1,3 @@\n import os\n+NECESSARY = 1\n x = 1\n"
            "@@ -10,2 +11,3 @@\n y = 2\n+REDUNDANT = 1\n z = 3\n")
# 两个文件各一个 hunk（分组为两个组，用于致败定位/预算耗尽）
TWO_FILE = ("diff --git a/keep.py b/keep.py\n--- a/keep.py\n+++ b/keep.py\n"
            "@@ -1,1 +1,2 @@\n k = 0\n+KEEP = 1\n"
            "diff --git a/bad.py b/bad.py\n--- a/bad.py\n+++ b/bad.py\n"
            "@@ -1,1 +1,2 @@\n b = 0\n+BAD = 1\n")


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    (r / "m.py").write_text("import os\n", encoding="utf-8")
    return r


def _write_diff(tmp_path, text, name="d.diff"):
    (tmp_path / name).write_text(text, encoding="utf-8")
    return tmp_path / name


# ── 命令树：Guard 必须缺席 ───────────────────────────────────────

def test_guard_has_no_cli_entry():
    """Guard 必须拦截宿主循环的工具调用，CLI 做不到 → 不得有子命令。"""
    p = cli_main.build_parser()
    sub = next(a for a in p._actions if a.dest == "group")
    assert "guard" not in sub.choices
    assert {"index", "reduce", "attribution", "eval"} <= set(sub.choices)
    for g in ("reduce", "attribution", "eval"):
        gsub = next(a for a in sub.choices[g]._actions if a.dest == "cmd")
        assert "guard" not in gsub.choices


def test_usage_and_help_exit_codes():
    """未知子命令 / 无参数 = 2；--help = 0（main 恒返回 int，可直接断言）。"""
    rc, _o, err = _run(["frobnicate"])
    assert rc == 2 and "frobnicate" in err
    assert _run(["reduce", "nope"])[0] == 2
    assert _run([])[0] == 2
    assert _run(["--help"])[0] == 0


# ── index 未被破坏 ──────────────────────────────────────────────

def test_index_stats_still_works(tmp_path):
    rc, out, _e = _run(["index", "stats", "--db", str(tmp_path / "i.db")])
    assert rc == 0 and "{" in out


def test_index_stats_storage_error(tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_text("not sqlite", encoding="utf-8")
    rc, _o, err = _run(["index", "stats", "--db", str(bad)])
    assert rc == 4 and "数据库错误" in err


# ── reduce analyze ──────────────────────────────────────────────

def test_reduce_analyze_converges(tmp_path, repo, monkeypatch):
    monkeypatch.setattr("cli.reduce_cmd.INJECTED_RUNNER", lambda s: "pass")
    d = _write_diff(tmp_path, TWO_HUNK)
    rc, out, _e = _run(["reduce", "analyze", str(d), "--repo", str(repo)])
    assert rc == 0, out
    assert "冗余率" in out and "收敛: 是" in out


def test_reduce_analyze_json_documented_keys(tmp_path, repo, monkeypatch):
    monkeypatch.setattr("cli.reduce_cmd.INJECTED_RUNNER", lambda s: "pass")
    d = _write_diff(tmp_path, TWO_HUNK)
    rc, out, _e = _run(["reduce", "analyze", str(d), "--repo", str(repo), "--json"])
    assert rc == 0
    data = json.loads(out)
    for k in ("original_diff", "necessary", "metrics"):
        assert k in data
    for k in ("converged", "test_runs", "redundancy_ratio"):
        assert k in data["metrics"]
    assert data["metrics"]["converged"] is True


def test_reduce_analyze_reads_stdin(tmp_path, repo, monkeypatch):
    monkeypatch.setattr("cli.reduce_cmd.INJECTED_RUNNER", lambda s: "pass")
    monkeypatch.setattr(sys, "stdin", io.StringIO(TWO_HUNK))
    rc, out, _e = _run(["reduce", "analyze", "-", "--repo", str(repo)])
    assert rc == 0 and "原始改动" in out


def test_reduce_analyze_non_convergence_is_nonzero(tmp_path, repo, monkeypatch):
    """★ 预算小到不可能收敛 → 退出码必须非 0（§7 总原则：非收敛必须可见）。"""
    monkeypatch.setattr("cli.reduce_cmd.INJECTED_RUNNER", lambda s: "fail")
    parts = [
        f"diff --git a/f{i}.py b/f{i}.py\n--- a/f{i}.py\n+++ b/f{i}.py\n"
        f"@@ -1,1 +1,2 @@\n x = {i}\n+Y = {i}\n"
        for i in range(8)  # 跨文件才能形成多个组，组大小恒 1 会直接收敛
    ]
    d = _write_diff(tmp_path, "".join(parts), "big.diff")
    rc, out, _e = _run(["reduce", "analyze", str(d), "--repo", str(repo),
                        "--max-test-runs", "2", "--json"])
    assert rc == 1, f"未收敛必须非 0，实际 {rc}\n{out}"
    data = json.loads(out)
    assert data["metrics"]["converged"] is False
    assert any("未收敛" in n for n in data.get("notes", []))


def test_reduce_analyze_culprit_direction(tmp_path, repo, monkeypatch):
    """致败方向：bad.py 使测试 fail，应精确指向 bad.py。

    subset 的元素是**一致性组**（Hunk 列表），不是单个 Hunk —— runner 契约。
    """
    def runner(subset):
        files = {h.file for grp in subset
                 for h in (grp if isinstance(grp, (list, tuple)) else [grp])}
        return "fail" if "bad.py" in files else "pass"

    monkeypatch.setattr("cli.reduce_cmd.INJECTED_RUNNER", runner)
    d = _write_diff(tmp_path, TWO_FILE)
    rc, out, _e = _run(["reduce", "analyze", str(d), "--repo", str(repo),
                        "--culprit-only", "--json"])
    assert rc == 0, out
    data = json.loads(out)
    assert data["culprit"]["locations"] == ["bad.py:1"]


def test_reduce_analyze_usage_errors(tmp_path, repo):
    """三种参数错误（方向冲突 / diff 不存在 / 没有 hunk）都应是 2。"""
    d = _write_diff(tmp_path, TWO_HUNK)
    rc, _o, err = _run(["reduce", "analyze", str(d), "--repo", str(repo),
                        "--minimize-necessary-only", "--culprit-only"])
    assert rc == 2 and "冲突" in err
    rc, _o, err = _run(["reduce", "analyze", str(tmp_path / "no.diff"),
                        "--repo", str(repo)])
    assert rc == 2 and "读不到" in err
    empty = _write_diff(tmp_path, "", "e.diff")
    rc, _o, err = _run(["reduce", "analyze", str(empty), "--repo", str(repo)])
    assert rc == 2 and "hunk" in err


# ── attribution report ──────────────────────────────────────────

def _trace_db(path, session):
    """写一个可归因的失败会话：压缩后重读 a.py → context_missing（§4.2）。"""
    from backend.necessity.cli.attribution_cmd import TraceDB
    from backend.necessity.index.trace import TraceStore

    t = TraceStore(db=TraceDB(str(path)), flush_every=1)
    t.record(session, "file_read", turn=1, path="a.py", lines=20, hash="h1")
    t.record(session, "compaction", turn=2, token_before=9000, token_after=3000)
    t.record(session, "file_read", turn=3, path="a.py", lines=20, hash="h1")
    t.flush()


def test_attribution_report_classifies_with_evidence(tmp_path):
    db = tmp_path / "t.db"
    _trace_db(db, "s1")
    rc, out, _e = _run(["attribution", "report", str(db), "--session", "s1"])
    assert rc == 0, out
    assert "context_missing" in out and "证据" in out
    assert "compaction_reread" in out  # 具体证据，而非只有类别名


def test_attribution_report_json_has_evidence(tmp_path):
    db = tmp_path / "t.db"
    _trace_db(db, "s1")
    rc, out, _e = _run(["attribution", "report", str(db), "--session", "s1", "--json"])
    assert rc == 0
    attr = json.loads(out)["attribution"]
    assert attr["primary"] == "context_missing"
    assert attr["evidence"], "非 unknown 必须带证据（ATTRIBUTION.md §3.3）"
    assert all("signal" in e and "detail" in e for e in attr["evidence"])


def test_attribution_report_unknown_when_no_events(tmp_path):
    db = tmp_path / "t.db"
    _trace_db(db, "other")  # 事件不属于目标会话
    rc, out, _e = _run(["attribution", "report", str(db), "--session", "missing"])
    assert rc == 1 and "unknown" in out  # 未得到结论 → 1


def test_attribution_report_missing_db_is_storage_error(tmp_path):
    rc, _o, err = _run(["attribution", "report", str(tmp_path / "no.db"),
                        "--session", "s"])
    assert rc == 4 and "不存在" in err  # 缺库 → 4，不是 3


def test_attribution_gate6_flags_insufficient_instrumentation(tmp_path):
    """只采了两类事件 → missing_event_kinds 非空 → 判为埋点不足。"""
    db = tmp_path / "t.db"
    _trace_db(db, "s1")
    rc, out, _e = _run(["attribution", "report", str(db), "--session", "s1",
                        "--gate6", "--json"])
    assert rc == 0
    g6 = json.loads(out)["gate6"]
    assert g6["insufficient"] is True and g6["missing_event_kinds"]
    assert "file_read" not in g6["missing_event_kinds"]


# ── eval gate0 ──────────────────────────────────────────────────

def test_eval_gate0_empty_db_reports_missing_instrumentation(tmp_path):
    """无轨迹 → gate0 契约退出码是 2（埋点未生效），不是 1。"""
    from backend.necessity.cli.attribution_cmd import TraceDB

    db = tmp_path / "empty.db"
    TraceDB(str(db)).append_agent_events([])  # 建表不写数据
    rc, out, err = _run(["eval", "gate0", "--db", str(db)])
    assert rc == 2, f"空库应返回 2，实际 {rc}\n{out}{err}"
    assert "没有采集到" in out or "埋点未生效" in out


def test_eval_gate0_with_data(tmp_path):
    db = tmp_path / "t.db"
    _trace_db(db, "s1")
    rc, out, _e = _run(["eval", "gate0", "--db", str(db)])
    assert rc in (0, 1), out  # 有数据 → PASS/MARGINAL，绝不 2
    assert "Gate 0 判定" in out


# ── eval summary ────────────────────────────────────────────────

def _jsonl(path):
    from backend.necessity.eval.records import Attempt, GateMetrics, write_attempts

    def mk(aid, arm, status, turns, dur):
        a = Attempt(attempt_id=aid, task_id=aid.split("#")[0], arm=arm,
                    run_index=0, status=status, turns=turns, gates=GateMetrics())
        a.started_at, a.ended_at = 0.0, dur
        return a

    write_attempts(path, [
        mk("t1#A#0", "A", "pass", 10, 20.0),
        mk("t1#A#1", "A", "fail", 15, 30.0),
        mk("t1#B#0", "B", "pass", 8, 12.0),
        mk("t2#B#0", "B", "error", 3, 5.0),
    ])


def test_eval_summary_table_has_per_arm_rows(tmp_path):
    f = tmp_path / "a.jsonl"
    _jsonl(f)
    rc, out, _e = _run(["eval", "summary", str(f)])
    assert rc == 0, out
    assert "记录数 4" in out
    rows = [ln for ln in out.splitlines() if ln.startswith(("A ", "B "))]
    assert len(rows) == 2, out
    # 走 eval/report.py 的口径（每个 arm 的完成率），而非本地原始计数
    assert "完成率" in out
    assert "口径说明" in out  # §6.3 的限定语随报告一起输出


def test_eval_summary_json(tmp_path):
    f = tmp_path / "a.jsonl"
    _jsonl(f)
    rc, out, _e = _run(["eval", "summary", str(f), "--json"])
    assert rc == 0
    data = json.loads(out)
    assert data["n_attempts"] == 4
    # report.build_report 的 arms 是 {arm: {...}} 映射
    assert {"A", "B"} <= set(data["arms"])
    assert "completion_rate" in data["arms"]["A"]
    assert "comparisons" in data


def test_eval_summary_file_errors(tmp_path):
    rc, _o, err = _run(["eval", "summary", str(tmp_path / "no.jsonl")])
    assert rc == 4 and "不存在" in err  # 文件不存在 → 存储错误
    f = tmp_path / "empty.jsonl"
    f.write_text("", encoding="utf-8")
    rc, _o, err = _run(["eval", "summary", str(f)])
    assert rc == 1 and "没有可用" in err  # 空文件 → 结果不完整
