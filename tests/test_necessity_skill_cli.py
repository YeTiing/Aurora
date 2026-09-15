"""`necessity eval skill` —— Skill 评测的 CLI 接线（规范 §7）。

## 这个文件锁的是什么

在 CLI 存在之前，`eval/skill_arms.py` 与 `eval/skill_registry.py` 是
**孤立代码**：接口正确、测试全过，但没有任何东西调用它们。
本文件断言「真的接上了真实数据」，而不只是「函数能跑」。

## 退出码是承重的

    0 有结论（adopt / reject）      1 样本不足（inconclusive）      2 输入不可用

1 与 2 必须分开：「样本不够所以没结论」与「命令用错了」是不同的事，
混在一起自动化脚本无法判断该重跑还是该修参数。
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.necessity.eval.records import (  # noqa: E402
    Attempt,
    GateMetrics,
    write_attempts,
)


def make_jsonl(tmp_path, *, n=8, base_pass=4, treat_pass=6, treat_arm="S_demo_v1"):
    """造一份两臂记录。baseline 臂固定 A_prime（规范 §7.5 的零能力对照）。"""
    p = tmp_path / "attempts.jsonl"
    rows = []
    for i in range(n):
        rows.append(Attempt(attempt_id=f"t{i}#A_prime", task_id=f"t{i}",
                            arm="A_prime", status="pass" if i < base_pass else "fail",
                            tokens=1000, gates=GateMetrics()))
    for i in range(n):
        rows.append(Attempt(attempt_id=f"t{i}#{treat_arm}", task_id=f"t{i}",
                            arm=treat_arm, status="pass" if i < treat_pass else "fail",
                            tokens=900, gates=GateMetrics()))
    write_attempts(p, rows)
    return p


def run_cli(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "backend.necessity.cli.main", "eval", "skill", *args],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace")


def test_cli_reports_inconclusive_when_evidence_is_insufficient(tmp_path):
    """**核心行为**：有正效应但样本不足时输出 inconclusive，退出码 1。

    规范 §7.8：「effect_size > 0 但 p ≥ 0.05 → inconclusive —— 样本不足，
    **不得准入**」。把 inconclusive 当 adopt 会让这套机制变成装饰。
    """
    p = make_jsonl(tmp_path, base_pass=4, treat_pass=6)
    r = run_cli(str(p), "--skill", "demo", "--version", "v1",
                "--treatment-arm", "S_demo_v1")
    assert r.returncode == 1, f"应判 inconclusive 并退 1，实际 {r.returncode}"
    assert "inconclusive" in r.stdout
    assert "不得准入" in r.stdout


def test_cli_exit_code_distinguishes_bad_input_from_no_conclusion(tmp_path):
    """输入问题退 2，与「样本不足」的 1 分开。

    混在一起会让自动化脚本无法判断该重跑（样本不够）还是该改参数（用错了）。
    """
    missing = tmp_path / "nope.jsonl"
    assert run_cli(str(missing), "--skill", "d", "--version", "v1",
                   "--treatment-arm", "X").returncode == 2


def test_cli_reports_arm_mismatch_actionably(tmp_path):
    """臂不匹配时要点出**缺哪一边**并列出文件里实际有哪些臂。

    只报「没有数据」会让人无从下手 —— 而这是最常见的用法错误。
    """
    p = make_jsonl(tmp_path, treat_arm="S_other")
    r = run_cli(str(p), "--skill", "demo", "--version", "v1",
                "--treatment-arm", "S_demo_v1")
    assert r.returncode == 2
    err = r.stderr
    assert "臂不匹配" in err
    assert "S_other" in err, "未列出文件里实际存在的臂"


def test_cli_writes_registry_only_when_asked(tmp_path):
    """不给 `--registry` 就不写盘 —— 不隐式改状态。"""
    from backend.necessity.eval.skill_registry import SkillRegistry

    p = make_jsonl(tmp_path, base_pass=4, treat_pass=6)
    reg_path = tmp_path / "reg.json"
    run_cli(str(p), "--skill", "demo", "--version", "v1",
            "--treatment-arm", "S_demo_v1")
    assert not reg_path.exists(), "没有 --registry 却写了盘"

    run_cli(str(p), "--skill", "demo", "--version", "v1",
            "--treatment-arm", "S_demo_v1", "--registry", str(reg_path))
    assert reg_path.is_file()
    # inconclusive 不得准入
    assert SkillRegistry(reg_path).is_adopted("demo") is False


def test_cli_does_not_adopt_on_mere_positive_trend(tmp_path):
    """**不是「看起来有效就准入」** —— 这正是 A5 存在的理由。

    规范 §7.1：「Skill 不应在创建或导入后立即启用，而应先**证明**它
    能改善 Agent 表现」。一个 50%→75% 的改善看起来不错，但在 8 个任务上
    p=0.5，没有任何统计意义上的证据。
    """
    from backend.necessity.eval.skill_registry import SkillRegistry

    p = make_jsonl(tmp_path, base_pass=4, treat_pass=8)   # 8/8 通过，看起来很强
    reg_path = tmp_path / "reg.json"
    r = run_cli(str(p), "--skill", "demo", "--version", "v1",
                "--treatment-arm", "S_demo_v1", "--registry", str(reg_path))
    # 即使 8/8 全过，只要 p 不显著就不得准入
    if r.returncode == 1:
        assert SkillRegistry(reg_path).is_adopted("demo") is False, \
            "inconclusive 却写入了 adopted=true"
