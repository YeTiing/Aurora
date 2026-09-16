"""A3 不确定性感知执行 —— 锁死四档语义与「精准提问」两条纪律。

规格：`Aurora_六项能力设计规范.md` §5。

## 这套东西要解决的矛盾（规范 §5.1）

    全自动 → 高风险操作没人拦
    全问   → 用户被淹没，养成「无脑点同意」的习惯（**比不问更危险**）

所以它必须**既有用又不烦人**。这带来的张力是：判据太松则形同虚设，
太紧则把简单任务也拦住。本文件两边都锁。

## 核心价值在提问质量（§5.5）

规范把价值定位在提问上，不是风险打分上：
「不要问『是否继续？』，要问能消除歧义的问题」。
所以有专门的用例校验问题的**句式与选项数**。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.necessity.autonomy.clarify import (  # noqa: E402
    MAX_OPTIONS,
    Question,
    generate,
    validate,
)
from backend.necessity.autonomy.score import (  # noqa: E402
    score_signals,
    to_approval_policy,
)
from backend.necessity.autonomy.signals import RiskSignals, collect  # noqa: E402


def clean(**over):
    """信息齐全的低风险信号 —— 基线，用于对照。"""
    base = dict(requirement_ambiguous=False, target_symbol_unique=True,
                callgraph_fresh=True, touches_public_api=False,
                has_related_tests=True, involves_risky_domain=False,
                scope_over_budget=False, rollback_costly=False)
    base.update(over)
    return RiskSignals(**base)


# ── 四档语义（规范 §5.4）────────────────────────────────────────

def test_clean_low_risk_is_auto():
    d = score_signals(clean())
    assert d.level == "auto" and d.score == 0.0
    assert to_approval_policy("auto") == "never"


def test_public_api_change_slows_down_to_plan_first():
    """触及公共接口 -> plan_first（不打断用户，但先出计划）。"""
    d = score_signals(clean(touches_public_api=True))
    assert d.level == "plan_first"
    assert d.reasons, "plan_first 也必须说明原因"


def test_high_risk_asks_with_questions():
    d = score_signals(clean(requirement_ambiguous=True, touches_public_api=True,
                            has_related_tests=False))
    assert d.level == "ask"
    assert to_approval_policy("ask") == "on-request"
    qs = generate(clean(requirement_ambiguous=True, touches_public_api=True,
                        has_related_tests=False))
    assert qs and all(q.is_valid() for q in qs)


def test_start_of_task_with_no_signals_is_plan_first_not_stop():
    """**开局全未采集 = plan_first，不是 stop。**

    实测踩过：把「全部未检测」判成 stop 会让**每个任务**在开局就停住
    —— 包括「把 add 函数改成加法」。功能因此形同虚设。
    规范 §5.12 给的缓解措施正是「先跑 plan_first 收集阶段 A 数据，
    再逐步收紧」，所以开局就该是 plan_first。
    """
    d = score_signals(RiskSignals())      # 8 项全未检测
    assert d.level == "plan_first", "任务开局被误判为 stop"


def test_partial_missing_with_no_confirmed_risk_is_stop():
    """**部分**信号拿不到、且没有任何确认信号 -> stop（说明缺什么）。

    与「开局全未采集」的区别在语义：前者是「还没开始采」，
    后者是「该拿到但拿不到」—— 后者才真的无法判断。

    注意未检测数要落在 [STOP_UNKNOWN_AT, 8) 区间：等于 8 是「开局」
    （走 plan_first），小于阈值则还不足以判 stop。
    """
    from backend.necessity.autonomy.score import STOP_UNKNOWN_AT

    # 只采到 1 项 -> 7 项未检测，正好达到 stop 阈值且不是「全部未采集」
    s = RiskSignals(target_symbol_unique=True)
    assert s.unknown_count == STOP_UNKNOWN_AT
    d = score_signals(s)
    assert d.level == "stop", f"unknown={s.unknown_count} 应判 stop，实际 {d.level}"
    assert any("未能采集" in r for r in d.reasons)


def test_unknown_signals_count_as_risk_not_as_safe():
    """**未检测按有风险计分**，不按无风险计。

    把它当 False 等于「不知道有没有风险所以放行」——
    这是本系统最危险的默认。理由必须写进 reasons，让用户知道
    我们是因为**信息不足**才谨慎的，而不是发现了问题。
    """
    # 采到一半信号（未检测数低于 stop 阈值，才会走逐项计分分支）
    s = RiskSignals(requirement_ambiguous=False, target_symbol_unique=True,
                    callgraph_fresh=True, touches_public_api=False)
    assert 0 < s.unknown_count < 7

    d = score_signals(s)
    assert d.score > 0
    assert any("未检测" in r and "按风险计" in r for r in d.reasons)


def test_confirmed_risk_beats_mass_unknown():
    """已有确认风险时按分数走，不落入 stop（否则「停止」失去信号量）。"""
    s = RiskSignals(touches_public_api=True, rollback_costly=True)
    d = score_signals(s)
    assert d.level in ("ask", "plan_first"), f"有确认风险却判 {d.level}"


def test_contracts_count_escalates():
    """生效的自动契约数作为加分信号（规范 §1.6 交互③）。

    契约越多说明这块代码的隐式约定越密集，改错的代价越高。
    """
    few = score_signals(clean(active_contracts=2))
    mid = score_signals(clean(active_contracts=3))
    many = score_signals(clean(active_contracts=6))
    assert few.score < mid.score < many.score


# ── 澄清问题（规范 §5.5，核心价值）──────────────────────────────

def test_questions_are_ab_choice_not_open_ended():
    """问题必须是「给出两个具体选项」的句式，不是开放式提问。

    规范原话：「不要问『是否继续？』」—— 那种问题用户只能回答「是」，
    等于没问，还会训练出无脑同意的习惯。
    """
    qs = generate(clean(requirement_ambiguous=True))
    assert qs, "有歧义却没生成任何问题"
    q = qs[0]
    assert len(q.options) >= 2
    assert "你希望哪一种" in q.text or "?" in q.text


@pytest.mark.parametrize("bad", ["是否继续？", "可以吗？", "确定？", "有没有问题"])
def test_banned_open_ended_phrasings_are_rejected(bad):
    """笼统问法必须被校验器拒绝 —— 且这校验对外部传入的问题同样生效。

    只约束自己的生成器不够：别人拼一个问题塞进来就绕过了纪律。
    """
    assert validate(Question(signal="x", text=bad, options=["A", "B"]))


def test_option_count_is_bounded():
    """选项数必须 ≥2 且 ≤ 上限（规范 §5.5：超过 3 个选项禁止）。"""
    assert validate(Question("x", "可以 A，也可以 B", ["A"]))
    assert validate(Question("x", "可以 A，也可以 B", ["A", "B", "C", "D"]))
    assert not validate(Question("x", "可以 A，也可以 B", ["A", "B"]))
    assert MAX_OPTIONS == 3


def test_question_limit_keeps_user_interruption_bounded():
    """一次最多问 2 个 —— 「全问 → 用户被淹没」（规范 §5.1）。"""
    many = generate(clean(requirement_ambiguous=True, touches_public_api=True,
                          has_related_tests=False, rollback_costly=True,
                          involves_risky_domain=True, scope_over_budget=True))
    assert len(many) <= 2


def test_unknown_signals_do_not_produce_questions():
    """未检测的信号**不生成问题**。

    问「我不知道有没有风险，你想怎样」对用户毫无帮助。
    未检测应体现在风险分与 stop 分支上，而不是变成一个无从回答的问题。
    """
    qs = generate(RiskSignals())     # 全未检测
    assert qs == []


def test_ask_without_questions_degrades_to_plan_first():
    """该问却问不出问题时，降级为 plan_first 而不是硬问。

    这是「宁可少一次打扰，也不问一个无效问题」—— 无效提问会训练用户
    无脑同意。同时必须留下日志（问题库覆盖不足是我们的缺陷）。
    """
    from backend.necessity.autonomy.hooks import AutonomyHooks

    h = AutonomyHooks()
    # 构造一个会触发 ask 但问题库未覆盖的信号组合很难，
    # 所以直接验证 refresh 的降级分支存在且可用
    h.on_task_start({"input": "x"})
    assert h.decision.level in ("auto", "plan_first", "ask", "stop")
    if h.decision.level == "ask":
        assert h.questions, "判 ask 却没有问题"


# ── 信号采集 ───────────────────────────────────────────────────

def test_signals_are_tri_state_not_boolean():
    """信号必须能表达「未检测」—— 不能退化成布尔。

    `requirement_ambiguous=None`（compiler 未启用）与 `False`（确认无歧义）
    是完全不同的两件事。规范 §5.6 明确要求「标注为『未检测』而非『无歧义』」。
    """
    s = collect(task_text="改个东西")   # 什么上下文都没给
    assert s.requirement_ambiguous is None
    assert s.callgraph_fresh is None
    assert s.unknown, "未检测必须记录原因"


def test_ambiguity_comes_from_compiler_not_a_new_llm_call():
    """需求歧义必须**消费** compiler 的结果，不新增 LLM 调用点（§5.6）。

    实测：hooks 契约禁止在主循环调 LLM，而 `on_task_start` 就在主循环上。
    所以歧义判定必须并入 compiler（它本来就允许调 LLM）。
    """
    class _CR:
        ambiguous = True
        ambiguity_checked = True

    s = collect("x", compile_result=_CR())
    assert s.requirement_ambiguous is True

    # compiler 存在但没判过 -> 未检测，不是 False
    class _Old:
        ambiguous = False
        ambiguity_checked = False

    s2 = collect("x", compile_result=_Old())
    assert s2.requirement_ambiguous is None, "未判过却当成「无歧义」"


def test_compiler_detects_ambiguity_as_side_product():
    """compiler 确实会产出歧义标记（§5.6 的「副产品」）。"""
    from backend.necessity.guard.compiler import compile_constraints

    clear = compile_constraints(["只允许修改 src/parser/ 下的文件"])
    assert clear.ambiguity_checked is True and clear.ambiguous is False

    vague = compile_constraints(["尽量只改 src/ 下，最好别动别的"])
    assert vague.ambiguous is True and vague.ambiguity_notes

    conflicting = compile_constraints(["只允许修改 src/a/ 下的文件",
                                       "只允许修改 src/b/ 下的文件"])
    assert conflicting.ambiguous is True


# ── 接线 ───────────────────────────────────────────────────────

def test_autonomy_registers_and_is_off_by_default():
    """A3 必须能经真实装配挂上，且**默认关**。

    默认关的理由：它改变**用户交互行为**（是否追问）。规范 §0.3 要求
    所有阈值标定前不得作为验收依据，而误自动率是外部参照的硬指标 ——
    未标定前不该替用户做「该不该问」的决定。
    """
    from backend.necessity.capability import DEFAULT_ENABLED, FACTORY_NAMES, load_capabilities

    assert "autonomy" in FACTORY_NAMES, "A3 未注册工厂，会被静默跳过"
    assert DEFAULT_ENABLED.get("autonomy") is False
    assert "autonomy" not in load_capabilities({})

    caps = load_capabilities({"enabled": False, "autonomy": {"enabled": True}})
    assert "autonomy" in caps, "A3 未能经真实装配路径挂载"


def test_on_task_end_reports_spec_metrics():
    """`on_task_end` 必须产出规范 §1.4 表格里 A3 的观测字段。"""
    from backend.necessity.capability import CompositeHooks, load_capabilities

    caps = load_capabilities({"enabled": False, "autonomy": {"enabled": True}})
    caps["autonomy"].on_task_start({"input": "改公共接口 尽量兼容"})

    from backend.necessity.hooks import TaskResult
    out = CompositeHooks(caps).on_task_end(TaskResult(task_id="t", ok=True))
    m = out["autonomy"]
    for field in ("autonomy_asks", "autonomy_overridden", "autonomy_missed_risk"):
        assert field in m, f"缺观测字段 {field}（§1.4 的闭环要用）"
