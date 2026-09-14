"""验收测试判定回归 —— 锁定 pytest 退出码与真实观测语义。"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.necessity.eval.tasks.baseline import check_baseline  # noqa: E402
from backend.necessity.eval.tasks.loader import load_task  # noqa: E402
from backend.necessity.eval.verify import (  # noqa: E402
    _parse_counts,
    classify,
    verify,
    verify_workdir,
)

TASKS_DIR = ROOT / "backend" / "necessity" / "eval" / "tasks"


def _make_task(tmp_path: Path, *, test_body: str = "assert f() == 1\n"):
    repo = tmp_path / "repo"
    tests = tmp_path / "tests"
    repo.mkdir()
    tests.mkdir()
    (repo / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tests / "test_mod.py").write_text(
        "from mod import f\n\n\ndef test_f():\n    " + test_body,
        encoding="utf-8",
    )
    return repo, tests


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def test_parse_counts_uses_collected_line_and_last_counter_match():
    """真实 pytest 输出中各计数取最后一次，collected 行必须单独解析。"""
    output = (
        "============================= test session starts =============================\n"
        "collected 6 items\n"
        "1 failed, 1 error, 2 passed in 0.02s\n"
        "short test summary: 2 failed, 3 errors, 4 passed in 0.03s\n"
    )

    assert _parse_counts(output) == {
        "passed": 4,
        "failed": 2,
        "errors": 3,
        "collected": 6,
    }


def test_collection_error_is_error_even_with_failed_and_error_counts():
    """退出码 2 代表本轮未被有效观测，不能因有失败计数而算 fail。"""
    output = "collected 2 items\n1 failed, 1 error in 0.01s\n"

    assert classify(2, output) == "error"


@pytest.mark.parametrize(
    ("returncode", "output", "expected"),
    [
        (0, "collected 1 item\n1 passed in 0.01s", "pass"),
        (0, "collected 0 items\nno tests ran", "error"),
        (1, "collected 1 item\n1 failed in 0.01s", "fail"),
        (1, "pytest output without a failure summary", "error"),
        (3, "internal error", "error"),
        (4, "usage error", "error"),
        (5, "collected 0 items\nno tests ran", "error"),
    ],
)
def test_classify_keeps_fail_for_observed_failures_only(
    returncode: int, output: str, expected: str
):
    """只有退出码 1 且确实有失败，才是「Agent 做错了」的有效 fail。"""
    assert classify(returncode, output) == expected


def test_verify_does_not_mutate_source_repo_or_tests(tmp_path):
    """repo/ 是不可污染的快照；验收测试必须在临时合并目录中执行。"""
    repo, tests = _make_task(tmp_path)
    before = (_tree_hash(repo), _tree_hash(tests))

    result = verify(repo, tests, timeout=30)

    assert result.status == "pass"
    assert (result.passed, result.collected) == (1, 1)
    assert (_tree_hash(repo), _tree_hash(tests)) == before


def test_verify_workdir_reports_missing_tests_without_raising(tmp_path):
    """工作现场缺 tests/ 是可诊断的 error，而不是让 runner 直接崩溃。"""
    result = verify_workdir(tmp_path)

    assert result.status == "error"
    assert "tests/" in result.output


def test_verify_timeout_becomes_error(tmp_path):
    """验收超时说明本轮未完成观测，必须记 error 而不是抛出异常。"""
    repo, tests = _make_task(tmp_path, test_body="import time\ntime.sleep(1)\n")

    result = verify(repo, tests, timeout=0.05)

    assert result.status == "error"
    assert "超时" in result.output


def test_verify_unexpected_exception_becomes_error(tmp_path):
    """复制或启动阶段的意外异常也要转成可诊断 error。"""
    repo, tests = _make_task(tmp_path)
    bad_copy_to = tmp_path / "not-a-directory"
    bad_copy_to.write_text("occupied", encoding="utf-8")

    result = verify(repo, tests, copy_to=bad_copy_to)

    assert result.status == "error"
    assert "FileExistsError" in result.output


def test_real_a02_pristine_snapshot_matches_baseline_reverse_precheck():
    """真实 A-02 基线必须失败，否则前置检查与终局判定就失去区分度。"""
    task_root = TASKS_DIR / "A-02-add-param"
    task = load_task(task_root)

    baseline = check_baseline(task, timeout=30)
    acceptance = verify(task.repo, task.tests, timeout=30)

    assert baseline.has_discrimination, baseline.diagnosis
    assert baseline.returncode == 1
    assert acceptance.status == "fail"
    assert acceptance.returncode == baseline.returncode
    assert acceptance.failed + acceptance.errors > 0
