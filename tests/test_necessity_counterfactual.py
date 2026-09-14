"""反事实实验（功能 2 的核心）—— 锁死三个会把结论判反的缺陷。

文档依据：DIFF_REDUCER.md §1.2 / §5.2 / §7 / §8.4。
INDEX.md Phase 3 的主指标之一就是「改动的必要性」。

## 这三个缺陷都是实测踩到的，且全部**不报错**

1. **撤销退化成恒等变换 → 冗余率恒 1.0**
   隔离环境按 `git worktree add --detach <base_commit>` 建立，默认 base=HEAD。
   若改动还在**工作区**（未提交），worktree 里是改动前的旧代码，于是
   「从改动后状态撤销某 hunk」变成把旧代码撤销成旧代码。
   实测：一个真正**必要**的改动（测试要求 `trunc(..., suffix=...)`）
   被判成「冗余率 1.0，Agent 的改动对目标无贡献」——结论完全相反。

2. **原地分析破坏调用方的工作区**
   判定器要反复撤销/恢复文件。最初直接在调用方目录上改，跑完
   `git diff` 变成 **0 行** —— Agent 的改动被实验自己吃掉了。

3. **判定口径与评测层分叉**
   `reduce/sandbox.run_tests` 自己判退出码，`eval/verify.py` 另一套。
   口径分叉会出现「评测说这测试失败、反事实实验说它通过」这种矛盾，
   而那不是算法问题（§8.4 明确要求同一口径）。
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.necessity.reduce.premise import check_applied  # noqa: E402
from backend.necessity.reduce.search import redundancy_ratio  # noqa: E402
from backend.necessity.reduce.split import all_hunks, parse_unified_diff  # noqa: E402
from backend.necessity.reduce.verify_runner import (  # noqa: E402
    RevertRunner,
    run_counterfactual,
)


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def _init(cwd: Path) -> None:
    for a in (["init", "-q"], ["config", "user.email", "t@t"],
              ["config", "user.name", "t"], ["add", "-A"], ["commit", "-qm", "snapshot"]):
        _git(a, cwd)


def make_repo(tmp_path: Path, *, with_redundant_file: bool = False) -> Path:
    """建一个**真实形态**的快照 + agent 改动。

    形态很关键：`tests/` 由**快照**提供（不在 diff 里），agent 只改源码。
    若让 agent 连测试一起改，撤销测试 hunk 会把验收用例删掉 ——
    那不是「改动冗余」，是「把考卷撕了」，会得出毫无意义的结论
    （这个坑我在验证时踩过一次）。
    """
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src/fmt.py").write_text(
        'def trunc(text):\n    return text[:10] + "..."\n', encoding="utf-8")
    if with_redundant_file:
        (root / "src/unused.py").write_text("X = 1\n", encoding="utf-8")
    # 快照的验收测试：**要求** suffix（这就是任务目标）
    (root / "tests/test_t.py").write_text(
        'from src.fmt import trunc\n\n\ndef test_custom():\n'
        '    assert trunc("abcdefghijklmno", suffix=">") == "abcdefghij>"\n',
        encoding="utf-8")
    _init(root)

    # agent 的改动：**不提交**（eval 里 Agent 从不提交）
    (root / "src/fmt.py").write_text(
        'DEFAULT="..."\n\n\ndef trunc(text, suffix=DEFAULT):\n'
        '    return text[:10] + suffix\n', encoding="utf-8")
    if with_redundant_file:
        (root / "src/unused.py").write_text("X = 1\nY = 2  # 没人用\n", encoding="utf-8")
    return root


def diff_of(root: Path) -> str:
    return _git(["diff", "HEAD"], root).stdout


# ── 前提校验 ──────────────────────────────────────────────────────

def test_premise_ok_when_workdir_holds_the_change(tmp_path):
    """改动在工作区时，前提**成立**（这是 eval 的真实形态）。"""
    root = make_repo(tmp_path)
    files = parse_unified_diff(diff_of(root))
    chk = check_applied(files, lambda rel: (root / rel).read_text(encoding="utf-8"))
    assert chk.ok is True, chk.reason
    assert chk.checked == 1


def test_premise_rejects_when_environment_has_old_code(tmp_path):
    """**缺陷 1 的守门人**：环境里是改动前的代码时必须拒绝。

    用一个「内容与 diff 不符」的文件模拟 worktree 拿到旧代码的情形。
    """
    root = make_repo(tmp_path)

    class OldEnv:
        """模拟 `git worktree --detach HEAD`：内容停留在改动前。"""

        def __call__(self, rel):
            if rel == "src/fmt.py":
                return 'def trunc(text):\n    return text[:10] + "..."\n'
            return ""

    chk = check_applied(parse_unified_diff(diff_of(root)), OldEnv())
    assert chk.ok is False
    assert "改动已应用" in chk.reason
    # 拒绝时必须给出**可执行**的说明，而不是只说「错了」
    assert "base-commit" in chk.reason or "提交" in chk.reason


def test_premise_handles_crlf(tmp_path):
    """行尾差异不该造成假失败（Windows 上 CRLF/LF 混用是常态）。"""
    root = make_repo(tmp_path)
    (root / "src/fmt.py").write_text(
        (root / "src/fmt.py").read_text(encoding="utf-8").replace("\n", "\r\n"),
        encoding="utf-8", newline="")
    files = parse_unified_diff(diff_of(root))
    chk = check_applied(files, lambda rel: (root / rel).read_text(encoding="utf-8"))
    assert chk.ok is True, chk.reason


# ── 隔离：绝不原地分析 ────────────────────────────────────────────

def test_counterfactual_does_not_touch_caller_workdir(tmp_path):
    """**缺陷 2 的守门人**：跑完实验，调用方的改动必须一字不动。

    ddmin 会反复撤销/恢复文件。原地跑会把 Agent 的改动整批吃掉 ——
    实测跑完 `git diff` 变成 0 行，而且不报错。
    """
    root = make_repo(tmp_path)
    before_diff = diff_of(root)
    before_files = {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
                    for p in root.rglob("*.py")}

    run_counterfactual(root, before_diff)

    assert diff_of(root) == before_diff, "调用方的改动被实验改动了"
    after_files = {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
                   for p in root.rglob("*.py")}
    assert after_files == before_files


# ── 判定正确性（功能 2 的核心）─────────────────────────────────────

def test_necessary_change_is_not_reported_redundant(tmp_path):
    """**缺陷 1 的正面断言**：必要改动 → 冗余率 0.0。

    这是整个功能的地基：改造前它会返回 1.0（完全相反的结论）。
    """
    root = make_repo(tmp_path)
    res, stats, prem = run_counterfactual(root, diff_of(root))
    allh = all_hunks(parse_unified_diff(diff_of(root)))

    assert prem.ok is True
    assert res.converged is True, "未收敛时必须显式标出（§7：宁可报未收敛）"
    assert len(res.hunks) == len(allh), "必要改动被误判为冗余"
    assert redundancy_ratio(allh, res.hunks) == 0.0
    # 撤销后验收测试必须失败 —— 这是「必要」的判定依据
    assert stats["fail"] >= 1


def test_redundant_hunk_is_separated_from_necessary_one(tmp_path):
    """冗余与必要必须能**分离**：只留必要那一个。"""
    root = make_repo(tmp_path, with_redundant_file=True)
    res, stats, prem = run_counterfactual(root, diff_of(root))
    allh = all_hunks(parse_unified_diff(diff_of(root)))

    assert prem.ok is True
    assert len(allh) == 2, "本场景应有 2 个文件各 1 个 hunk"
    kept_files = {getattr(h, "file", "?") for h in res.hunks}
    assert kept_files == {"src/fmt.py"}, f"冗余改动未被剔除: {kept_files}"
    # 1 行冗余 / 8 行总改动 —— 按**行数**定义（§8.5），不是 hunk 数
    assert redundancy_ratio(allh, res.hunks) > 0.0


def test_runner_reports_error_instead_of_lying_when_revert_is_impossible(tmp_path):
    """撤销做不到时必须返回 error，不能当成 pass。

    `error` 与 `pass` 混同会让「这次判定没做成」被读成「改动是冗余的」——
    而 §7 的总原则是宁可报未收敛/无法判定，也不要给错误结论。
    """
    root = make_repo(tmp_path)
    files = parse_unified_diff(diff_of(root))
    r = RevertRunner(workdir=root, files=files)
    # 抹掉基线内容，模拟「无法还原改动前」的情形
    r._post.clear()
    assert r.revert(r.hunks) is False, "拿不到基线内容时必须报失败而不是静默跳过"

    from backend.necessity.reduce.search import ERROR as _ERR
    assert r([[]]) == _ERR, "撤销失败必须归 error，不能归 pass/fail"
