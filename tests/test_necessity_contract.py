"""A2 隐式行为契约挖掘 —— 锁死「宁缺勿滥」与置信度校准。

规格：`Aurora_六项能力设计规范.md` §4。

## 最大的风险是误报（规范 §4.12 / §4.13）

规范把「挖不到可接受，挖错不可接受」列为第一个非目标，理由很直接：
**一条错误的契约会拦住正确的改动，比没有契约更糟**。
所以本文件反复验证「保守」这一面，而不只是「能挖出来」。

## 一条实测踩过的校准缺陷

初版置信度公式给分太紧（最强来源只有 0.45），导致**最高只能到 0.65**——
永远够不到规范 §4.7 的 0.8 自动注入门槛。那一档因此**形同虚设**：
所有契约不分强弱都被丢进人工队列，功能等于没做。
本文件用「强来源 + ≥2 条独立来源必须能自动注入」锁住这条。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.necessity.contract.compile import (  # noqa: E402
    CompileError,
    to_guard_constraints,
)
from backend.necessity.contract.extract import (  # noqa: E402
    extract_all,
    from_callgraph,
    from_exceptions,
    from_similar,
    from_tests,
    from_types,
)
from backend.necessity.contract.schema import (  # noqa: E402
    AUTO_INJECT_AT,
    CONTRACT_KINDS,
    MIN_SOURCES_FOR_AUTO,
    REVIEW_AT,
    ContractCandidate,
    compute_confidence,
)


# ── 置信度校准（§4.5 强度表 + §4.7 门槛）────────────────────────

def test_strong_source_with_two_independent_sources_reaches_auto_inject():
    """**校准的核心断言**：⭐⭐⭐ + ≥2 条独立来源必须能跨过自动注入门槛。

    规范 §4.7 的表格写的就是「≥ 0.8（强证据，**≥2 条独立来源**）」。
    若这条够不到 0.8，那一档就永远不触发，功能形同虚设 ——
    实测初版正是如此（上限 0.65）。
    """
    assert compute_confidence(["tests", "callgraph"], 2) >= AUTO_INJECT_AT
    assert compute_confidence(["tests", "history"], 2) >= AUTO_INJECT_AT


def test_single_strong_source_lands_in_review_band():
    """单条强来源进人工队列 —— 不够自动注入（规范要求 ≥2 条独立来源）。"""
    c = compute_confidence(["tests"], 1)
    assert REVIEW_AT <= c < AUTO_INJECT_AT


def test_single_weak_source_is_dropped():
    """单条弱来源不提（规范 §4.7：<0.5 不提，避免噪音）。"""
    assert compute_confidence(["trace"], 1) < REVIEW_AT


def test_repeated_weak_evidence_cannot_beat_source_diversity():
    """**多次弱证据不能替代来源多样性。**

    「同一个弱来源出现 20 次」不应跨过自动注入门槛 ——
    规范的门槛写的是「**独立来源**」，重复出现不等于独立。
    这条防的是「用出现次数刷分」。
    """
    assert compute_confidence(["trace"], 50) < REVIEW_AT


def test_unknown_source_scores_zero_not_default():
    """未知来源不计分 —— 而不是给一个默认强度。

    给默认值意味着「我们不知道这个来源有多可信，但先当成还行」，
    那正好违反了「挖错不可接受」。
    """
    assert compute_confidence(["made_up_source"], 5) == 0.0


def test_confidence_never_exceeds_one():
    assert compute_confidence(list(["tests"] * 10), 100) <= 1.0


# ── 三档注入策略（§4.7）────────────────────────────────────────

def test_stale_contract_is_never_injected():
    """`polluted_by_stale` 的契约**不注入**（§1.5 + 门禁）。

    「宁可少一条契约，不可注入错的」—— 依赖过期索引的契约可能已经
    不成立于当前代码，注入它会拦住正确改动。
    """
    c = ContractCandidate(id="x", confidence=0.99, sources=["tests", "callgraph"],
                          polluted_by_stale=True)
    assert c.injection == "skip_stale"


def test_injection_tiers_follow_confidence_and_source_count():
    high = ContractCandidate(id="a", confidence=0.9, sources=["tests", "callgraph"])
    assert high.injection == "auto"

    # 置信度高但只有 1 条来源 -> 不进自动（规范要求 ≥2 条独立来源）
    one_src = ContractCandidate(id="b", confidence=0.9, sources=["tests"])
    assert one_src.injection == "review"

    mid = ContractCandidate(id="c", confidence=0.55, sources=["types"])
    assert mid.injection == "review"

    weak = ContractCandidate(id="d", confidence=0.2, sources=["trace"])
    assert weak.injection == "drop"


def test_min_sources_constant_matches_spec():
    """「≥2 条独立来源」是规范明文，不能悄悄改成 1。"""
    assert MIN_SOURCES_FOR_AUTO == 2


# ── 提取器：保守优先（§4.12）────────────────────────────────────

def test_single_test_assertion_is_not_a_contract():
    """**一次断言不算契约** —— 可能只是巧合。

    判据要求同一符号在 ≥2 个测试函数里被断言。放宽会让候选爆量且
    几乎全是噪声，而噪声契约会拦住正确改动。
    """
    one = "def test_a():\n    assert User(1).save() == '1'\n"
    assert from_tests({"t.py": one}) == []


def test_repeated_assertions_across_tests_become_candidate():
    src = ("def test_a():\n    assert User(1).save() == '1'\n"
           "def test_b():\n    assert User(2).save() == '2'\n")
    cands = from_tests({"t.py": src})
    assert cands and all(c.guard_type == "test_preserved" for c in cands)


def test_call_order_requires_multiple_occurrences():
    """单次调用关系不是契约 —— 太常见，当契约会全是噪声。

    规范 §4.5 说「某函数被调用后必被另一函数调用」，关键词是「必」；
    本实现把它量化为「≥3 处」。
    """
    assert from_callgraph([("a", "b")]) == []
    assert from_callgraph([("a", "b")] * 3), "重复 3 次应产出候选"


def test_exception_contract_requires_multiple_files():
    """「普遍捕获」要跨多个文件才算 —— 单个文件里的 try 不构成接口契约。"""
    one = "try:\n    x()\nexcept ValueError:\n    pass\n"
    assert from_exceptions({"a.py": one}) == []

    many = {f"f{i}.py": one for i in range(3)}
    assert from_exceptions(many), "3 个文件都捕获应产出候选"


def test_optional_never_none_skipped_when_it_does_return_none():
    """确实会返回 None 的函数**不该**被判成「一定返回值」。

    这正是「挖错不可接受」的一个具体形态：判反了会让 Agent 以为
    可以依赖非空返回，而实际可能拿到 None。
    """
    src = ("from typing import Optional\n"
           "def f() -> Optional[int]:\n"
           "    if x:\n        return 1\n    return None\n")
    assert from_types({"m.py": src}) == []


def test_similar_requires_three_occurrences():
    """「同类模块 3+ 个采用同一模式」—— 2 个不算（规范 §4.5）。"""
    two = {"a.py": "def delete_user():\n    db.delete(1)\n",
           "b.py": "def delete_item():\n    db.delete(2)\n"}
    # 两个都含 delete 但用了硬删除 -> 不产出软删除契约
    assert from_similar(two) == []


def test_extract_all_survives_one_source_failing():
    """某个来源失败不该让整个挖掘失败（挖不到可接受）。"""
    cands = extract_all(test_files={"bad.py": "def broken(:\n"},  # 语法错误
                        call_pairs=[], sources={}, module_sources={})
    assert isinstance(cands, list)          # 不抛即通过


# ── 编译成 guard 约束（§4.3「执行层白送」）──────────────────────

def test_strong_contracts_compile_into_guard_types():
    """强证据契约必须能编译成 8 种 guard 类型之一（§4.9 硬要求）。"""
    cands = extract_all(
        test_files={"t.py": "def test_a():\n    assert User(1).save() == '1'\n"
                            "def test_b():\n    assert User(2).save() == '2'\n"})
    cons, problems = to_guard_constraints(cands, strict=True)
    assert cons, "强证据契约一条都没编译出来"
    assert problems == [], f"强证据编译出问题: {problems}"


def test_compiled_scope_matches_guard_conventions():
    """编译出的 scope 形状必须精确匹配 `guard/spec.py` 的约定。

    形状不对**不会报错** —— `checker` 读不到它要的键就跳过，
    表现为「约束配了但不生效」。所以这里逐个类型断言 key。
    """
    cases = {
        "test_preserved": {"kind": "tests", "selectors": ["t"]},
        "call_chain": {"kind": "graph", "root": "a", "direction": "callees"},
        "symbol_scope": {"kind": "symbol", "pattern": "*del*", "qualifier": "public"},
        "signature_stable": {"kind": "symbols", "symbols": [{"file": "a", "name": "b"}]},
        "impact_limit": {"kind": "symbols", "symbols": [{"file": "a", "name": "b"}],
                         "max_files": 3},
    }
    for gtype, scope in cases.items():
        cand = ContractCandidate(id=f"c-{gtype}", statement="x", sources=["tests"],
                                 confidence=0.9, guard_type=gtype, guard_scope=scope)
        cons, probs = to_guard_constraints([cand], strict=True)
        assert cons, f"{gtype} 编译失败: {probs}"
        assert cons[0].type == gtype


def test_contract_without_guard_type_is_not_forced_into_a_type():
    """没有对应 guard 类型的候选**不硬塞** —— 硬塞会产出错误约束。

    异常契约就是这种（规范 §4.5 列了它，但 8 类型里没有直接对应的）。
    正确行为是如实记录「不注入」，而不是随便挑一个类型塞进去。
    """
    cand = ContractCandidate(id="x", statement="ValueError 是接口契约",
                             sources=["exceptions"], confidence=0.9,
                             guard_type="")
    cons, probs = to_guard_constraints([cand])
    assert cons == []
    assert any("无对应的 guard 类型" in p for p in probs)


def test_compiled_constraint_defaults_to_warn_not_block():
    """自动注入的契约默认动作是 `warn`，不是 `block`。

    规范 §4.7：「自动注入的契约首次被违反 → **不直接 block**
    （用户还没确认过它）→ 降级为 warn」。默认 block 会在用户
    完全不知情的情况下拦住他的改动。
    """
    cand = ContractCandidate(id="x", statement="s", sources=["tests", "callgraph"],
                             confidence=0.9, guard_type="symbol_scope",
                             guard_scope={"kind": "symbol", "pattern": "*del*"})
    cons, _ = to_guard_constraints([cand])
    assert cons[0].on_violation == "warn"


def test_strict_mode_raises_for_strong_contract_that_cannot_compile():
    """强证据契约编译失败时 `strict=True` 必须抛 —— 那是我们的 bug。

    规范 §4.9 把「强证据 100% 可编译」列为硬要求。静默跳过会让
    这条要求无从验证（永远没失败，也永远没证明）。
    """
    bad = ContractCandidate(id="x", statement="s", sources=["tests", "callgraph"],
                            confidence=0.9, guard_type="symbol_scope",
                            guard_scope={})      # 缺 pattern
    with pytest.raises(CompileError):
        to_guard_constraints([bad], strict=True)
    # 非 strict 时只记问题，不抛
    cons, probs = to_guard_constraints([bad])
    assert cons == [] and probs


def test_contract_kinds_map_into_the_eight_guard_types():
    """规范 §4.3 的映射表必须都指向真实存在的 guard 类型。"""
    from backend.necessity.guard.spec import CONSTRAINT_TYPES

    for kind, gtype in CONTRACT_KINDS.items():
        assert gtype in CONSTRAINT_TYPES, f"{kind} -> {gtype} 不是合法的 guard 类型"


# ── git 历史裁判法（§4.6，ground truth 的客观来源）──────────────

def test_fix_detection_uses_commit_prefix_not_subject_keywords():
    """**只看提交类型前缀**，不扫整个 subject。

    实测踩过：扫全 subject 会把 `feat(necessity): … 含 5 个实测缺陷修`
    也算成 fix（中文描述里带「修」字）。而 feat 是**新增功能**，
    不是「违反约定后的修复」—— 用它当违反事件会让召回率分母虚高、
    匹配率虚低，整份评估失去意义。
    """
    from backend.necessity.contract.judge import _FIX_PREFIX_RE

    assert _FIX_PREFIX_RE.search("fix: 修了一处问题")
    assert _FIX_PREFIX_RE.search("fix(scope): 修了一处问题")
    assert not _FIX_PREFIX_RE.search("feat(x): 加了功能，顺手修了个错字")
    assert not _FIX_PREFIX_RE.search("docs: 更新文档并修了几个错字")
    assert not _FIX_PREFIX_RE.search("修复了那个问题")   # 没有类型前缀


def test_judge_filters_runtime_artifacts_from_events():
    """运行时产物不该参与契约裁判。

    实测发现：真实仓库历史里 fix 提交的 files 混着 `.aurora/fts5.db-shm`、
    `.necessity/index.db` 这类产物。它们与行为约定无关，留着会让**任意**
    候选的 scope 都「有可能」匹配上，把误报率稀释成无意义的数字。
    """
    from backend.necessity.contract.judge import _is_code_file

    assert _is_code_file("backend/agent/nodes.py")
    assert _is_code_file("README.md")            # 文档可能是约定的载体
    assert not _is_code_file(".necessity/index.db")
    assert not _is_code_file(".aurora/fts5.db-shm")
    assert not _is_code_file("backend/__pycache__/x.pyc")
    assert not _is_code_file("node_modules/pkg/index.js")


def test_judge_returns_lower_bound_not_truth():
    """召回率字段名必须是 `recall_lower_bound` —— 它是**下界**不是真值。

    规范 §4.6 明确：「不是每条违反都会被 revert → 这是召回率的下界，
    不是真值」。把下界当准确值会让人高估覆盖率。
    """
    from backend.necessity.contract.judge import JudgeResult

    r = JudgeResult()
    assert hasattr(r, "recall_lower_bound")
    assert not hasattr(r, "recall"), "不该暴露一个叫 recall 的字段（会被当成真值）"


def test_judge_reports_when_no_evidence_instead_of_claiming_success():
    """没有裁判数据时必须说「没有数据」，不能说「契约都对」。

    那是两件完全不同的事 —— 前者是「不知道」，后者是「验证过」。
    """
    from backend.necessity.contract.judge import evaluate

    res = evaluate([], "/nonexistent/repo/path")
    assert res.recall_lower_bound is None
    assert any("不是" in n and "契约都对" in n for n in res.notes)


def test_judge_works_on_real_history():
    """在 Aurora 自己的历史上能跑出数据（端到端）。"""
    from backend.necessity.contract.judge import find_violation_events

    evs = find_violation_events(str(ROOT), limit=200)
    assert evs, "在真实仓库上没找到任何 fix/revert 事件"
    assert all(e.commit and e.kind in ("revert", "fix") for e in evs)
    # 事件里的文件都应是代码文件
    for e in evs:
        for f in e.files:
            assert not f.startswith((".aurora/", ".necessity/")), f"噪声混入: {f}"


# ── 审批交互（§4.7）────────────────────────────────────────────

def test_auto_contract_defaults_to_warn_not_block():
    """**自动挖出的契约默认是 warn** —— 用户还没确认过它（§4.7）。

    默认 block 会在用户完全不知情的情况下拦住他的改动，
    而那正是 v1 被指出的洞：「用户可能被一堆他没要求过的约束拦住」。
    """
    from backend.necessity.contract.review import CONFIRMED, ReviewQueue

    q = ReviewQueue(state_path=ROOT / ".necessity" / "_t_review.json")
    assert q.effective_action("c-1") == "warn"
    # 用户确认后才升级
    q.decide("c-1", CONFIRMED)
    assert q.effective_action("c-1") == "block"
    (ROOT / ".necessity" / "_t_review.json").unlink(missing_ok=True)


def test_permanently_ignored_contract_is_off():
    """「永久忽略这条」必须真的不再生效，也不该再出现在队列里。"""
    from backend.necessity.contract.review import IGNORED_ALWAYS, ReviewQueue
    from backend.necessity.contract.schema import ContractCandidate

    import tempfile
    from pathlib import Path as _P
    q = ReviewQueue(state_path=_P(tempfile.mkdtemp()) / "s.json")
    q.decide("c-x", IGNORED_ALWAYS)
    assert q.effective_action("c-x") == "off"
    assert q.is_off("c-x")

    cand = ContractCandidate(id="c-x", statement="s", confidence=0.6,
                             sources=["types"], guard_type="symbol_scope",
                             guard_scope={"kind": "symbol", "pattern": "*a*"})
    assert q.pending_items([cand]) == [], "永久忽略的契约仍出现在队列里"


def test_ignored_once_still_reminds_next_time():
    """「忽略本次」只对这一次有效 —— 下次还要提醒。

    区分「忽略本次」与「永久忽略」是有意的：前者是「这次情况特殊」，
    后者是「这条约定对我们不适用」。
    """
    from backend.necessity.contract.review import IGNORED_ONCE, ReviewQueue

    import tempfile
    from pathlib import Path as _P
    q = ReviewQueue(state_path=_P(tempfile.mkdtemp()) / "s.json")
    q.decide("c-y", IGNORED_ONCE)
    assert q.effective_action("c-y") == "warn", "忽略本次不该永久关闭"
    assert not q.is_off("c-y")


def test_review_state_persists_across_instances():
    """确认状态必须持久化 —— 否则每次重启都要重新确认一遍。

    那正是「养成无脑点同意」的成因：反复问同一个问题会让用户不再思考。
    """
    from backend.necessity.contract.review import CONFIRMED, ReviewQueue

    import tempfile
    from pathlib import Path as _P
    sp = _P(tempfile.mkdtemp()) / "state.json"
    ReviewQueue(state_path=sp).decide("c-z", CONFIRMED)
    assert ReviewQueue(state_path=sp).effective_action("c-z") == "block"


def test_review_queue_documents_evidence_for_each_item():
    """`review_queue.md` 每条都要带依据与置信度 —— 用户据此判断。

    规范 §4.7 的中间档是「用户主动查看后才启用」，
    所以清单必须可读、可判断，而不是一串 ID。
    """
    from backend.necessity.contract.review import ReviewQueue
    from backend.necessity.contract.schema import ContractCandidate

    import tempfile
    from pathlib import Path as _P
    q = ReviewQueue(state_path=_P(tempfile.mkdtemp()) / "s.json")
    cand = ContractCandidate(id="c-q", statement="删除必须软删除",
                             confidence=0.6, sources=["types"],
                             guard_type="symbol_scope",
                             guard_scope={"kind": "symbol", "pattern": "*del*"})
    md = q.render_queue([cand])
    assert "删除必须软删除" in md
    assert "0.60" in md and "types" in md
    assert "尚未注入" in md
