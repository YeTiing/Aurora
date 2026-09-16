"""A6 多方案竞争 —— 锁死「可解释裁决」与「不选违规最少者」两条纪律。

规格：`Aurora_六项能力设计规范.md` §8。

## 这套东西最容易被做错的两处

1. **用加权总分**（规范 §8.4 明确反对）：
   「权重无法客观标定，会『用一个编出来的数字掩盖真实权衡』」。
   本文件锁死「按序比较」与「每步可解释」。

2. **全部候选违规时选「违反最少」的**（§8.5① 明确禁止）：
   那会让用户在接受一个违规方案时**毫不知情** —— 而违反的正是
   A2 挖出的隐式约定。

## 它可能证明自己没有价值（§8.8 门禁）

规范明确写了「胜者与随机无显著差异 → **砍掉这个功能**」——
设计要允许自己被否定。本文件不试图证明它有效，只证明它**可解释**。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.necessity.compete import (  # noqa: E402
    MIN_DIVERSITY,
    STRATEGIES,
    Arena,
    Candidate,
    CandidateMetrics,
    Verdict,
    build_specs,
    diversity,
    judge,
    rank_key,
    verify_isolation,
    worth_competing,
)


def cand(cid, strategy="minimal", *, tests=True, sec=0, crit=0, imp=1,
         red=0.0, lines=10, viol=None, stale=False, gen="agent-a",
         diff=None, error=""):
    return Candidate(
        id=cid, strategy=strategy, generator=gen, error=error,
        diff=diff if diff is not None else f"--- a/x\n+++ b/x\n@@ {cid}\n+x\n",
        metrics=CandidateMetrics(
            tests_pass=tests, tests_total=5, security_findings=sec,
            security_critical=crit, impact_files=imp, redundancy_ratio=red,
            diff_lines=lines, violated_constraints=list(viol or []),
            impact_stale=stale))


# ── 硬门禁（§8.4）──────────────────────────────────────────────

def test_failed_tests_are_eliminated():
    """测试不通过 -> 淘汰（硬门禁①）。"""
    v = judge([cand("bad", tests=False), cand("good")])
    assert v.winner == "good"
    assert "测试未通过" in v.rejected_reasons["bad"]


def test_critical_security_finding_is_eliminated():
    """安全扫描有 critical -> 淘汰（硬门禁②）。"""
    v = judge([cand("risky", crit=1), cand("safe")])
    assert v.winner == "safe"
    assert "critical" in v.rejected_reasons["risky"]


def test_constraint_violation_is_eliminated():
    """违反已编译约束 -> 淘汰（硬门禁③，★ 含 A2 的自动契约）。"""
    v = judge([cand("viol", viol=["契约:软删除"]), cand("ok")])
    assert v.winner == "ok"
    assert "软删除" in v.rejected_reasons["viol"]


def test_all_candidates_violating_same_constraint_reports_no_viable():
    """**核心用例**（§8.5①）：全部违反同一约束 -> 报告无可行候选。

    规范明确禁止「选违反最少的那个」：那会让用户在接受违规方案时
    毫不知情，而违反的正是 A2 挖出的隐式约定。
    """
    v = judge([cand("a", viol=["契约:软删除"]),
               cand("b", viol=["契约:软删除"])])
    assert v.no_viable_candidate is True
    assert v.winner == "", "不得选出一个『违反最少』的候选"
    # 必须列出全部违规详情，交回人类决策
    assert len(v.rejected_reasons) == 2
    assert any("不选" in n for n in v.notes)


# ── 按序比较（§8.4，**不做加权总分**）──────────────────────────

def test_ordered_comparison_priorities():
    """四级优先顺序：安全发现数 > 影响面 > 冗余率 > 改动行数。"""
    # 第 1 级：安全发现数少者优（即使改动更大）
    v = judge([cand("sec_clean", sec=0, lines=100),
               cand("sec_dirty", sec=2, lines=1)])
    assert v.winner == "sec_clean"

    # 第 2 级：影响面小者优（即使冗余率更高）
    v2 = judge([cand("small", sec=0, imp=1, red=0.9),
                cand("big", sec=0, imp=9, red=0.0)])
    assert v2.winner == "small"

    # 第 3 级：冗余率低者优（即使行数更多）
    v3 = judge([cand("lean", sec=0, imp=1, red=0.0, lines=99),
                cand("fat", sec=0, imp=1, red=0.5, lines=1)])
    assert v3.winner == "lean"


def test_verdict_documents_which_level_decided():
    """裁决必须说明**在第几级分出胜负**，不是只给一个结果。"""
    v = judge([cand("a", imp=1), cand("b", imp=2)])
    assert any("第 2 级" in t for t in v.tie_breakers), \
        f"没说明在哪一级分出胜负: {v.tie_breakers}"


def test_verdict_is_always_explainable():
    """裁决可解释性是硬要求 100%（§8.7）。"""
    for candidates in (
        [cand("only")],
        [cand("a"), cand("b")],
        [cand("x", tests=False)],
        [cand("y", viol=["c"]), cand("z", viol=["c"])],
    ):
        v = judge(candidates)
        assert v.explainable, f"裁决不可解释: {candidates}"


def test_stale_impact_is_downweighted():
    """影响面依赖过期索引时降权（§1.5：A6 的策略是「降权该维度」）。

    避免「基于旧索引算出的好看数字」靠不可靠的信息取胜。
    """
    fresh_big = cand("fresh", imp=3, stale=False)
    stale_small = cand("stale", imp=3, stale=True)
    assert rank_key(stale_small).impact_effective < rank_key(fresh_big).impact_effective


def test_ranking_is_stable_for_identical_candidates():
    """完全相同的候选（真平局）也要给出稳定顺序，不能随机。"""
    v1 = judge([cand("a"), cand("b")])
    v2 = judge([cand("a"), cand("b")])
    assert v1.ranking == v2.ranking


# ── 评审独立性（§8.7 硬要求）──────────────────────────────────

def test_self_generated_candidate_flags_independence_violation():
    """评审者 ID 与生成者 ID 相同时必须标记 —— 规范要求评审独立。"""
    from backend.necessity.compete import JUDGE_ID

    v = judge([cand("self", gen=JUDGE_ID)])
    assert any("独立性" in n for n in v.notes)


def test_independent_generators_do_not_trigger_warning():
    v = judge([cand("a", gen="agent-1"), cand("b", gen="agent-2")])
    assert not any("独立性" in n for n in v.notes)


# ── 多样性（§8.7 硬门禁 ≥75%）─────────────────────────────────

def test_identical_diff_is_detected_as_convergence():
    """四种策略产出同样的补丁 = 「竞争」是假的（§8.6 主要风险）。

    规范把多样性列为**硬门禁**，就是为了防这个。
    """
    same = [Candidate(id=f"c{i}", strategy=s, diff="--- a\n+++ b\n@@\n+x\n")
            for i, s in enumerate(STRATEGIES)]
    assert diversity(same) < MIN_DIVERSITY
    assert diversity(same) == 1 / len(STRATEGIES)


def test_distinct_diffs_pass_diversity_gate():
    distinct = [Candidate(id=f"c{i}", strategy=s, diff=f"--- a\n+++ b\n@@\n+x{i}\n")
                for i, s in enumerate(STRATEGIES)]
    assert diversity(distinct) >= MIN_DIVERSITY


def test_diversity_ignores_line_endings_and_blank_lines():
    """比较要归一化行尾与空行 —— 否则 CRLF 差异会被当成「不同方案」。"""
    a = Candidate(id="a", diff="--- a\n+++ b\n@@\n+x\n")
    b = Candidate(id="b", diff="--- a\r\n+++ b\r\n@@\r\n+x\r\n\r\n")
    assert diversity([a, b]) < MIN_DIVERSITY, "CRLF 差异被误判成不同方案"


# ── 四种策略的区分度来源（§8.4）───────────────────────────────

def test_strategies_impose_different_constraints():
    """四种策略靠**不同的硬约束**产生区分度，不是靠换 prompt 措辞。

    若全部策略约束相同，产出必然趋同，多样性门禁会失败。
    """
    specs = {s.strategy: s for s in build_specs()}
    assert set(specs) == set(STRATEGIES)

    # minimal 有影响面上限；backward_compatible 有签名约束
    assert specs["minimal"].constraints, "minimal 没有约束"
    assert specs["backward_compatible"].constraints, "backward_compatible 没有约束"
    # structural 刻意**不施加**额外约束（它与其他三个的区别所在）
    assert specs["structural"].constraints == []

    # 提示词必须两两不同
    hints = [s.prompt_hint for s in specs.values()]
    assert len(set(hints)) == len(hints), "有策略的提示词重复了"


def test_worth_competing_enforces_cost_budget():
    """A6 是 4× 成本，只在影响面大或用户显式要求时启用（§1.7）。"""
    assert worth_competing(2)[0] is False
    assert worth_competing(5)[0] is True
    assert worth_competing(2, user_requested=True)[0] is True
    # 原因必须可读 —— 否则「没生效」与「不适用」分不开
    on, why = worth_competing(2)
    assert not on and "阈值" in why


# ── 隔离（§8.9 + §8.2）────────────────────────────────────────

def test_arena_reports_leaks():
    """创建了却没清理的候选要能被发现（§8.6 要求 worktree 清理记录）。"""
    from backend.necessity.compete.arena import ArenaStats

    st = ArenaStats(created=["a", "b"], cleaned=["a"])
    assert st.leaked == ["b"]


def test_arena_cleans_up_even_on_exception(tmp_path):
    """异常路径也必须清理 —— 泄漏的 worktree 会让下次运行报
    「分支已存在」，而那个错误与真因无关。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    import subprocess
    for a in (["init", "-q"], ["config", "user.email", "t@t"],
              ["config", "user.name", "t"], ["add", "-A"], ["commit", "-qm", "b"]):
        subprocess.run(["git", *a], cwd=repo, capture_output=True)

    arena = Arena(repo)
    with pytest.raises(RuntimeError):
        with arena.candidate("boom") as sb:
            assert sb.workdir is not None
            raise RuntimeError("候选生成失败")
    assert arena.stats().leaked == [], "异常路径下 worktree 没被清理"


def test_isolation_failure_is_recorded_not_raised():
    """建不起隔离环境不该让整个竞争崩掉 —— 该候选记为 error 即可。"""
    arena = Arena("/definitely/not/a/repo")
    with arena.candidate("x") as sb:
        # 不抛；候选自身带 error 说明
        res, why = sb.run_tests()
    assert res == "error" and why
    assert arena.stats().leaked == [], "创建失败不该被记为已创建"


def test_verify_isolation_detects_dirty_workspace(tmp_path):
    """「不影响主工作区」必须是**可断言的事实**，不是一句设计说明。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    import subprocess
    for a in (["init", "-q"], ["config", "user.email", "t@t"],
              ["config", "user.name", "t"], ["add", "-A"], ["commit", "-qm", "b"]):
        subprocess.run(["git", *a], cwd=repo, capture_output=True)

    ok, _ = verify_isolation(repo)
    assert ok is True

    (repo / "a.py").write_text("x = 2\n", encoding="utf-8")
    bad, why = verify_isolation(repo)
    assert bad is False and "未预期改动" in why


# ── CLI 入口（此前 A6 只有库调用，无自动化入口）────────────────

def test_compete_cli_registered():
    """`necessity compete` 必须注册 —— 否则功能存在但没人用得上。"""
    import subprocess
    r = subprocess.run([sys.executable, "-m", "backend.necessity.cli.main", "--help"],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    assert "compete" in r.stdout, "compete 子命令未注册"


def _run_cli(cands, tmp_path):
    import json
    import subprocess
    p = tmp_path / "c.json"
    p.write_text(json.dumps(cands, ensure_ascii=False), encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-m", "backend.necessity.cli.main", "compete", "judge", str(p)],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace")


def test_compete_cli_returns_zero_with_winner(tmp_path):
    r = _run_cli([
        {"id": "m1", "strategy": "minimal", "diff": "@@ a\n+x",
         "metrics": {"tests_pass": True, "security_findings": 0, "impact_files": 1}},
        {"id": "b1", "strategy": "backward_compatible", "diff": "@@ b\n+y",
         "metrics": {"tests_pass": True, "security_findings": 1, "impact_files": 2}},
    ], tmp_path)
    assert r.returncode == 0
    assert "胜者：m1" in r.stdout
    assert "第 1 级" in r.stdout, "未说明在哪一级分出胜负"


def test_compete_cli_exit_code_1_for_no_viable_candidate(tmp_path):
    """**无可行候选退 1，与「输入错误」的 2 分开。**

    前者是裁决结论（需要人来决定），后者是命令用错了。
    混在一起会让自动化脚本无法判断该找人还是该改参数。
    """
    r = _run_cli([
        {"id": "a", "strategy": "minimal", "diff": "@@ a\n+x",
         "metrics": {"tests_pass": True, "violated_constraints": ["契约:软删除"]}},
        {"id": "b", "strategy": "structural", "diff": "@@ b\n+z",
         "metrics": {"tests_pass": True, "violated_constraints": ["契约:软删除"]}},
    ], tmp_path)
    assert r.returncode == 1
    assert "无可行候选" in r.stdout
    assert "不得选" in r.stdout


def test_compete_cli_exit_code_2_for_bad_input(tmp_path):
    r = _run_cli([], tmp_path)
    assert r.returncode == 2
    assert "没有候选" in r.stderr


def test_compete_cli_warns_on_converged_candidates(tmp_path):
    """多样性不达标时 CLI 必须告警 —— 否则「假竞争」会被当成真竞争。"""
    r = _run_cli([
        {"id": "a", "strategy": "minimal", "diff": "@@ same\n+x",
         "metrics": {"tests_pass": True}},
        {"id": "b", "strategy": "structural", "diff": "@@ same\n+x",
         "metrics": {"tests_pass": True}},
    ], tmp_path)
    assert "多样性不达标" in r.stdout


# ── A2 → Guard 的注入（§4.7 三档）──────────────────────────────

def test_strong_contract_is_injected_with_warn_action():
    """强证据契约注入 guard，且默认动作是 **warn**。

    规范 §4.7：「自动注入的契约首次被违反 -> 不直接 block
    （用户还没确认过它）」。默认 block 会拦住用户不知情的改动。
    """
    import tempfile
    from pathlib import Path as _P

    from backend.necessity.contract.schema import ContractCandidate, compute_confidence
    from backend.necessity.guard.interceptor import GuardHooks

    strong = ContractCandidate(
        id="c-s", statement="删除必须软删除", sources=["tests", "callgraph"],
        confidence=compute_confidence(["tests", "callgraph"], 3),
        guard_type="symbol_scope",
        guard_scope={"kind": "symbol", "pattern": "*delete*"})

    h = GuardHooks({"workspace": str(_P(tempfile.mkdtemp())), "default_action": "warn"})
    h.on_task_start({"id": "t", "contracts": [strong]})
    injected = [c for c in h.constraints if c.id == "c-s"]
    assert injected, "强证据契约未被注入"
    assert injected[0].on_violation == "warn", "未确认的契约不该默认 block"


def test_weak_contract_is_not_injected():
    """低置信度契约不进 guard（规范 §4.7：<0.5 不提）。"""
    import tempfile
    from pathlib import Path as _P

    from backend.necessity.contract.schema import ContractCandidate, compute_confidence
    from backend.necessity.guard.interceptor import GuardHooks

    weak = ContractCandidate(
        id="c-w", statement="轨迹模式", sources=["trace"],
        confidence=compute_confidence(["trace"], 1),
        guard_type="call_chain",
        guard_scope={"kind": "graph", "root": "a", "direction": "callees"})

    h = GuardHooks({"workspace": str(_P(tempfile.mkdtemp())), "default_action": "warn"})
    h.on_task_start({"id": "t", "contracts": [weak]})
    assert not [c for c in h.constraints if c.id == "c-w"]


def test_contract_injection_failure_does_not_break_guard():
    """注入失败必须 fail-open —— 与所有钩子契约一致。"""
    import tempfile
    from pathlib import Path as _P

    from backend.necessity.guard.interceptor import GuardHooks

    h = GuardHooks({"workspace": str(_P(tempfile.mkdtemp())), "default_action": "warn"})
    # 喂一个畸形契约（不是 dict 也不是 ContractCandidate）
    h.on_task_start({"id": "t", "contracts": [object()]})
    assert any("契约注入失败" in n for n in h.notes), "失败没留下痕迹"
