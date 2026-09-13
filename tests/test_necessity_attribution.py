"""Failure Attribution 测试 —— 十类可达、证据必带、优先级、元评测、闭环、降级。

全部离线，不调 LLM（Layer 2 用注入的假分类器验证）。
用**真实 TraceStore API** 构造轨迹，不发明新格式。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.necessity.attribution import (  # noqa: E402
    CATEGORIES, UNKNOWN, AttributionHooks, AttributionResult, FailureClassifier,
    build_attribution_hooks, check_signal_coverage, evaluate_attribution,
    failure_distribution, is_agent_fault, map_free_text_label,
    map_free_text_labels, verify_improvement,
)
from backend.necessity.hooks import TaskResult  # noqa: E402
from backend.necessity.index.trace import TraceStore  # noqa: E402

SESSION = "s1"


# ── 构造工具 ──────────────────────────────────────────────────────

def trace_of(events):
    """events: [(kind, turn, payload_dict)] → 真实 TraceStore。"""
    t = TraceStore()
    for kind, turn, payload in events:
        t.record(SESSION, kind, turn=turn, **payload)
    return t


def fail(turns=3, **stats):
    return TaskResult(task_id="task_001", ok=False, turns=turns,
                      diff_stats=dict(stats))


def attribute(events, result=None, task=None, llm=None):
    clf = FailureClassifier(trace=trace_of(events), llm=llm)
    return clf.attribute(result or fail(), task=task, session_id=SESSION)


# 每类一个「最小可触发轨迹」（构造后断言分类器能选出来）
BUILDERS = {
    "planning": lambda: attribute(
        [("file_read", 1, {"path": "a.py"}), ("file_write", 9, {"path": "a.py"})],
        fail(turns=10)),
    "context_missing": lambda: attribute(
        [("file_write", 1, {"path": "a.py", "added": 2, "removed": 0})], fail(turns=1)),
    "context_stale": lambda: attribute([
        ("file_read", 1, {"path": "a.py"}),
        ("file_write", 2, {"path": "a.py", "writer": "external"}),
        ("file_write", 3, {"path": "a.py", "writer": "agent"}),
    ], fail(turns=3)),
    "tool_use": lambda: attribute(
        [("tool_result", 2, {"error_type": "invalid_arguments"})], fail(turns=2)),
    "edit_error": lambda: attribute([
        ("file_read", 1, {"path": "a.py"}),
        ("file_write", 2, {"path": "a.py"}),
        ("test_run", 3, {"suite": "t", "result": "fail", "error_type": "SyntaxError"}),
    ], fail(turns=3)),
    "constraint_drift": lambda: attribute(
        [("constraint_violation", 1, {"constraint_id": "c1"})], fail(turns=1)),
    "thrashing": lambda: attribute([
        ("file_read", 0, {"path": "a.py"}),
        ("file_write", 1, {"path": "a.py"}),
        ("edit_revert", 2, {"path": "a.py"}),
        ("edit_revert", 3, {"path": "a.py"}),
        ("edit_revert", 4, {"path": "a.py"}),
    ], fail(turns=4)),
    "environment": lambda: attribute(
        [("test_run", 1, {"suite": "t", "result": "fail",
                          "error_type": "ModuleNotFoundError"})], fail(turns=1)),
    "capability": lambda: attribute([
        ("file_read", 1, {"path": "a.py"}),
        ("file_write", 2, {"path": "a.py"}),
    ], fail(turns=10, max_turns=10)),
    "task_issue": lambda: attribute(
        [("test_run", 1, {"suite": "t", "result": "fail"})],
        fail(turns=1), task={"baseline_fail": True}),
}


@pytest.mark.parametrize("category", CATEGORIES)
def test_every_category_is_reachable(category):
    """十类都必须能从构造的轨迹里被选中 —— 否则分类体系有死类。"""
    res = BUILDERS[category]()
    assert res is not None
    assert res.primary == category, f"{category} 不可达，实际 {res.primary}"


@pytest.mark.parametrize("category", CATEGORIES)
def test_evidence_non_empty_and_shaped(category):
    """§3.3：没有证据的归因等于猜。每条证据须含 {turn, signal, detail}。"""
    res = BUILDERS[category]()
    assert res.evidence, f"{category} 归因缺少证据"
    for ev in res.evidence:
        assert set(ev) >= {"turn", "signal", "detail"}
        assert isinstance(ev["detail"], str) and ev["detail"]


@pytest.mark.parametrize("category", CATEGORIES)
def test_improvement_mapping_present(category):
    """分类存在的意义是映射到改进方向（§2.2）。"""
    assert BUILDERS[category]().improvement


def test_result_without_evidence_is_rejected():
    """不变式：非 unknown 的归因不允许没有证据。"""
    with pytest.raises(ValueError):
        AttributionResult(attempt_id="x", primary="planning", evidence=[])


def test_non_agent_fault_flag():
    """§2.3：environment / task_issue 不是 Agent 的锅。"""
    assert is_agent_fault("environment") is False
    assert is_agent_fault("task_issue") is False
    assert is_agent_fault("planning") is True
    for cat in ("environment", "task_issue"):
        assert BUILDERS[cat]().agent_fault is False
    assert BUILDERS["planning"]().agent_fault is True


def test_unknown_is_returned_not_guessed():
    """§5.3：轨迹不足以判定时必须 unknown，不得硬猜。"""
    res = attribute([("file_read", 1, {"path": "a.py"})], fail(turns=1))
    assert res.primary == UNKNOWN
    assert res.evidence == []
    assert res.note
    assert res.improvement


def test_empty_trace_degrades_gracefully():
    """§8.1：无轨迹 → 显式不可归因，不崩溃。"""
    res = FailureClassifier(trace=TraceStore()).attribute(fail(), session_id="nope")
    assert res is not None and res.primary == UNKNOWN
    assert res.method == "unattributable"
    assert "无轨迹" in res.note


def test_passing_result_needs_no_attribution():
    passing = TaskResult(task_id="t", ok=True, turns=3)
    assert FailureClassifier(trace=trace_of([])).attribute(passing) is None


# ── 信号优先级（§4.3）──────────────────────────────────────────────

def test_priority_environment_beats_context_missing():
    """先排除「不是 Agent 的锅」：环境信号压过上下文信号。"""
    res = attribute([
        ("test_run", 1, {"suite": "t", "result": "fail",
                         "error_type": "ConnectionTimeout"}),
        ("file_write", 2, {"path": "a.py", "writer": "agent"}),
    ], fail(turns=2))
    assert res.primary == "environment"
    assert "context_missing" in res.contributing


def test_priority_constraint_drift_beats_thrashing():
    res = attribute([
        ("constraint_violation", 1, {"constraint_id": "c1"}),
        ("file_read", 2, {"path": "a.py"}),
        ("file_write", 3, {"path": "a.py"}),
        ("edit_revert", 4, {"path": "a.py"}),
        ("edit_revert", 5, {"path": "a.py"}),
        ("edit_revert", 6, {"path": "a.py"}),
    ], fail(turns=6))
    assert res.primary == "constraint_drift"
    assert "thrashing" in res.contributing


def test_multi_cause_primary_and_contributing():
    """§2.4：primary + contributing[] 同时给出。"""
    res = attribute([
        ("file_write", 1, {"path": "a.py", "writer": "agent"}),
        ("file_write", 2, {"path": "a.py", "writer": "agent"}),
        ("file_write", 3, {"path": "a.py", "writer": "agent"}),
        ("edit_revert", 4, {"path": "a.py"}),
        ("edit_revert", 5, {"path": "a.py"}),
        ("edit_revert", 6, {"path": "a.py"}),
    ], fail(turns=6))
    assert res.primary == "thrashing"
    assert res.contributing, "次因应被记录"
    assert res.primary not in res.contributing


def test_stats_count_only_primary():
    """统计只用 primary，避免多因重复计数（§2.4）。"""
    a = AttributionResult(attempt_id="a", primary="thrashing", evidence=[{"turn": 1}],
                          contributing=["context_missing"])
    b = AttributionResult(attempt_id="b", primary="context_missing",
                          evidence=[{"turn": 1}], contributing=["thrashing"])
    dist = failure_distribution([a, b])
    assert dist["thrashing"] == pytest.approx(0.5)
    assert dist["context_missing"] == pytest.approx(0.5)
    assert sum(dist.values()) == pytest.approx(1.0)


# ── Layer 2（可注入，测试无需 LLM）─────────────────────────────────

class FakeLLM:
    def __init__(self, out):
        self.out = out
        self.seen = ""

    def classify(self, summary):
        self.seen = summary
        return self.out


def test_llm_layer_used_when_rules_miss():
    llm = FakeLLM({"primary": "planning", "confidence": 0.7,
                   "evidence": ["turn 1-4 无序探索，未先确认改动边界"]})
    res = attribute([("file_read", 1, {"path": "a.py"})], fail(turns=4), llm=llm)
    assert res.primary == "planning" and res.method == "llm"
    assert llm.seen, "应投喂结构化摘要"
    assert "轨迹摘要" in llm.seen


def test_llm_without_evidence_is_rejected():
    """§5.3：LLM 给不出证据 → 放弃，输出 unknown，而不是硬猜。"""
    llm = FakeLLM({"primary": "planning", "confidence": 0.9, "evidence": []})
    res = attribute([("file_read", 1, {"path": "a.py"})], fail(turns=4), llm=llm)
    assert res.primary == UNKNOWN


def test_rule_wins_over_llm_on_conflict():
    """§8.4：冲突时以客观信号为准，并记录冲突。

    Layer 2 只在 Layer 1 无高置信信号时触发（§5.1），所以这里用一条
    低置信度规则信号（capability 0.4）来打开 LLM 层。
    """
    llm = FakeLLM({"primary": "planning", "confidence": 0.99,
                   "evidence": ["LLM 认为是规划问题"]})
    res = attribute([("file_read", 1, {"path": "a.py"}),
                     ("file_write", 2, {"path": "a.py"})],
                    fail(turns=10, max_turns=10), llm=llm)
    assert res.primary == "capability" and res.method == "rule"
    assert res.conflict and "planning" in res.conflict


def test_llm_exception_does_not_break_attribution():
    class Boom:
        def classify(self, summary):
            raise RuntimeError("llm down")

    res = attribute([("file_read", 1, {"path": "a.py"})], fail(turns=1), llm=Boom())
    assert res.primary == UNKNOWN


# ── 元评测：循环性缓解 / Gate 6 / 闭环 ────────────────────────────

def test_free_text_mapping_and_failure_rate():
    """循环性缓解：自由文本先映射，再报失败率。"""
    assert map_free_text_label("环境缺依赖，包装不上") == "environment"
    assert map_free_text_label("一直在同一个函数来回改又回滚") == "thrashing"
    assert map_free_text_label("莫名其妙就挂了") is None

    res = map_free_text_labels({
        "a": "环境缺依赖", "b": "反复回滚", "c": "莫名其妙挂了", "d": "没读过那个文件",
    })
    assert len(res.mapped) == 3 and res.unmapped == ["c"]
    assert res.mapping_failure_rate == pytest.approx(0.25)
    assert res.taxonomy_inadequate() is True
    assert res.as_dict()["mapping_failure_rate"] == pytest.approx(0.25)


def test_meta_eval_accuracy_and_confusion():
    attribs = {
        "a": AttributionResult("a", "planning", evidence=[{"turn": 1}]),
        "b": AttributionResult("b", "thrashing", evidence=[{"turn": 1}]),
        "c": AttributionResult("c", "environment", evidence=[{"turn": 1}]),
    }
    gold = {"a": "planning", "b": "context_missing", "c": "environment"}
    rep = evaluate_attribution(attribs, gold)
    assert rep.total == 3 and rep.correct == 2
    assert rep.accuracy == pytest.approx(2 / 3)
    # top_confusions 是 (gold, predicted) 方向对
    assert ("context_missing", "thrashing") in [p[:2] for p in rep.top_confusions]
    assert rep.per_class["planning"]["precision"] == pytest.approx(1.0)


def test_meta_eval_uses_manual_method_override():
    attribs = {"a": AttributionResult("a", "planning", evidence=[{"turn": 1}])}
    rep = evaluate_attribution(attribs, {"a": "planning"}, methods={"a": "rule"})
    assert rep.by_method["rule"] == pytest.approx(1.0)


def test_gate6_insufficient_when_unknown_over_40pct():
    """>40% unknown → 信号覆盖不足（EVAL.md Gate 6）。"""
    attribs = [AttributionResult(str(i), UNKNOWN) for i in range(3)]
    attribs.append(AttributionResult("ok", "planning", evidence=[{"turn": 1}]))
    cov = check_signal_coverage(attribs)
    assert cov.unknown_ratio == pytest.approx(0.75)
    assert cov.insufficient is True
    assert "补" in cov.note or "污染" in cov.note


def test_gate6_reports_missing_event_kinds():
    """TraceStore.missing_event_kinds() 是 Gate 6 的直接输入。"""
    t = TraceStore()
    t.record(SESSION, "file_read", turn=1)
    cov = check_signal_coverage(
        [AttributionResult("a", "planning", evidence=[{"turn": 1}])], trace=t)
    assert "file_write" in cov.missing_event_kinds
    assert cov.insufficient is True


def test_gate6_ok_when_coverage_sufficient():
    t = TraceStore()
    for k in ("file_read", "file_write", "compaction",
              "edit_revert", "constraint_violation", "test_run"):
        t.record(SESSION, k, turn=1, path="a.py")
    cov = check_signal_coverage(
        [AttributionResult("a", "planning", evidence=[{"turn": 1}])], trace=t)
    assert cov.insufficient is False and cov.unknown_ratio == 0.0


def _cat_results(category, n, total):
    out = [AttributionResult(f"{category}_{i}", category, evidence=[{"turn": 1}])
           for i in range(n)]
    out += [AttributionResult(f"other_{i}", "planning", evidence=[{"turn": 1}])
            for i in range(total - n)]
    return out


def test_closed_loop_reports_success_when_category_drops():
    """EVAL.md §5.1 第 ⑦ 步：目标类占比下降 → 闭环成立。"""
    v = verify_improvement(_cat_results("context_missing", 4, 10),
                           _cat_results("context_missing", 1, 10),
                           "context_missing",
                           completion_before=0.5, completion_after=0.7)
    assert v.target_dropped is True
    assert v.before_ratio == pytest.approx(0.4)
    assert v.after_ratio == pytest.approx(0.1)
    assert "闭环成立" in v.note


def test_closed_loop_reports_failure_honestly():
    """没下降就是没下降，且完成率下降时要提示撤销。"""
    v = verify_improvement(_cat_results("context_missing", 2, 10),
                           _cat_results("context_missing", 3, 10),
                           "context_missing",
                           completion_before=0.6, completion_after=0.4)
    assert v.target_dropped is False
    assert "闭环未成立" in v.note
    assert "撤销" in v.caveat


def test_closed_loop_empty_input_is_safe():
    v = verify_improvement([], [], "planning")
    assert v.target_dropped is False
    assert v.before_total == 0 and v.after_total == 0


# ── 钩子装配（硬契约）─────────────────────────────────────────────

def test_factory_contract_and_composite_compatibility():
    """工厂名与返回形态是硬契约（core/capability.py::FACTORY_NAMES）。"""
    hooks = build_attribution_hooks({"enabled": True})
    assert isinstance(hooks, AttributionHooks)
    # NecessityHooks 子集：只实现生命周期；不越权实现 before_tool
    # （CompositeHooks 用 getattr 探测，缺失即跳过 —— 部分实现是合法形态）
    hooks.on_task_start({"task_id": "t1", "session_id": SESSION})
    assert not hasattr(hooks, "before_tool")
    assert not hasattr(hooks, "scan_workspace")


def test_on_task_end_attributes_using_real_trace():
    t = trace_of([("file_write", 1, {"path": "unread.py", "writer": "agent"})])
    hooks = build_attribution_hooks({"trace": t, "session_id": SESSION})
    rep = hooks.on_task_end(fail(turns=1))
    assert rep["attributed"] is True
    assert rep["attribution"]["primary"] == "context_missing"
    assert hooks.distribution()["context_missing"] == pytest.approx(1.0)


def test_on_task_end_never_raises():
    """契约 1：归因层故障不得阻塞任务（这里让轨迹存储直接抛）。"""
    class BrokenTrace:
        def events(self, session_id="", kind=""):
            raise RuntimeError("trace backend down")

        def missing_event_kinds(self):
            raise RuntimeError("trace backend down")

    hooks = build_attribution_hooks({"trace": BrokenTrace(), "session_id": SESSION})
    out = hooks.on_task_end(fail(turns=1))
    assert out["attributed"] is False
    assert "error" in out
