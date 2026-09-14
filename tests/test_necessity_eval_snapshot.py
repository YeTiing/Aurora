"""快照与单次执行回归 —— 锁定隔离、基线 diff 和终局判定。"""
from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.necessity.eval.agents import Behavior, ScriptedAgent  # noqa: E402
from backend.necessity.eval.execute import run_once  # noqa: E402
from backend.necessity.eval.records import TaskSpec  # noqa: E402
from backend.necessity.eval.snapshot import (  # noqa: E402
    _attr_path,
    create,
    diff_of,
)


@dataclass
class FakeTask:
    spec: TaskSpec
    repo: Path
    tests: Path
    text: str = "请完成这个任务。"

    def task_text(self) -> str:
        return self.text


def make_task(tmp_path: Path, task_id: str = "t1") -> FakeTask:
    """造一个**结构真实**的任务快照，沿用 runner 测试的任务替身模式。"""
    root = tmp_path / task_id
    repo = root / "repo"
    tests = root / "tests"
    (repo / "src").mkdir(parents=True)
    tests.mkdir()
    (repo / "src" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "mod.py").write_text(
        "def value():\n    return 1\n", encoding="utf-8"
    )
    (tests / "test_mod.py").write_text(
        "from src.mod import value\n\n\ndef test_value():\n    assert value() == 1\n",
        encoding="utf-8",
    )
    return FakeTask(TaskSpec(task_id=task_id, category="A"), repo, tests)


def test_attr_path_rejects_missing_and_blank_attributes():
    """缺失/空属性不能变成 Path('.')，否则 copytree 会递归复制整个仓库。"""

    class TaskWithoutTests:
        pass

    task = TaskWithoutTests()
    assert _attr_path(task, "tests") is None
    task.tests = ""
    assert _attr_path(task, "tests") is None
    task.tests = "   "
    assert _attr_path(task, "tests") is None


def test_create_rejects_empty_repo_without_recursive_copy(tmp_path):
    """空 repo/ 必须立即失败，不能把临时目录当成输入递归复制。"""
    repo = tmp_path / "empty-repo"
    repo.mkdir()
    task = type("Task", (), {"repo": repo, "tests": None})()

    with pytest.raises(RuntimeError, match="没有任何 .py 文件"):
        create(task)


def test_create_merges_tests_with_repo_root_and_builds_pristine_commit(tmp_path):
    """tests/ 与 repo/ 必须合并到同一根，且 Agent 运行前已有非空基线提交。"""
    task = make_task(tmp_path)

    ws = create(task)
    try:
        assert ws.workdir == ws.repo_path
        assert (ws.workdir / "src" / "mod.py").is_file()
        assert (ws.workdir / "tests" / "test_mod.py").is_file()
        assert ws.pristine
        assert (ws.workdir / ".git").is_dir()
    finally:
        ws.cleanup()


def test_snapshot_mutation_does_not_change_source_bytes(tmp_path):
    """Agent 修改隔离目录后，仓库快照必须保持字节级不变。"""
    task = make_task(tmp_path)
    source_before = {
        path.relative_to(task.repo): path.read_bytes()
        for path in task.repo.rglob("*")
        if path.is_file()
    }

    ws = create(task)
    try:
        (ws.workdir / "src" / "mod.py").write_text(
            "def value():\n    return 999\n", encoding="utf-8"
        )
    finally:
        ws.cleanup()

    source_after = {
        path.relative_to(task.repo): path.read_bytes()
        for path in task.repo.rglob("*")
        if path.is_file()
    }
    assert source_after == source_before


def test_pristine_commit_exists_before_mutation_and_diff_names_file(tmp_path):
    """基线若延后到 Agent 之后，git diff HEAD 会为空；此处锁定正确时序。"""
    task = make_task(tmp_path)
    ws = create(task)
    try:
        assert ws.pristine
        target = ws.workdir / "src" / "mod.py"
        target.write_text(
            "def value():\n    return 2\n", encoding="utf-8"
        )

        diff = diff_of(ws.workdir)

        assert "src/mod.py" in diff
        assert "return 2" in diff
    finally:
        ws.cleanup()


def test_diff_of_preserves_chinese_utf8_content(tmp_path):
    """中文注释必须完整出现在 diff；UTF-8 解码不能被 Windows locale 吞掉。"""
    task = make_task(tmp_path)
    ws = create(task)
    try:
        target = ws.workdir / "src" / "mod.py"
        target.write_text(
            "def value():\n    # 中文回归注释\n    return 1\n", encoding="utf-8"
        )

        diff = diff_of(ws.workdir)

        assert "中文回归注释" in diff
    finally:
        ws.cleanup()


def test_cleanup_removes_workspace_and_keep_preserves_it(tmp_path):
    """正常结束删除现场，失败取证模式则保留并返回现场路径。"""
    first = create(make_task(tmp_path, "cleanup"))
    first_path = first.root
    first.cleanup()
    assert not first_path.exists()

    second = create(make_task(tmp_path, "keep"))
    second_path = Path(second.keep())
    second.cleanup()
    try:
        assert second_path == second.root
        assert second_path.exists()
    finally:
        # ⚠️ 不能写 `ignore_errors=True`：git 在 Windows 上把对象文件设为只读，
        # rmtree 会因此失败，而 ignore_errors 会**静默吞掉**它 ——
        # 于是测试自己成了泄漏源（实测每跑一轮 /tmp 就多一个 nsk-run-*）。
        # 复用生产代码里那个处理只读属性的回调。
        from backend.necessity.eval.snapshot import _remove_readonly
        shutil.rmtree(second_path, onerror=_remove_readonly)


def test_diff_of_non_git_directory_returns_empty_string(tmp_path):
    """没有 git 基线的目录没有可归因 diff，必须返回空串而不是抛异常。"""
    assert diff_of(tmp_path) == ""


@pytest.mark.parametrize("terminal_status", ["timeout", "error"])
def test_terminal_agent_status_cannot_be_overridden_by_passing_tests(
    tmp_path, terminal_status
):
    """timeout/error 表示 Agent 未获公平机会，验收测试通过也不能翻案。"""
    task = make_task(tmp_path, terminal_status)
    agent = ScriptedAgent({task.spec.task_id: Behavior(status=terminal_status)})

    result, verify_info = run_once(
        task,
        "A",
        0,
        agent,
        turn_limit=40,
        verify_timeout=30,
    )

    assert verify_info["status"] == "pass"
    assert result.status == terminal_status
    assert result.meta["verdict_source"] == "agent_terminal"


def test_non_terminal_agent_uses_acceptance_tests_as_verdict_source(tmp_path):
    """普通 Agent 结果必须由验收测试裁决，而不是沿用 Agent 自报状态。"""
    task = make_task(tmp_path, "acceptance")
    agent = ScriptedAgent({task.spec.task_id: Behavior(status="fail")})

    result, verify_info = run_once(
        task,
        "A",
        0,
        agent,
        turn_limit=40,
        verify_timeout=30,
    )

    assert verify_info["status"] == "pass"
    assert result.status == "pass"
    assert result.meta["verdict_source"] == "acceptance_tests"
