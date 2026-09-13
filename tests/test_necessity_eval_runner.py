"""eval/runner.py 与 eval/harness.py 的测试 —— 离线、无 LLM。

覆盖五件最容易出错的事：
  1. 8 个 arm 的能力映射（接错能力 = 测的不是系统）
  2. D 组按需裁剪（§7.3）
  3. 续跑不重复落盘（§7.4）
  4. 预检数与 §7.1 矩阵一致
  5. 缺 key 时明确报错且**不产出任何假记录**
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.necessity.eval.agents import AgentUnavailable, Behavior, ScriptedAgent  # noqa: E402
from backend.necessity.eval.aurora_agent import AuroraAgent  # noqa: E402
from backend.necessity.eval.harness import (  # noqa: E402
    ADMONISH_PROMPT,
    arm_capability_config,
    load_arm_hooks,
    prompt_for_arm,
    text_level_violations,
    text_scope_patterns,
)
from backend.necessity.eval.records import (  # noqa: E402
    ARMS,
    TaskSpec,
    read_attempts,
)
from backend.necessity.eval.runner import (  # noqa: E402
    ARMS_TASK_FILTER,          # records.py 实际名是 ARM_TASK_FILTER，runner 里给了别名
    EvalRunner,
    completed_keys,
    gate0_plan,
    is_large_diff,
    plan_matrix,
    run_gate0_mode,
    tasks_for_arm,
)


# ── 测试替身 ─────────────────────────────────────────────────────

@dataclass
class FakeTask:
    spec: TaskSpec
    repo: Path
    text: str = "请完成这个任务。"

    def task_text(self) -> str:
        return self.text


def make_task(tmp_path: Path, task_id: str, *, large: bool = False,
              category: str = "A", constraints=None) -> FakeTask:
    repo = tmp_path / task_id
    repo.mkdir(parents=True, exist_ok=True)
    meta = {"large_diff": True} if large else {"large_diff": False}
    spec = TaskSpec(task_id=task_id, category=category, min_lines_changed=100 if large else 5,
                    constraints=list(constraints or []), meta=meta)
    return FakeTask(spec=spec, repo=repo)


class DeadAgent:
    """不可用的 Agent —— 模拟缺 LLM key。"""
    name = "dead"

    def available(self):
        return False, "未配置 LLM API key，请设置 AURORA_LLM_API_KEY"

    def run(self, **kw):
        raise AssertionError("不可用的 Agent 不该被调用")


# ── 1. arm → 能力映射（8 个 arm 全覆盖）─────────────────────────

@pytest.mark.parametrize("arm,expected", [
    ("A", []),
    ("A_prime", []),                                   # 只有 prompt 叮嘱，无能力
    ("B", ["context"]),
    ("B_prime", ["context"]),                          # 能力在，符号索引被配置关闭
    ("C", ["guard"]),
    ("C_prime", []),                                   # 文本检查在 runner 里，不挂能力
    ("D", ["reduce"]),
    ("E", ["attribution", "context", "guard", "reduce"]),
])
def test_arm_capability_mapping(arm, expected):
    _hooks, caps = load_arm_hooks(arm, workspace=".", session_id="s")
    assert sorted(caps.keys()) == expected


def test_all_eight_arms_are_accounted_for():
    assert set(ARMS) == {"A", "B", "C", "D", "E",
                         "A_prime", "B_prime", "C_prime"}
    for arm in ARMS:
        cfg = arm_capability_config(arm)
        # 总开关必须显式关闭：否则 DEFAULT_ENABLED 会让裸 arm 挂上 context/guard
        assert cfg["enabled"] is False


def test_a_prime_has_no_capabilities_but_gets_admonishment():
    """A′ 是最关键对照（§3.1）——它必须是纯粹「什么都不做只叮嘱」。"""
    _h, caps = load_arm_hooks("A_prime")
    assert caps == {}
    assert ADMONISH_PROMPT.strip() in prompt_for_arm("A_prime", "任务")
    assert "不要重复读取" in prompt_for_arm("A_prime", "任务")
    assert prompt_for_arm("A", "任务") == "任务"


def test_b_prime_disables_symbol_index_only():
    cfg = arm_capability_config("B_prime")["context_paging"]
    assert cfg["min_lines_for_index"] >= 10 ** 8   # 索引永不触发 → 只外置全文
    assert arm_capability_config("B")["context_paging"].get(
        "min_lines_for_index", 50) == 50


# ── 2. 按需裁剪（§3.3）──────────────────────────────────────────

def test_is_large_diff_reads_explicit_meta():
    assert is_large_diff(TaskSpec(task_id="t", meta={"large_diff": True}))
    assert not is_large_diff(TaskSpec(task_id="t", meta={"diff_lines": 10}))
    assert is_large_diff(TaskSpec(task_id="t", meta={"diff_lines": 200}))
    # 没有显式声明时退回 min_lines_changed
    assert is_large_diff(TaskSpec(task_id="t", min_lines_changed=100))


def test_arm_d_skips_small_diff_and_runs_large_diff(tmp_path):
    tasks = [make_task(tmp_path, "big", large=True),
             make_task(tmp_path, "small", large=False)]
    sel = [t.spec.task_id for t in tasks_for_arm("D", tasks)]
    assert sel == ["big"]
    assert [t.spec.task_id for t in tasks_for_arm("A", tasks)] == ["big", "small"]
    assert ARMS_TASK_FILTER == {"D": "large_diff_only"}


# ── 3. 预检估算 ──────────────────────────────────────────────────

def test_preflight_matches_matrix_for_30_tasks(tmp_path):
    """§7.1 的 690 次 = 7 arm × 30 × 3 + D 的 20 × 3 —— 已含 D 的裁剪。

    （裸乘 8 × 30 × 3 = 720；文档给的 690 正是裁剪省下的 30 次，
      所以这里直接核对 690 而不是再减一次。）
    """
    tasks = [make_task(tmp_path, f"t{i:02d}", large=(i < 20)) for i in range(30)]
    plan = plan_matrix(tasks, runs=3)
    assert plan.total == 690
    per_arm = {arm: sum(1 for _t, a, _r in plan.entries if a == arm) for arm in ARMS}
    assert per_arm == {"A": 90, "B": 90, "C": 90, "D": 60, "E": 90,
                       "A_prime": 90, "B_prime": 90, "C_prime": 90}
    assert plan.per_arm["D"] == 20
    assert plan.skipped["D"] == 10
    assert plan.per_arm["A"] == 30
    assert plan.estimate_minutes() == 690 * 8.0


def test_preflight_small_matrix(tmp_path):
    tasks = [make_task(tmp_path, "big", large=True),
             make_task(tmp_path, "small", large=False)]
    plan = plan_matrix(tasks, runs=2)
    # 7 个 arm 各 2 任务 × 2 次 + D 的 1 任务 × 2 次
    assert plan.total == 7 * 2 * 2 + 1 * 2
    assert plan.skipped["D"] == 1
    text = plan.describe()
    assert "合计" in text and "小时" in text


def test_gate0_plan_is_a_only_single_run(tmp_path):
    tasks = [make_task(tmp_path, f"t{i}") for i in range(30)]
    plan = gate0_plan(tasks)
    assert plan.total == 30
    assert {a for _t, a, _r in plan.entries} == {"A"}
    assert {r for _t, _a, r in plan.entries} == {0}


# ── 4. 缺 key：明确报错，不产假数据 ─────────────────────────────

def test_aurora_agent_reports_missing_key_actionably(tmp_path, monkeypatch):
    root = tmp_path / "Aurora"
    root.mkdir()
    (root / "run_server.py").write_text("# stub", encoding="utf-8")
    for k in ("AURORA_LLM_API_KEY", "AURORA_API_KEY",
              "AURORA_AUTH_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    ok, why = AuroraAgent(root).available()
    assert ok is False
    assert "AURORA_LLM_API_KEY" in why          # 告诉用户配哪个变量
    assert "scripted" in why                     # 给出可选择的无 key 路径


def test_aurora_agent_reports_missing_host(tmp_path):
    ok, why = AuroraAgent(tmp_path / "nope").available()
    assert ok is False and "AURORA_ROOT" in why


def test_runner_aborts_with_no_attempts_written_when_agent_unavailable(tmp_path):
    out = tmp_path / "runs.jsonl"
    tasks = [make_task(tmp_path, "t1"), make_task(tmp_path, "t2")]
    runner = EvalRunner(DeadAgent(), out, tasks=tasks, log=lambda *a: None)
    with pytest.raises(AgentUnavailable):
        runner.run(plan_matrix(tasks, runs=2))
    assert not out.exists()                      # 绝不产出伪造记录


def test_aurora_agent_require_available_raises(tmp_path, monkeypatch):
    for k in ("AURORA_LLM_API_KEY", "AURORA_API_KEY",
              "AURORA_AUTH_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(AgentUnavailable):
        AuroraAgent(tmp_path).require_available()


# ── 5. 单次运行的测量 ────────────────────────────────────────────

def test_measure_records_waste_ratio_and_rho(tmp_path):
    task = make_task(tmp_path, "t1", constraints=["只允许修改 src/parser/"])
    behavior = Behavior(
        status="pass", turns=6, tokens=500,
        read_paths=["a.py"], compaction_turn=2, read_after_compaction=["a.py"],
        constraint_violations=0,
    )
    agent = ScriptedAgent({"t1": behavior})
    runner = EvalRunner(agent, tmp_path / "o.jsonl", tasks=[task], log=lambda *a: None)
    att = runner.run_one(task, "A", 0)
    assert att.attempt_id == "t1#A#0"
    assert att.category == "A"
    assert att.status == "pass" and att.turns == 6
    assert att.gates.reads_total == 2 and att.gates.reads_waste == 1
    assert att.gates.waste_ratio == 0.5
    assert att.gates.compaction_count == 1
    assert att.gates.constraint_rho == 1.0        # 无违反 → ρ=1
    assert att.gates.constraint_survivals == 7    # s = T+1
    assert att.gates.tokens_total == 500
    assert att.events and all("kind" in e for e in att.events)
    assert att.ended_at >= att.started_at


def test_measure_rho_uses_first_violation_turn(tmp_path):
    task = make_task(tmp_path, "t1", constraints=["只允许修改 src/parser/"])
    behavior = Behavior(turns=10, read_paths=["a.py"], constraint_violations=1)
    agent = ScriptedAgent({"t1": behavior})
    runner = EvalRunner(agent, tmp_path / "o.jsonl", tasks=[task], log=lambda *a: None)
    att = runner.run_one(task, "C", 0)
    assert att.gates.constraint_violations == 1
    assert att.gates.constraint_survivals == 1
    assert att.gates.constraint_rho == pytest.approx(0.1)


def test_early_stop_over_turn_limit_counts_as_fail(tmp_path):
    """§7.3 早停：超轮次上限即终止，计入 fail（timeout）。"""
    task = make_task(tmp_path, "t1")
    agent = ScriptedAgent({"t1": Behavior(turns=99)})
    runner = EvalRunner(agent, tmp_path / "o.jsonl", tasks=[task],
                        turn_limit=10, log=lambda *a: None)
    att = runner.run_one(task, "A", 0)
    assert att.status == "timeout"
    assert att.turns == 10
    assert att.meta["early_stop"] is True
    assert "轮次上限" in att.error


def test_diff_stats_computed_from_text_when_absent(tmp_path):
    """没给 diff_stats 时从 unified diff 现算（跳过 +++/--- 文件头）。"""
    task = make_task(tmp_path, "t1")
    diff = "--- a/x.py\n+++ b/x.py\n@@\n-old\n+new\n+extra\n"
    agent = ScriptedAgent({"t1": Behavior(diff_text=diff)})
    runner = EvalRunner(agent, tmp_path / "o.jsonl", tasks=[task], log=lambda *a: None)
    att = runner.run_one(task, "A", 0)
    assert att.diff_stats == {"added": 2, "removed": 1, "total": 3}
    assert att.diff_text == diff


def test_no_constraints_leaves_rho_unset(tmp_path):
    """无约束任务 ρ 记 -1（区别于「全违反」的 0，否则报告会误读）。"""
    task = make_task(tmp_path, "t1")
    runner = EvalRunner(ScriptedAgent(), tmp_path / "o.jsonl", tasks=[task],
                        log=lambda *a: None)
    att = runner.run_one(task, "A", 0)
    assert att.gates.constraint_survivals == -1
    assert att.gates.constraint_rho == 0.0


def test_agent_exception_becomes_error_record_not_crash(tmp_path):
    class BoomAgent:
        name = "boom"

        def available(self):
            return True, "ok"

        def run(self, **kw):
            raise RuntimeError("宿主炸了")

    task = make_task(tmp_path, "t1")
    runner = EvalRunner(BoomAgent(), tmp_path / "o.jsonl", tasks=[task], log=lambda *a: None)
    att = runner.run_one(task, "A", 0)
    assert att.status == "error"
    assert "RuntimeError" in att.error


# ── 6. C′ 文本层面检查 ──────────────────────────────────────────

def test_text_scope_extraction_and_violation():
    constraints = ["只允许修改 src/parser/ 下的文件"]
    assert "src/parser/" in text_scope_patterns(constraints)
    assert text_level_violations(constraints, ["src/parser/x.py"]) == []
    assert text_level_violations(constraints, ["src/utils/helper.py"]) == ["src/utils/helper.py"]


def test_c_prime_flags_text_level_violation(tmp_path):
    task = make_task(tmp_path, "t1", category="C",
                     constraints=["只允许修改 src/parser/ 下的文件"])
    behavior = Behavior(written_paths=["src/utils/helper.py"])
    agent = ScriptedAgent({"t1": behavior})
    runner = EvalRunner(agent, tmp_path / "o.jsonl", tasks=[task], log=lambda *a: None)
    att = runner.run_one(task, "C_prime", 0)
    assert att.gates.constraint_violations == 1
    assert att.meta["text_violations"] == ["src/utils/helper.py"]


# ── 7. 续跑 ─────────────────────────────────────────────────────

def test_resume_skips_completed_and_does_not_duplicate(tmp_path):
    out = tmp_path / "runs.jsonl"
    tasks = [make_task(tmp_path, "t1"), make_task(tmp_path, "t2")]
    plan = plan_matrix(tasks, arms=["A", "B"], runs=2)      # 8 次

    r1 = EvalRunner(ScriptedAgent(), out, tasks=tasks, log=lambda *a: None)
    s1 = r1.run(plan)
    assert s1["ran"] == 8 and s1["skipped"] == 0
    assert len(read_attempts(out)) == 8

    # 第二次：全部已完成 → 全跳过，文件不增长
    r2 = EvalRunner(ScriptedAgent(), out, tasks=tasks, log=lambda *a: None)
    s2 = r2.run(plan)
    assert s2["ran"] == 0 and s2["skipped"] == 8
    assert len(read_attempts(out)) == 8
    ids = [a.attempt_id for a in read_attempts(out)]
    assert len(ids) == len(set(ids))                         # 无重复 attempt_id


def test_resume_only_reruns_missing(tmp_path):
    out = tmp_path / "runs.jsonl"
    tasks = [make_task(tmp_path, "t1"), make_task(tmp_path, "t2")]
    plan = plan_matrix(tasks, arms=["A"], runs=2)            # 4 次

    EvalRunner(ScriptedAgent(), out, tasks=tasks, log=lambda *a: None).run(plan)
    # 手工删掉最后一行 → 模拟「跑到一半崩了」
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    out.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    assert len(read_attempts(out)) == 3

    s = EvalRunner(ScriptedAgent(), out, tasks=tasks, log=lambda *a: None).run(plan)
    assert s["skipped"] == 3 and s["ran"] == 1
    assert len(read_attempts(out)) == 4


def test_completed_keys_reflects_jsonl(tmp_path):
    out = tmp_path / "runs.jsonl"
    tasks = [make_task(tmp_path, "t1")]
    EvalRunner(ScriptedAgent(), out, tasks=tasks, log=lambda *a: None).run(
        plan_matrix(tasks, arms=["A"], runs=1))
    assert completed_keys(read_attempts(out)) == {("t1", "A", 0)}


def test_retry_errors_reruns_only_error_status(tmp_path):
    out = tmp_path / "runs.jsonl"
    task = make_task(tmp_path, "t1")
    plan = plan_matrix([task], arms=["A"], runs=1)
    # 先落一条 error
    runner = EvalRunner(ScriptedAgent(), out, tasks=[task], log=lambda *a: None)
    att = runner.run_one(task, "A", 0)
    att.status = "error"
    from backend.necessity.eval.records import write_attempts
    write_attempts(out, [att])

    r = EvalRunner(ScriptedAgent(), out, tasks=[task],
                   retry_errors=True, log=lambda *a: None)
    assert r.run(plan)["ran"] == 1
    assert len(read_attempts(out)) == 2


# ── 8. 端到端（假 Agent）────────────────────────────────────────

def test_end_to_end_two_tasks_two_arms_two_runs(tmp_path):
    """2 任务 × 2 arm × 2 次 → JSONL → report 出表。"""
    out = tmp_path / "runs.jsonl"
    tasks = [make_task(tmp_path, "t1"), make_task(tmp_path, "t2")]
    behaviors = {
        # E 组「更好」：读得少、token 少
        "t1": Behavior(turns=5, tokens=400, read_paths=["a.py"]),
        "t2": Behavior(turns=5, tokens=500, read_paths=["b.py"]),
    }
    agent = ScriptedAgent(behaviors)
    plan = plan_matrix(tasks, arms=["A", "E"], runs=2)
    assert plan.total == 8

    summary = EvalRunner(agent, out, tasks=tasks, log=lambda *a: None).run(plan)
    assert summary["ran"] == 8

    attempts = read_attempts(out)
    assert len(attempts) == 8
    assert sorted({a.arm for a in attempts}) == ["A", "E"]
    assert sorted({a.task_id for a in attempts}) == ["t1", "t2"]

    from backend.necessity.eval.report import build_report, render_table, summaries

    table = render_table(summaries(attempts))
    assert "A" in table and "E" in table and "完成率" in table
    rep = build_report(attempts)
    assert rep["n_attempts"] == 8
    assert "不外推" in rep["disclaimer"]


def test_gate0_mode_reports_waste_ratio(tmp_path):
    out = tmp_path / "runs.jsonl"
    tasks = [make_task(tmp_path, "t1"), make_task(tmp_path, "t2")]
    # 每个任务：读 a.py 两次（中间一次压缩）→ 50% 浪费
    behavior = Behavior(read_paths=["a.py"], compaction_turn=2,
                        read_after_compaction=["a.py"])
    agent = ScriptedAgent({"t1": behavior, "t2": behavior})
    summary = run_gate0_mode(tasks, agent, out, log=lambda *a: None)
    assert summary["gate0"]["total_reads"] == 4
    assert summary["gate0"]["waste_reads"] == 2
    assert summary["gate0"]["waste_ratio"] == 0.5
    assert summary["verdict"] == "PASS"


def test_gate0_dry_run_writes_nothing(tmp_path):
    out = tmp_path / "runs.jsonl"
    tasks = [make_task(tmp_path, "t1")]
    summary = run_gate0_mode(tasks, ScriptedAgent(), out,
                             dry_run=True, log=lambda *a: None)
    assert summary["dry_run"] is True
    assert not out.exists()


def test_jsonl_is_incrementally_written(tmp_path):
    """§7.4：必须增量落盘，崩溃时已完成的工作不能丢。

    这里验证「每完成一次立刻可见」：跑完第一次后文件里就有 1 行。
    """
    out = tmp_path / "runs.jsonl"
    tasks = [make_task(tmp_path, "t1"), make_task(tmp_path, "t2")]
    seen = []

    class WatchingAgent(ScriptedAgent):
        def run(self, **kw):
            seen.append(len(read_attempts(out)))
            return super().run(**kw)

    plan = plan_matrix(tasks, arms=["A"], runs=1)
    EvalRunner(WatchingAgent(), out, tasks=tasks, log=lambda *a: None).run(plan)
    assert seen[0] == 0          # 第一次运行前文件为空
    assert seen[1] == 1          # 第二次运行前已有 1 条


def test_attempt_dict_has_all_contract_fields(tmp_path):
    """§7.4 的字段一个不能少。"""
    out = tmp_path / "runs.jsonl"
    task = make_task(tmp_path, "t1")
    EvalRunner(ScriptedAgent(), out, tasks=[task], log=lambda *a: None).run(
        plan_matrix([task], arms=["A"], runs=1))
    raw = json.loads(out.read_text(encoding="utf-8").strip())
    for key in ("attempt_id", "task_id", "arm", "run_index", "started_at",
                "ended_at", "turns", "tokens", "events", "diff_text",
                "diff_stats", "status", "gates"):
        assert key in raw, f"缺字段 {key}"
    assert "waste_ratio" in raw["gates"]
