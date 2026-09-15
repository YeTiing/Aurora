"""A4 扩展供应链安全 —— 锁死对抗检验中发现的 4 个真实缺陷。

规格：`Aurora_六项能力设计规范.md` §6。
阈值：检出率 ≥ 90% / 误报率 ≤ 10% / 「无法判定」占比 ≤ 30% —— 规范 §6.7
明确说这是**安全类**指标，不允用基线放宽。

## 为什么不只用自造语料的 100%

规范 §6.11 自己警告过：「若 20 个样本都是自己写的明显恶意代码，
检出率会虚高」。实测确认了这一点：
    自造语料        20/20 = 100%
    对抗硬样本（不在语料里） 6/8 = 75%
所以本文件既跑语料，也跑**对抗样本** —— 后者才是有信息量的那个数字。
"""
import importlib
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ⚠️ 不能用 `from backend.necessity.supply import scan`：
# `supply/__init__.py` 同时导出子模块 `scan` 与同名函数 `scan`，后者覆盖前者。
_SCAN = importlib.import_module("backend.necessity.supply.scan")
_GATE = importlib.import_module("backend.necessity.supply.gate")
_MANIFEST = importlib.import_module("backend.necessity.supply.manifest")
_FP = importlib.import_module("backend.necessity.supply.fingerprint")

CORPUS = ROOT / "tests" / "fixtures" / "supply_corpus"


def as_extension(files: dict[str, str]) -> Path:
    """把若干文件写成一个扩展目录（扫描器的真实输入形态）。

    扫描器要的是**目录**：直接传单个 .py 文件会被判「不是扩展目录」并返回
    `scan_error`（实测把这个当成 100% 误报，其实是调用方式错了）。
    """
    d = Path(tempfile.mkdtemp(prefix="nsk-ext-"))
    for name, body in files.items():
        p = d / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return d


def caught(path: Path) -> bool:
    """是否会被门拦下（decision 非 allow）。

    用**准入决策**而不是「有没有高危 finding」作为检出判据：
    `unpinned_dependency` 是 medium，但它会让 decision 变成 restricted ——
    从「这套扩展能不能直接放进来」的角度，那已经是检出了。
    """
    return _GATE.admit("t", str(path)).decision != "allow"


# ── 缺陷 1：依赖清单文件名必须按模式匹配 ─────────────────────────

@pytest.mark.parametrize("name,expect", [
    ("requirements.txt", "pip"),
    ("requirements-dev.txt", "pip"),
    ("m18_requirements.txt", "pip"),      # 带前缀（实测漏过）
    ("dev-requirements.txt", "pip"),
    ("requirement.txt", "pip"),           # 单数
    ("constraints.txt", "pip"),
    ("package.json", "npm"),
    ("package-lock.json", "npm"),
    ("m19_package.json", "npm"),          # 带前缀（实测漏过）
    ("README.md", ""),
    ("pkg_scripts.json", ""),             # 不是 package.json，不该认
])
def test_dependency_manifest_dispatches_by_pattern(name, expect):
    """必须认「像清单的文件名」，不能要求严格等于某个名字。

    实测：语料的 `m18_requirements.txt` 因为带前缀而**完全没被检查**，
    而真实扩展包的清单名千奇百怪。要求严格相等等于把「换了个名字」
    变成「扫不到」，用户会读成「这套依赖没问题」。
    """
    assert _SCAN._dep_kind(Path(name)) == expect


# ── 缺陷 2：锁定版本被误判成未锁定 ──────────────────────────────

def test_correctly_pinned_requirement_is_not_flagged():
    """`requests==2.31.0` 是**正确**的锁定写法，不得报「未固定」。

    ⚠️ 原判据写成 `==[0-9]+(?:\\.[0-9]+){2}`，要求**三段**点分数字，
    于是 `2.31.0`（两段）被误判成未固定 —— 把做对了的清单报成风险。
    用户一旦发现误报就会一律放行，**比漏报更危险**。
    """
    d = as_extension({"requirements.txt": "requests==2.31.0\nflask==1.0\n"})
    assert not caught(d), "正确锁定的清单被判成了风险"


@pytest.mark.parametrize("line", [
    "flask\n",            # 完全无版本
    "requests>=2.0\n",    # 浮动
    "pkg~=1.2\n",         # 兼容版本，仍非精确锁
])
def test_unpinned_requirements_are_flagged(line):
    d = as_extension({"requirements.txt": line})
    assert caught(d), f"{line.strip()!r} 未固定版本却放行了"


# ── 缺陷 3：npm 安装钩子完全没检查 ──────────────────────────────

def test_npm_install_hook_is_flagged():
    """`postinstall` 会在**安装时自动执行**，用户还没运行任何东西。

    这是把任意命令送进用户机器的经典通道（规范 §6.5「远程脚本直接执行」
    的真实形态）。只看 `dependencies` 会把这一类完全漏掉 —— 对抗检验实测。
    """
    d = as_extension({"package.json": (
        '{"name":"x","scripts":{"postinstall":"node setup.js"},'
        '"dependencies":{"lodash":"4.17.21"}}')})
    rules = {f.rule_id for f in _SCAN.scan_extension(d)}
    assert "install_script" in rules, "安装钩子未告警"
    assert caught(d)


def test_normal_npm_test_script_is_not_flagged():
    """`test` / `build` 这类脚本不是安装钩子，不该告警（避免误报）。"""
    d = as_extension({"package.json": (
        '{"scripts":{"test":"jest","build":"tsc"},'
        '"dependencies":{"lodash":"4.17.21"}}')})
    assert "install_script" not in {f.rule_id for f in _SCAN.scan_extension(d)}


# ── 缺陷 4：权限清单不参与准入决策（最严重）──────────────────────

def _manifest(**kw) -> object:
    m = _MANIFEST.PermissionManifest(*(["not_detected"] * 8))
    for k, v in kw.items():
        setattr(m, k, v)
    return m


def test_permission_manifest_alone_can_block_admission():
    """**findings 干净时，权限清单必须能独立拦下高危扩展。**

    ⚠️ 此前 `_decision(level, unknown_ratio)` 只读 findings，权限清单算出来
    却**完全不参与决策**。实测：一个读了敏感环境变量的扩展，findings 里
    没有对应规则，于是拿到 `allow`。清单成了给人看的装饰，而结论是
    「可以放」—— 安全能力里最危险的失效：报告说「有风险」，结论说「放行」。
    """
    assert _GATE._decision("low", 0.0, _manifest(reads_env="required")) == "ask", \
        "清单读到凭据却仍然放行"
    # 对照：清单也干净时才 allow
    assert _GATE._decision("low", 0.0,
                           _MANIFEST.PermissionManifest(*(["not_detected"] * 8))) == "allow"


def test_manifest_combo_escalates_but_single_capability_does_not():
    """执行 / 网络单独出现是**日常能力**，组合才是外传通道。

    规范 §5.1：「全问 → 用户被淹没，养成无脑点同意的习惯，**比不问更危险**」。
    实测：把 `executes_shell` / `accesses_network` 任一命中就 ask，会拦掉
    **8/20 个正常样本**（固定 argv 的 subprocess、本地 socket 都是无害用法）。
    所以这两项必须**组合**才升级；而凭据/远程/库写这类强信号单项即可。
    """
    assert _GATE._decision("low", 0.0, _manifest(executes_shell="required")) == "allow"
    assert _GATE._decision("low", 0.0, _manifest(accesses_network="required")) == "allow"
    assert _GATE._decision(
        "low", 0.0,
        _manifest(executes_shell="required", accesses_network="required")) == "ask"


def test_benign_local_extension_is_allowed():
    """纯本地计算不得被拦 —— 否则门形同虚设，用户会一律点同意。"""
    d = as_extension({"mod.py": "def add(a, b):\n    return a + b\n"})
    rep = _GATE.admit("t", str(d))
    assert rep.decision == "allow", f"过严: {rep.decision} {rep.level}"


# ── Tri 三态：不确定不能说成「不需要」──────────────────────────

def test_manifest_uses_undetermined_for_unreadable_extension():
    """无法读取时 8 项全部 `undetermined`，不得降级成 `not_detected`。

    规范原话：「把不确定说成『不需要』是安全设计里最危险的做法」。
    """
    m = _MANIFEST.build_manifest(Path("does-not-exist-xyz"))
    vals = {m.reads_workspace, m.writes_files, m.executes_shell, m.accesses_network,
            m.reads_env, m.installs_deps, m.calls_remote_mcp, m.writes_database}
    assert vals == {"undetermined"}, f"缺失路径未全部标 undetermined: {vals}"


# ── 指纹：任一文件变化都必须触发重新审批 ─────────────────────────

def test_fingerprint_changes_on_any_file_modification():
    d = as_extension({"a.py": "x = 1\n", "b.py": "y = 2\n"})
    before = _FP.fingerprint(d)
    (d / "b.py").write_text("y = 3\n", encoding="utf-8")
    assert _FP.fingerprint(d) != before, "改了文件但指纹没变"


def test_changed_fingerprint_forces_ask():
    """指纹变化必须让 allow 回到审批路径（规范 §6.7 硬要求）。"""
    d = as_extension({"a.py": "x = 1\n"})
    first = _GATE.admit("t", str(d))
    (d / "a.py").write_text("x = 2\n", encoding="utf-8")
    again = _GATE.admit("t", str(d), previous_fingerprint=first.fingerprint)
    assert again.decision in ("ask", "restricted", "reject"), \
        f"指纹变了却没回到审批: {again.decision}"


# ── 语料指标（回归护栏）────────────────────────────────────────

def _rate(sub: str) -> tuple[int, int]:
    files = sorted((CORPUS / sub).glob("*"))
    n = sum(1 for p in files if caught(as_extension({p.name: p.read_text(encoding="utf-8")})))
    return n, len(files)


def test_corpus_detection_rate_meets_spec():
    """语料检出率 ≥ 90%（规范 §6.7，安全类不得放宽）。

    断言**区间**而非具体值：具体值会随语料演进而变，
    但「不能跌破规范下限」是硬要求。
    """
    hit, total = _rate("malicious")
    assert hit / total >= 0.90, f"检出率 {hit}/{total} 低于规范下限 90%"


def test_corpus_false_positive_rate_meets_spec():
    """语料误报率 ≤ 10%（规范 §6.7）。"""
    fp, total = _rate("benign")
    assert fp / total <= 0.10, f"误报率 {fp}/{total} 超过规范上限 10%"


def test_corpus_has_no_trivially_invalid_malicious_sample():
    """恶意样本必须真的含恶意内容。

    实测缺陷：`m11_fixed_argv_then_delete.py` 的内容与一个 benign 样本
    **完全相同**（只有 `git status`）。一个不含恶意的「恶意样本」
    会让检出率虚低，而一个必然被命中的样本会让它虚高 —— 两者都会
    让指标失去意义。
    """
    benign_bodies = {p.read_text(encoding="utf-8").strip()
                     for p in (CORPUS / "benign").glob("*")}
    dupes = [p.name for p in (CORPUS / "malicious").glob("*")
             if p.read_text(encoding="utf-8").strip() in benign_bodies]
    assert not dupes, f"这些恶意样本与正常样本内容相同，不构成对抗：{dupes}"


# ── 准入钩子（规范 §6.2 的「入口门」）──────────────────────────

def test_admission_defaults_to_observe_and_never_blocks():
    """默认模式必须是 observe —— 只记录、**永不阻断**。

    依据规范 §6.8：「检出率 < 90% → 默认关闭，仅作参考提示」。
    当前语料实测 100%/0%，但语料是自造的（§6.11 警告过），
    所以在真实语料验证之前不应该拦人 —— 拦住工作比漏报更糟。
    """
    from backend.necessity.supply import admission as A

    A.reset()
    d = as_extension({"x.py": "import os\nk = os.environ['VENDOR_API_KEY']\n"})
    h = A.AdmissionHook(mode="observe", log_path=str(d.parent / "log.jsonl"))
    r = h.check("evil", d)
    assert r.allowed is True, "observe 模式不该阻断"
    assert h.stats()["mode"] == "observe"


def test_admission_off_mode_does_not_even_scan():
    from backend.necessity.supply import admission as A

    A.reset()
    h = A.AdmissionHook(mode="off", log_path=str(as_extension({"a.py": "x=1\n"}).parent / "l.jsonl"))
    r = h.check("any", as_extension({"a.py": "x=1\n"}))
    assert r.allowed is True and h.scanned == 0


def test_admission_enforce_blocks_risky_extension():
    from backend.necessity.supply import admission as A

    A.reset()
    d = as_extension({"x.py": "import os\nk = os.environ['VENDOR_API_KEY']\n"})
    h = A.AdmissionHook(mode="enforce", log_path=str(d.parent / "log.jsonl"))
    assert h.check("evil", d).allowed is False


def test_unreadable_extension_is_allowed_not_blocked():
    """**「扫不到」不是「有风险」。**

    实测踩过：enforce 模式下不存在的路径被判 `scan_error`(high) → ask → 拒绝，
    而加载器会因为一个不存在的目录崩掉 —— 把**未知**当成了**危险**。
    这与 `Tri` 三态的设计原则一致（规范 §6.5：把不确定说成「不需要」/
    「不安全」同样危险）。
    """
    from backend.necessity.supply import admission as A

    A.reset()
    h = A.AdmissionHook(mode="enforce",
                        log_path=str(Path(tempfile.mkdtemp()) / "log.jsonl"))
    r = h.check("ghost", "/definitely/not/a/real/path")
    assert r.allowed is True, "无法读取被判成了危险"
    assert "未知" in r.reason


def test_admission_failure_never_breaks_loading():
    """准入自身故障必须放行（契约 2：质量组件不该让 Aurora 起不来）。"""
    from backend.necessity.supply import admission as A

    A.reset()

    class Boom:
        def check(self, *a, **k):
            raise RuntimeError("boom")

    A.set_hook(Boom())
    r = A.check_extension("x", "/whatever")
    assert r.allowed is True
    A.reset()


def test_admission_writes_observations_to_log(tmp_path):
    """observe 模式的产出是**判定分布** —— 那是阶段 A 标定的输入（§0.3）。"""
    from backend.necessity.supply import admission as A

    A.reset()
    log = tmp_path / "log.jsonl"
    h = A.AdmissionHook(mode="observe", log_path=str(log))
    h.check("a", as_extension({"a.py": "x=1\n"}))
    assert log.is_file() and log.read_text(encoding="utf-8").strip()


# ── 接线：加载器必须真的调用准入 ───────────────────────────────

def test_skills_loader_calls_admission(monkeypatch, tmp_path):
    """`SkillsManager._scan` 必须经过准入门（默认 observe 所以不改变行为）。

    只断言函数存在不够 —— 这条测的是**真的接上了**：
    用一个记录调用的假钩子，确认加载器在发现 SKILL.md 时问了准入。
    """
    from backend.necessity.supply import admission as A

    calls = []

    class Spy:
        def check(self, ext_id, path, description=""):
            calls.append(ext_id)
            return A.AdmissionResult(extension_id=ext_id, allowed=True)

    A.reset()
    A.set_hook(Spy())
    try:
        skill_root = tmp_path / "skills"
        d = skill_root / "demo_skill"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            "---\nname: demo_skill\ndescription: t\n---\n\nbody\n", encoding="utf-8")

        from backend.skills import SkillManager
        SkillManager(skill_roots=[str(skill_root)])._scan()
        assert "demo_skill" in calls, "技能加载器没有调用准入钩子"
    finally:
        A.reset()


def test_plugins_loader_calls_admission(monkeypatch, tmp_path):
    """`PluginManager.discover` 同样必须经过准入门。"""
    from backend.necessity.supply import admission as A

    calls = []

    class Spy:
        def check(self, ext_id, path, description=""):
            calls.append(ext_id)
            return A.AdmissionResult(extension_id=ext_id, allowed=True)

    A.reset()
    A.set_hook(Spy())
    try:
        root = tmp_path / "plugins"
        d = root / "myplugin"
        (d / ".codex-plugin").mkdir(parents=True)
        (d / ".codex-plugin" / "plugin.json").write_text(
            '{"interface": {"displayName": "My", "shortDescription": "s"}}',
            encoding="utf-8")

        from backend.plugins import PluginManager
        PluginManager(plugin_dirs=[str(root)]).discover()
        assert "myplugin" in calls, "插件加载器没有调用准入钩子"
    finally:
        A.reset()
