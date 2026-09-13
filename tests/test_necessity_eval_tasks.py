"""任务集测试 —— 校验 / 泄露检测 / 配比 / 区分性。

最重要的一条是 EVAL.md §1.2 第 5 步的**反向前置检查**：
    「必须验证 repo/ 原始状态下这些测试是**失败**的，
      否则这个任务没有区分度。」
如果基线就通过，Agent 什么都不做也算成功 —— 那整个评测跑出来的数字
全是虚的。本测试把它变成自动化断言。
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.necessity.eval.records import TaskSpec  # noqa: E402
from backend.necessity.eval.tasks.generator import (  # noqa: E402
    generate_starter_set,
    make_redundancy_trap_task,
    make_same_name_task,
    make_scope_trap_task,
)
from backend.necessity.eval.tasks.loader import (  # noqa: E402
    check_distribution,
    check_task_md_leaks,
    load_all,
    load_task,
    summarize,
)


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    """生成一份入门任务集（模块级，避免重复生成拖慢测试）。"""
    base = tmp_path_factory.mktemp("tasks")
    generate_starter_set(base, clean=True)
    return base


# ── TaskSpec 校验 ────────────────────────────────────────────────

def test_spec_accepts_valid_b_category():
    s = TaskSpec(task_id="B-1", category="B", decoy_symbols=["Settings.save"])
    assert s.validate() == []


def test_spec_rejects_b_without_decoy():
    """B 类必须声明干扰符号 —— 文档称这是实验能否成立的关键。"""
    s = TaskSpec(task_id="B-1", category="B")
    problems = s.validate()
    assert any("decoy_symbols" in p for p in problems)


def test_spec_rejects_c_without_constraints():
    s = TaskSpec(task_id="C-1", category="C")
    problems = s.validate()
    assert any("constraints" in p for p in problems)


def test_spec_rejects_bad_category():
    assert any("category" in p for p in TaskSpec(task_id="X", category="Z").validate())


def test_spec_roundtrip():
    s = TaskSpec(task_id="t", category="B", decoy_symbols=["a.b"], constraints=["c"],
                 title="x", difficulty="hard")
    assert TaskSpec.from_dict(s.to_dict()).to_dict() == s.to_dict()


# ── 泄露检测（§1.2 第 4 步）──────────────────────────────────────

def test_leak_detector_catches_line_numbers():
    assert check_task_md_leaks("在 parser.py 第 45 行加上判断")
    assert check_task_md_leaks("modify utils.py:120")


def test_leak_detector_catches_code_snippets():
    assert check_task_md_leaks("加上 `if not x: return None`")


def test_leak_detector_allows_goal_only_description():
    """文档给的好例子：只描述目标，不给做法 —— 必须放行。"""
    good = ("现在 X 函数在遇到 Y 情况时会抛出 Z 异常，改为返回 None，"
            "并同步更新所有调用方。")
    assert check_task_md_leaks(good) == []


# ── 生成器 ───────────────────────────────────────────────────────

def test_generate_starter_set_creates_required_structure(generated):
    tasks = load_all(generated)
    assert len(tasks) == 4
    for t in tasks:
        assert t.repo.is_dir()
        assert t.tests.is_dir()
        assert t.meta_path.exists()
        assert t.ok, f"{t.spec.task_id} 有问题: {t.problems}"


def test_generate_marks_clean_removes_old(tmp_path):
    base = tmp_path / "tasks"
    (base / "stale").mkdir(parents=True)
    (base / "stale" / "x").write_text("x")
    generate_starter_set(base, clean=True)
    assert not (base / "stale").exists()


def test_same_name_task_creates_real_decoy(generated):
    """B 类任务必须有真实存在的同名干扰符号，而不只是 meta 里声明。"""
    repo = generated / "B-01-same-name-save" / "repo"
    decoy = (repo / "config/settings.py").read_text(encoding="utf-8")
    target = (repo / "models/user.py").read_text(encoding="utf-8")
    assert "def save(" in decoy, "干扰文件里没有同名方法"
    assert "def save(" in target, "目标文件里没有方法"


def test_scope_trap_has_constraint_in_meta(generated):
    m = json.loads((generated / "C-03-scope-trap" / "meta.json").read_text(encoding="utf-8"))
    assert m["constraints"], "诱导越界任务必须声明约束"
    assert "src/parser" in m["constraints"][0]


def test_redundancy_trap_embeds_temptations(generated):
    """高冗余任务必须真的埋了"顺手改动"的诱惑。"""
    calc = (generated / "C-04-redundancy-trap" / "repo" / "calc.py").read_text(encoding="utf-8")
    assert "TODO" in calc
    assert "import os" in calc, "缺未使用 import 这个陷阱"
    assert "import sys" in calc


def test_generator_git_repo_initialized(generated):
    """Diff Reducer 的 worktree 需要 git —— 生成器应建好初始 commit。"""
    repo = generated / "B-01-same-name-save" / "repo"
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                       capture_output=True, text=True)
    assert r.returncode == 0, "仓库未初始化 git"


# ── 反向前置检查：基线必须失败（§1.2 第 5 步）════════════════════

@pytest.mark.parametrize("task_id", [
    "B-01-same-name-save",
    "B-02-same-name-reload",
    "C-03-scope-trap",
    "C-04-redundancy-trap",
])
def test_tests_fail_at_baseline(generated, task_id, tmp_path):
    """**这是任务集最重要的不变量。**

    基线就通过 = 任务没有区分度 = Agent 什么都不做也算成功 = 评测数字全虚。
    """
    import shutil

    t = load_task(generated / task_id)
    work = tmp_path / "work"
    shutil.copytree(t.repo, work)
    shutil.copytree(t.tests, work / "tests", dirs_exist_ok=True)

    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests/", "-p", "no:cacheprovider"],
        cwd=work, capture_output=True, text=True, timeout=180,
        encoding="utf-8", errors="replace",
    )
    assert r.returncode != 0, (
        f"{task_id} 在基线状态下测试通过 —— 该任务没有区分度，"
        "会让 Agent 不干活也算成功"
    )


def test_b_category_decoy_test_still_passes_at_baseline(generated, tmp_path):
    """B 类的关键性质：**目标**的测试失败，但**干扰符号**的测试通过。

    这意味着「改错对象」会立刻被测试发现 —— 这正是区分性的来源。
    """
    import shutil

    t = load_task(generated / "B-01-same-name-save")
    work = tmp_path / "work"
    shutil.copytree(t.repo, work)
    shutil.copytree(t.tests, work / "tests", dirs_exist_ok=True)

    r = subprocess.run(
        # -rA 输出每个用例的 PASSED/FAILED —— 默认 -q 只列失败的，
        # 无法断言『干扰测试通过了』
        [sys.executable, "-m", "pytest", "-q", "tests/", "-p", "no:cacheprovider", "-rA"],
        cwd=work, capture_output=True, text=True, timeout=180,
        encoding="utf-8", errors="replace",
    )
    out = (r.stdout or "") + (r.stderr or "")
    # 目标测试失败、干扰测试通过 —— 两者必须同时成立
    assert "test_user_save_behavior" in out
    assert "test_settings_save_untouched" in out
    assert "1 failed, 1 passed" in out, "预期 1失败1通过"
    assert "PASSED" in out and "FAILED" in out, "缺 PASSED/FAILED 明细"


# ── 配比校验 ─────────────────────────────────────────────────────

def test_distribution_flags_b_under_half(generated):
    """B 类不足一半应被报出来（文档要求 B 占一半以上）。"""
    tasks = load_all(generated)
    problems = check_distribution(tasks)
    # 入门集是 2B+2C（B 恰好一半），A 类缺失应被报
    assert any("A 类" in p for p in problems), f"应报缺 A 类: {problems}"


def test_distribution_flags_empty_set():
    assert check_distribution([]) == ["任务集为空"]


def test_summarize_shape(generated):
    s = summarize(load_all(generated))
    assert s["total"] == 4
    assert s["by_category"] == {"B": 2, "C": 2}
    assert s["with_decoy"] == 2
    assert s["with_constraints"] >= 1
    assert s["invalid"] == []


# ── loader 容错 ──────────────────────────────────────────────────

def test_loader_reports_missing_files_without_raising(tmp_path):
    bad = tmp_path / "broken"
    bad.mkdir()
    t = load_task(bad)
    assert not t.ok
    assert any("meta.json" in p for p in t.problems)
    assert any("repo" in p for p in t.problems)


def test_loader_falls_back_to_dirname_for_missing_task_id(tmp_path):
    d = tmp_path / "no-id-task"
    (d / "repo").mkdir(parents=True)
    (d / "tests").mkdir()
    (d / "task.md").write_text("do something")
    (d / "meta.json").write_text(json.dumps({"category": "A"}))
    t = load_task(d)
    assert t.spec.task_id == "no-id-task"


def test_loader_skips_underscore_dirs(tmp_path):
    base = tmp_path / "tasks"
    (base / "_templates").mkdir(parents=True)
    (base / "real").mkdir()
    (base / "real" / "repo").mkdir()
    (base / "real" / "tests").mkdir()
    (base / "real" / "task.md").write_text("x")
    (base / "real" / "meta.json").write_text(json.dumps({"task_id": "real", "category": "A"}))
    names = [t.spec.task_id for t in load_all(base)]
    assert names == ["real"]
