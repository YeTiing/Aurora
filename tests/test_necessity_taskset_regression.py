"""任务集完整性回归 —— 锁死「静默漏文件」这个真实失败模式。

## 这个测试守护的两件事

**一、仓库里的真实任务集必须可用。**

本轮真事：Necessity 合并进 Aurora 时，`eval/tasks/*/` 下的 `task.md` 与
`meta.json` **一个都没带过来** —— 只有 `repo/` 和 `tests/` 被提交了。
后果是不报错的：

    load_all() 返回 4 个任务，但全部 invalid（spec.category 为空）
    → 配比检查报「缺少 A/B/C 类」
    → runner 照跑不误，跑出来的每一行都不可信

而当时的测试**全部通过** —— 因为 `test_necessity_eval_tasks.py` 只对
`tmp_path` 里现场生成的任务做断言，从不碰仓库里的那一份。
两个测试文件凑在一起，恰好漏掉了「文件有没有真的进来」这个断点。

**二、基线必须失败（EVAL.md §1.2 第 5 步）。**

基线就通过 = Agent 什么都不做也算成功 = 整批数字虚高。
它同样不报错，只是让两组看起来持平。所以必须变成断言。

## 为什么用 subprocess 而不是直接调库

pytest 进程里再起 pytest 会有 import 缓存/插件状态污染；
更重要的是测试要验的正是**子进程的真实退出码语义**
（1=有用例失败 vs 2=collection error vs 5=没收集到用例），
in-process 跑拿不到这个信号。
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.necessity.eval.tasks.baseline import (  # noqa: E402
    BaselineCheck,
    TasksetInvalid,
    _parse_counts,
    assert_discriminates,
    check_baseline,
    problems,
)
from backend.necessity.eval.tasks.loader import (  # noqa: E402
    check_distribution,
    load_all,
    summarize,
)

TASKS_DIR = ROOT / "backend" / "necessity" / "eval" / "tasks"


def test_tasks_dir_exists_and_is_nonempty():
    assert TASKS_DIR.is_dir(), f"任务集目录不存在: {TASKS_DIR}"
    assert load_all(TASKS_DIR), "任务集为空"


def test_committed_taskset_has_no_missing_files():
    """**核心回归**：每个任务目录的必需文件都在。

    上一轮就是在这里漏掉的 —— repo/ 和 tests/ 在，task.md/meta.json 不在，
    而没有任何断言发现它。
    """
    tasks = load_all(TASKS_DIR)
    broken = {t.root.name: t.problems for t in tasks if not t.ok}
    assert not broken, (
        "仓库里的任务集有结构问题（多半是合并时漏了文件）：\n"
        + "\n".join(f"  {k}: {v}" for k, v in broken.items())
    )


def test_committed_taskset_covers_all_three_categories():
    """A/B/C 三类都必须存在。

    A 类缺失时「graph 不比 grep 差」这条反向验收标准根本无法验
    —— 而它和 B 类的优势是**并列**的两条验收线，缺一不可。
    """
    tasks = load_all(TASKS_DIR)
    cats = {t.spec.category for t in tasks}
    missing = {"A", "B", "C"} - cats
    assert not missing, f"任务集缺少类别 {sorted(missing)}（现有 {sorted(cats)}）"


def test_committed_taskset_satisfies_distribution():
    """配比必须达标（B 类占一半以上）。"""
    tasks = load_all(TASKS_DIR)
    probs = check_distribution(tasks)
    assert not probs, "配比不达标：\n" + "\n".join(f"  {p}" for p in probs)


def test_b_tasks_declare_decoy_and_repo_really_has_it():
    """B 类的干扰符号必须**真的在仓库里**，而不只是 meta 里写了个名字。

    B-02 曾经在这里出过真问题：meta 声明 `Settings.reload`，
    但干扰文件里只有 `save` —— 干扰根本不成立，任务退化成 A 类。
    只查 meta 是查不出来的，必须落到 repo 里找定义。
    """
    import re

    tasks = [t for t in load_all(TASKS_DIR) if t.spec.category == "B"]
    assert tasks, "没有 B 类任务"
    for t in tasks:
        assert t.spec.decoy_symbols, f"{t.spec.task_id}: B 类未声明干扰符号"
        blob = "\n".join(
            p.read_text(encoding="utf-8", errors="replace")
            for p in t.repo.rglob("*.py")
        )
        for sym in t.spec.decoy_symbols:
            method = sym.split(".")[-1]
            assert re.search(rf"\bdef\s+{re.escape(method)}\s*\(", blob), (
                f"{t.spec.task_id}: 声明的干扰符号 {sym} 在 repo/ 里找不到定义 —— "
                "干扰不存在，这个任务退化成 A 类，无法产生主指标差距"
            )


def test_committed_taskset_baseline_is_green():
    """**第二条核心回归**：每个任务在原始状态下测试必须失败。

    这是 EVAL.md §1.2 第 5 步，也是最贵的一条（每个任务起一次 pytest）。
    """
    tasks = load_all(TASKS_DIR)
    checks = assert_discriminates(tasks, workers=4)
    assert len(checks) == len(tasks)
    for c in checks:
        assert c.has_discrimination, f"{c.task_id}: {c.diagnosis}"


# ── 判据本身的行为（不依赖真实任务集，跑得快）────────────────────

def test_parse_counts_reads_pytest_summary():
    out = "2 failed, 1 passed in 0.05s"
    assert _parse_counts(out) == {"passed": 1, "failed": 2, "errors": 0, "collected": 3}


def test_parse_counts_handles_collected_line():
    """`collected N items` 必须被解析 —— 它是识别「没收集到用例」的唯一依据。"""
    out = "collected 4 items\n\n2 failed, 2 passed in 0.1s"
    c = _parse_counts(out)
    assert c["collected"] == 4 and c["failed"] == 2


def test_discrimination_false_on_exit_2_even_with_error_count():
    """**退出码 2（collection error）不算有区分度。**

    这是判据最容易写错的地方：collection error 也有 failed/errors > 0，
    但它意味着**测试根本没跑起来**（本轮 B-02 的 ModuleNotFoundError 就是），
    不是「测试跑了且失败了」。若按 `failed > 0` 判定，坏任务会被判成合格。
    """
    c = BaselineCheck(task_id="x", checked=True, returncode=2,
                      errors=1, collected=1)
    assert c.has_discrimination is False
    assert "被中断" in c.diagnosis


def test_discrimination_false_when_nothing_collected():
    """退出码 5（没收集到用例）是**零区分度**，绝不能当成合格。"""
    c = BaselineCheck(task_id="x", checked=True, returncode=5, collected=0)
    assert c.has_discrimination is False


def test_discrimination_true_only_on_exit_1_with_failures():
    c = BaselineCheck(task_id="x", checked=True, returncode=1,
                      failed=1, passed=1, collected=2)
    assert c.has_discrimination is True


def test_discrimination_false_when_all_green():
    """基线全绿 = 任务没有区分度（最需要抓的情形）。"""
    c = BaselineCheck(task_id="x", checked=True, returncode=0,
                      passed=2, collected=2)
    assert c.has_discrimination is False
    assert "没有区分度" in c.diagnosis


def test_problems_lists_only_bad_checks():
    good = BaselineCheck(task_id="ok", checked=True, returncode=1,
                         failed=1, collected=1)
    bad = BaselineCheck(task_id="bad", checked=True, returncode=0,
                        passed=1, collected=1)
    out = problems([good, bad])
    assert len(out) == 1 and "bad" in out[0]


# ── 反向前置检查的端到端行为（用临时任务，不碰真实集）────────────

def _mk_task(tmp_path: Path, name: str, test_body: str, module_body: str = "def f():\n    return 1\n"):
    root = tmp_path / name
    (root / "repo").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "repo" / "mod.py").write_text(module_body, encoding="utf-8")
    (root / "tests" / "test_x.py").write_text(test_body, encoding="utf-8")
    (root / "task.md").write_text("任务：改点什么。", encoding="utf-8")
    import json
    (root / "meta.json").write_text(
        json.dumps({"task_id": name, "category": "A"}), encoding="utf-8")
    return root


def test_check_baseline_detects_failing_task(tmp_path):
    root = _mk_task(tmp_path, "t-fails", "from mod import f\n\n\ndef test_x():\n    assert f() == 2\n")
    from backend.necessity.eval.tasks.loader import load_task

    c = check_baseline(load_task(root))
    assert c.has_discrimination, c.diagnosis


def test_check_baseline_flags_task_with_no_discrimination(tmp_path):
    """**假任务的识别**：测试断言的是当前行为 → 基线就通过 → 无区分度。"""
    root = _mk_task(tmp_path, "t-green", "from mod import f\n\n\ndef test_x():\n    assert f() == 1\n")
    from backend.necessity.eval.tasks.loader import load_task

    c = check_baseline(load_task(root))
    assert not c.has_discrimination
    assert c.returncode == 0


def test_assert_discriminates_raises_on_bad_task(tmp_path):
    """不合格时必须**抛异常中止整批**，而不是记一条记录混过去。"""
    root = _mk_task(tmp_path, "t-green", "from mod import f\n\n\ndef test_x():\n    assert f() == 1\n")
    from backend.necessity.eval.tasks.loader import load_task

    with pytest.raises(TasksetInvalid) as ei:
        assert_discriminates([load_task(root)], workers=1)
    assert "t-green" in str(ei.value)
    assert ei.value.problems


def test_check_baseline_reports_missing_dirs(tmp_path):
    """结构缺失必须给出**可执行**的诊断，而不是一个空结果。"""
    from backend.necessity.eval.tasks.loader import load_task

    (tmp_path / "empty").mkdir()
    c = check_baseline(load_task(tmp_path / "empty"))
    assert c.checked is False
    assert "repo/" in c.error


def test_runner_load_tasks_enforces_check(tmp_path):
    """runner.load_tasks 必须默认执行反向前置检查（闭环在 runner 上）。

    这是「机制接进生产路径」的断言 —— 只实现不接线等于没实现。
    """
    from backend.necessity.eval.runner import EvalRunner

    root = _mk_task(tmp_path, "t-green", "from mod import f\n\n\ndef test_x():\n    assert f() == 1\n")
    # 另建一个目录，让 loader 只扫到这一个任务
    base = tmp_path / "tasks"
    base.mkdir()
    import shutil
    shutil.copytree(root, base / "t-green")

    r = EvalRunner(None, tmp_path / "out.jsonl", log=lambda *_: None)
    with pytest.raises(TasksetInvalid):
        r.load_tasks(base)


def test_runner_skips_check_when_disabled(tmp_path):
    """显式关掉时不该拦（续跑场景需要），但这是 caller 自担的选择。"""
    from backend.necessity.eval.runner import EvalRunner

    base = tmp_path / "tasks"
    base.mkdir()
    import shutil
    shutil.copytree(_mk_task(tmp_path, "t-green",
                             "from mod import f\n\n\ndef test_x():\n    assert f() == 1\n"),
                    base / "t-green")

    r = EvalRunner(None, tmp_path / "out.jsonl", check_baseline=False,
                   log=lambda *_: None)
    tasks = r.load_tasks(base)
    assert len(tasks) == 1
