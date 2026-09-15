"""A1 证据化补丁 —— 锁死「不造假」与「没扫 ≠ 干净」两条纪律。

规格：`Aurora_六项能力设计规范.md` §3。

## 这套东西存在的理由（规范 §3.1）

绝大多数 Agent 只报「我做了什么」，不报「**我没做什么**」。
所以 `unverified`（本次没验证什么）被规范标为核心，并参与门禁：
「`unverified` 永远为空 → ⚠️ 可疑 —— 说明验收项拆分没工作」。

## 最大的风险是造假（规范 §3.12）

「为凑字段而造假数据」被列为主要风险。缓解措施写进了代码：
**取不到就写「未采集」**，绝不填一个看起来合理的默认值。
本文件的多数用例就是锁这一条。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.necessity.hooks import FileChange, TaskResult  # noqa: E402
from backend.necessity.report import (  # noqa: E402
    SYNC_FIELDS,
    EvidenceBundle,
    build_bundle,
    enrich_bundle,
    render_markdown,
    write_report,
)
from backend.necessity.report.schema import SecurityEvidence  # noqa: E402


def task(task_id="t1", **kw):
    return TaskResult(task_id=task_id, ok=True, **kw)


# ── 两段式：status / pending（规范 §3.6）──────────────────────

def test_sync_segment_is_partial_and_declares_what_is_pending():
    """同步段必须自报 `partial` 且列出待补项。

    规范 §3.6 硬性要求 1：「必须在显眼位置标注『必要性证据尚未生成』——
    不得让用户以为已完成」。所以 `status`/`pending` 是**必填**的。
    """
    b = build_bundle(task(), changes=[])
    assert b.status == "partial"
    assert "necessity" in b.pending


def test_enrich_moves_to_complete_only_when_necessity_succeeds():
    b = build_bundle(task(), changes=[])

    class _Res:
        hunks = [type("H", (), {"id": "h1", "file": "a.py"})()]

    enrich_bundle(b, necessity_runner=lambda: _Res())
    assert b.status == "complete"
    assert b.pending == []
    assert b.necessity and b.necessity[0].necessary is True


def test_enrich_failure_keeps_partial_and_never_fakes_data():
    """必要性跑失败时**保持 partial**，不得伪造数据（§3.6 硬性要求 3）。"""
    b = build_bundle(task(), changes=[])

    def boom():
        raise RuntimeError("reduce 沙箱挂了")

    enrich_bundle(b, necessity_runner=boom)
    assert b.status == "partial", "失败却标成了 complete"
    assert b.necessity == [], "失败却伪造了必要性数据"
    assert any("reduce 沙箱挂了" in e for e in b.collection_errors)


def test_enrich_without_runner_stays_partial():
    b = build_bundle(task(), changes=[])
    enrich_bundle(b, necessity_runner=None)
    assert b.status == "partial"
    assert any("未接入" in e for e in b.collection_errors)


# ── 「没扫 ≠ 干净」（最容易被写错的一条）──────────────────────

def test_security_not_scanned_is_not_clean():
    """未经扫描**不能**被当成干净。

    把未知当通过是安全类里最危险的错误：报告会给出虚假的安心感。
    """
    s = SecurityEvidence(scanned=False)
    assert s.clean is False

    s2 = SecurityEvidence(scanned=True)
    assert s2.clean is True

    s3 = SecurityEvidence(scanned=True, critical=1)
    assert s3.clean is False


def test_bundle_marks_security_unscanned_when_no_paths():
    b = build_bundle(task(), changes=[])
    assert b.security.scanned is False
    assert "没有可扫描" in b.security.note
    assert b.security.clean is False


def test_security_scan_is_synchronous_only_and_says_so(tmp_path):
    """同步段只跑**同步**的密钥层，并在 note 里说明层数。

    实测踩过：`SecurityScanner.scan()` 是 `async def` 且签名是单个 filepath。
    同步调用它拿回协程对象，却把 `scanned=True` 写进报告 ——
    **未扫描却声称已扫描**。所以这里必须：① 只用同步接口；
    ② 在 note 里如实标出只覆盖了密钥层。
    """
    leak = tmp_path / "leak.py"
    leak.write_text("API_KEY = 'sk-abcdefghijklmnopqrst'\n", encoding="utf-8")
    clean = tmp_path / "clean.py"
    clean.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    from backend.security_scanner import SecurityScanner
    sc = SecurityScanner(workspace=str(tmp_path))
    b = build_bundle(task(), changes=[FileChange(path=str(leak), kind="modify"),
                                      FileChange(path=str(clean), kind="modify")],
                     scanner=sc)
    assert b.security.scanned is True
    assert b.security.critical >= 1, "密钥未检出"
    # note 必须如实说明覆盖面，不能让读者以为做了全量扫描
    assert "密钥" in b.security.note and "未" in b.security.note


def test_out_of_workspace_paths_are_recorded_not_silently_clean(tmp_path):
    """路径不在扫描器 workspace 内时必须**留下痕迹**，不能静默报干净。

    实测：`SecurityScanner.scan_secrets` 内部 `except Exception: pass`
    会吞掉「路径不在 workspace」的错误并返回空列表 —— 看起来像
    「扫过了、没发现」。那是「没扫」被伪装成「干净」的经典形态。
    """
    from backend.security_scanner import SecurityScanner
    other = tmp_path / "other"
    other.mkdir()
    (other / "leak.py").write_text("API_KEY = 'sk-abcdefghijklmnopqrst'\n",
                                   encoding="utf-8")
    sc = SecurityScanner(workspace=str(tmp_path / "nowhere"))
    b = build_bundle(task(), changes=[FileChange(path=str(other / "leak.py"),
                                                kind="modify")], scanner=sc)
    # 要么报出问题，要么明确说「未覆盖」—— 都不能是「干净」
    assert b.security.clean is False or b.collection_errors


# ── 「本次没验证什么」必须显式（规范 §3.5 / §3.9）───────────────

def test_unverified_is_never_empty():
    """`unverified` 不得为空 —— 那会被门禁判为可疑（§3.9）。

    没有需求清单时的正确表达是「无法判定覆盖」，而不是「全部已验证」。
    """
    b = build_bundle(task(), changes=[])
    assert b.unverified, "unverified 为空会让门禁判为可疑"
    assert any("无法判定" in u or "未采集" in u or "无" in u for u in b.unverified)


def test_unverified_lists_missing_requirements():
    b = build_bundle(
        task(), changes=[],
        requirements=["需求A", "需求B"],
        test_results=[{"command": "需求A", "exit_code": 0, "passed": True}])
    assert any("需求B" in u for u in b.unverified), "漏掉的需求没被列出"


def test_unverified_declares_none_explicitly_when_all_covered():
    """全部覆盖时也要**显式**写「无」，而不是留空列表。"""
    b = build_bundle(
        task(), changes=[],
        requirements=["需求A"],
        test_results=[{"command": "需求A", "exit_code": 0, "passed": True}])
    assert b.unverified == ["（无：全部验收项均有对应验证）"]


# ── 门禁与填充率（规范 §3.8 / §3.9）────────────────────────────

def test_filled_ratio_covers_all_sync_fields():
    b = build_bundle(task(), changes=[])
    assert b.filled_ratio == 1.0, f"同步段字段未填满 {SYNC_FIELDS}"
    assert b.verifiable is True


def test_collection_errors_are_recorded_not_hidden():
    """缺来源时必须记进 `collection_errors` —— 这是「不造假」的落地点。"""
    b = build_bundle(task(), changes=[], store=None, symbols=None)
    assert b.collection_errors, "来源缺席却没有留下任何痕迹"
    assert any("影响面未采集" in e for e in b.collection_errors)


def test_missing_impact_is_unknown_not_zero():
    """没采到影响面 ≠ 无影响。措辞必须体现这个区别。"""
    b = build_bundle(task(), changes=[], store=None, symbols=None)
    msg = " ".join(b.collection_errors)
    assert "不是「无影响」，是「未知」" in msg


# ── 渲染：首屏三条（规范 §3.7）────────────────────────────────

def test_markdown_first_screen_shows_completeness_unverified_conclusion():
    """首屏必须依次给出：完整性 → 结论 → 未验证项（规范 §3.7）。

    断言的是**相对顺序**而不是「前 16 行」：行数会随采集失败项数量浮动，
    而「三条硬要求都排在采集失败明细之前」才是规范的真正意图
    （失败明细一多就把它们挤下去，等于没有首屏）。
    """
    b = build_bundle(task("tX"), changes=[])
    md = render_markdown(b)

    i_complete = md.index("完整性")
    i_conclusion = md.index("## 结论")
    i_unverified = md.index("## 未验证项")
    assert i_complete < i_conclusion < i_unverified, "三条硬要求顺序不对"

    # 采集失败明细必须**排在**三条之后 —— 否则它会把硬要求挤出首屏
    if "采集失败项" in md:
        assert i_unverified < md.index("采集失败项"), \
            "采集失败明细挤到了未验证项之前"


def test_partial_report_warns_in_first_screen():
    """`partial` 必须在最显眼处 —— 不能让用户以为报告已完整。"""
    b = build_bundle(task(), changes=[])
    md = render_markdown(b)
    assert "不完整" in md
    assert "必要性判定" in md


def test_report_renders_staleness_warning_when_graph_is_stale():
    """索引过期必须在报告里显式标注（规范 §1.5：不隐藏）。"""
    b = build_bundle(task(), changes=[])
    from backend.necessity.report.schema import StalenessInfo
    b.staleness = StalenessInfo(stale_files=["a.py", "b.py"], callgraph_fresh=False)
    md = render_markdown(b)
    assert "影响面可能过期" in md


# ── 落盘 ───────────────────────────────────────────────────────

def test_write_report_emits_json_and_markdown(tmp_path):
    b = build_bundle(task("tt"), changes=[])
    jp, mp = write_report(b, str(tmp_path / "bundles"))
    data = json.loads(Path(jp).read_text(encoding="utf-8"))
    assert data["task_id"] == "tt"
    assert data["status"] == "partial"
    assert Path(mp).is_file()
    # JSON 与 Markdown 从同一对象渲染，不该出现「报告说 A、数据是 B」
    assert "不完整" in Path(mp).read_text(encoding="utf-8")


def test_bundle_schema_roundtrips_through_json():
    b = build_bundle(task(), changes=[FileChange(path="a.py", kind="modify",
                                                 added=3, removed=1)])
    d = b.to_dict()
    assert d["changes"][0]["path"] == "a.py"
    assert set(SYNC_FIELDS) <= set(d), "同步段字段未全部序列化"


# ── 接线：必须能经真实 load_capabilities 挂上（规范 §3.4）──────

def test_report_capability_is_registered_and_off_by_default():
    """A1 必须在工厂表里，且**默认关**。

    默认关的理由是它**写盘**（`.necessity/bundles/`），而 I1 的验收要求是
    「未启用时宿主行为逐字节一致」。有副作用的组件不能默认开。
    """
    from backend.necessity.capability import DEFAULT_ENABLED, FACTORY_NAMES, load_capabilities

    assert "report" in FACTORY_NAMES, "A1 未注册工厂，load_capabilities 会静默跳过"
    assert DEFAULT_ENABLED.get("report") is False, "A1 不该默认开（会写盘）"
    assert "report" not in load_capabilities({}), "默认配置下 A1 被挂上了"


def test_report_capability_builds_through_real_loader():
    """显式开启时，`load_capabilities` 必须真的能构造出实例。

    这个断言防的是本仓库踩过两次的坑：模块存在但没在 `__init__.py`
    导出工厂 —— `load_capabilities` 只在日志里留一行 warning 就跳过，
    表现为「配了但没生效」。
    """
    from backend.necessity.capability import load_capabilities

    caps = load_capabilities({"enabled": False,
                              "evidence": {"enabled": True, "write": False}})
    assert "report" in caps, "A1 未能经真实装配路径挂载"


def test_on_task_end_returns_metrics_and_keeps_full_bundle():
    """`on_task_end` 返回**指标**，完整证据包另存 —— 不把整份报告塞进指标出口。"""
    from backend.necessity.capability import CompositeHooks, load_capabilities

    caps = load_capabilities({"enabled": False,
                              "evidence": {"enabled": True, "write": False}})
    rep = caps["report"]
    rep.set_changes([FileChange(path="x.py", kind="modify", added=3, removed=1)])
    out = CompositeHooks(caps).on_task_end(task("wire"))

    assert "report" in out
    metrics = out["report"]
    assert metrics["bundle_status"] == "partial"
    assert metrics["bundle_fields_filled"] == 1.0
    # 完整包在能力实例上可取
    assert rep.last_bundle is not None
    assert rep.last_bundle.changes[0].path == "x.py"


def test_factory_failure_does_not_break_assembly(tmp_path):
    """工厂抛异常时装配必须继续（结构性降级），不能拖垮整个挂载。"""
    from backend.necessity.capability import load_capabilities

    # 给一个 workspace 指向不存在的路径，落盘会失败但不应影响构造
    caps = load_capabilities({"enabled": False,
                              "evidence": {"enabled": True,
                                           "workspace": str(tmp_path / "nope"),
                                           "write": False}})
    assert "report" in caps


# ── 预算（规范 §1.7）：A1 异步段是该受限的那一处 ───────────────

def test_enrich_respects_budget_by_elapsed_time():
    """耗时超预算时**不采纳结果**，保持 partial。

    A1 的异步段要跑上百次测试（reduce 明确说了不能在主循环），是最该受限
    的一处。`Budget.enforce()` 的空调用只看**已用量**，无法表达「这次要花
    多少」—— 所以真正的把关必须是**后置**（用真实耗时检查）。
    实测踩过：只做前置检查时，`max_tokens=0` 这类紧预算永远不触发。
    """
    import time

    from backend.necessity.gate.budget import Budget

    class _Res:
        hunks = [type("H", (), {"id": "h1", "file": "a.py"})()]

    b = build_bundle(task("budget"), changes=[])
    tight = Budget(max_wall_time_ms=1, on_exceed="degrade")
    enrich_bundle(b, necessity_runner=lambda: (time.sleep(0.02), _Res())[1],
                  budget=tight)
    assert b.status == "partial", "超出预算却采纳了结果"
    assert b.necessity == [], "超预算仍写入了必要性数据"
    assert any("耗时" in e and "预算" in e for e in b.collection_errors)


def test_enrich_within_budget_completes():
    """预算充足时正常完成 —— 预算不该把正常工作挡掉。"""
    from backend.necessity.gate.budget import Budget

    class _Res:
        hunks = [type("H", (), {"id": "h1", "file": "a.py"})()]

    b = build_bundle(task("ok"), changes=[])
    enrich_bundle(b, necessity_runner=lambda: _Res(),
                  budget=Budget(max_wall_time_ms=60_000))
    assert b.status == "complete"


def test_default_budget_is_not_shared_between_calls():
    """默认预算必须每次**新建**。

    `Budget` 是有状态的（累计 used / 记录起始时间）。共享同一个实例会让
    多次调用的用量累加 —— 第二次调用必然「超限」，而且是莫名其妙地超。
    """
    from backend.necessity.report._budget import default_budget_for

    a = default_budget_for("A1")
    b = default_budget_for("A1")
    assert a is not b, "默认预算被共享了"
    a.record(tokens=999999)
    assert b.used_tokens == 0, "新取的预算带上了别人的用量"


def test_unknown_capability_yields_no_budget():
    """未声明的能力不强制预算（返回 None），由调用方自行决定。"""
    from backend.necessity.report._budget import default_budget_for

    assert default_budget_for("NOPE") is None
