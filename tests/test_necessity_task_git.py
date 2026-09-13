"""任务快照的 git 元数据 —— 锁死两个**静默**失败模式。

## 一、gitlink（克隆后源码丢失）

任务快照的 `repo/` 是嵌套 git 仓库，git 会把**任何**嵌套仓库记成
gitlink（mode 160000），只存一个 SHA。后果：本机一切正常，
`git clone` 出来的 `repo/` 是**空目录** —— 而没有任何一步报错。

实测确认的三种无效修法（都验证过，全部失败）：
    add -A / add -f / 外层 .gitignore 排除 repo/.git
唯一有效：内层 `.git` 不入库，克隆后按需重建（repo_git.ensure_git）。

## 二、`--is-inside-work-tree` 判断错仓库

`git rev-parse --is-inside-work-tree` 对**任何**子目录都返回 true
（只要祖先里有 .git）。任务快照恰好嵌在 Aurora 仓库里，于是
`Sandbox._is_git()` 为真，`git worktree add` 建出来的是
**Aurora 自己的** worktree —— Diff Reducer 在分析错对象，全程不报错。

正确判据：`--show-toplevel` 必须等于该目录本身。
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.necessity.eval.tasks import repo_git  # noqa: E402

# 真实任务集（同时是对仓库内容的断言）
TASKS_DIR = ROOT / "backend" / "necessity" / "eval" / "tasks"


def _git(args, cwd):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    return (r.stdout or "").strip(), r.returncode


def _init_repo(p: Path) -> None:
    for a in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        _git(a, p)


# ── 入库规则：嵌套 .git 必须被忽略 ───────────────────────────────

def test_tasks_gitignore_excludes_inner_git():
    """`tasks/.gitignore` 必须排除嵌套的 repo/.git。

    漏了这条不会报错 —— 只是克隆后少文件（见文件头）。
    """
    p = repo_git.committed_gitignore_path(TASKS_DIR)
    assert p.is_file(), f"缺少 {p} —— 嵌套 .git 会被记成 gitlink"
    text = p.read_text(encoding="utf-8")
    assert "repo/.git" in text


def test_gitignore_text_is_shared_not_duplicated():
    """规则文本只定义一次（模块常量），.gitignore 与它一致。

    防的是「文档/规则两处各写一份，改一处漏一处」。
    """
    p = repo_git.committed_gitignore_path(TASKS_DIR)
    assert p.read_text(encoding="utf-8") == repo_git.COMMITTED_GITIGNORE


def test_inner_git_is_actually_ignored_by_git():
    """让 git 自己确认：repo/.git 处于忽略状态。

    只断言文件内容不够 —— 规则写法错了（比如少了 `**/` 前缀）照样不生效。
    """
    d = TASKS_DIR / "A-01-rename-func" / "repo" / ".git"
    if not d.exists():
        pytest.skip("本地无内层 .git（克隆场景）")
    r = subprocess.run(
        ["git", "check-ignore", "-v",
         str(d.relative_to(ROOT)).replace("\\", "/")],
        cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, "repo/.git 未被忽略 —— 会被记成 gitlink"


# ── is_git_repo 的判据（最关键）─────────────────────────────────

def test_is_git_repo_true_for_real_repo(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    _init_repo(repo)
    assert repo_git.is_git_repo(repo) is True


def test_is_git_repo_false_for_subdir_of_a_repo(tmp_path):
    """**核心用例**：仓库里的子目录不是仓库。

    用 `--is-inside-work-tree` 会把这里判成 True —— 那会让
    `git worktree add` 建出父仓库的 worktree。
    """
    outer = tmp_path / "outer"
    outer.mkdir()
    _init_repo(outer)
    sub = outer / "a" / "b" / "repo"
    sub.mkdir(parents=True)
    assert repo_git.is_git_repo(sub) is False


def test_is_inside_work_tree_would_misjudge(tmp_path):
    """把「为什么不能用 --is-inside-work-tree」写成可执行证据。

    这样将来有人「简化」判据时，这条会红并说明原因。
    """
    outer = tmp_path / "outer"
    outer.mkdir()
    _init_repo(outer)
    sub = outer / "sub"
    sub.mkdir()

    out, rc = _git(["rev-parse", "--is-inside-work-tree"], sub)
    assert rc == 0 and out == "true", "该判据对子目录返回 true（这正是陷阱）"
    # 而正确的判据给出相反（正确）的答案
    assert repo_git.is_git_repo(sub) is False


def test_is_git_repo_false_for_missing_dir(tmp_path):
    assert repo_git.is_git_repo(tmp_path / "nope") is False


# ── ensure_git 的行为 ────────────────────────────────────────────

def _mk_task(root: Path, with_git: bool) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    repo = root / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    (root / "task.md").write_text("t", encoding="utf-8")
    if with_git:
        _init_repo(repo)
        repo_git.commit_all(repo)
    return root


def test_ensure_git_ok_when_already_repo(tmp_path):
    root = _mk_task(tmp_path / "t", with_git=True)
    assert repo_git.ensure_git(root) == "ok"


def test_ensure_git_rebuilds_when_missing_inside_a_repo(tmp_path):
    """克隆场景：外层有仓库、任务 repo/ 内层没有 —— 必须重建。"""
    outer = tmp_path / "outer"
    outer.mkdir()
    _init_repo(outer)
    root = _mk_task(outer / "tasks" / "t", with_git=False)

    assert repo_git.ensure_git(root) == "rebuilt"
    assert repo_git.is_git_repo(root / "repo") is True
    # 重建后必须有初始 commit（否则 worktree / rev-parse HEAD 都不能用）
    out, rc = _git(["rev-parse", "HEAD"], root / "repo")
    assert rc == 0 and out


def test_ensure_git_auto_when_not_in_any_repo(tmp_path):
    """解包出来的普通目录：不该硬建仓库（Diff Reducer 本就允许降级）。"""
    root = _mk_task(tmp_path / "t", with_git=False)
    assert repo_git.ensure_git(root) == "auto"


def test_ensure_git_reports_missing_repo_dir(tmp_path):
    root = tmp_path / "t"
    root.mkdir()
    assert repo_git.ensure_git(root) == "no-repo"


def test_ensure_git_does_not_touch_existing_history(tmp_path):
    """已是仓库时不得改写 —— `rev-parse HEAD` 可能已被别处引用。"""
    root = _mk_task(tmp_path / "t", with_git=True)
    before, _ = _git(["rev-parse", "HEAD"], root / "repo")
    repo_git.ensure_git(root)
    after, _ = _git(["rev-parse", "HEAD"], root / "repo")
    assert before == after


def test_ensure_all_covers_every_task():
    """真实任务集：每个 repo/ 都应是可用仓库（本地场景）。"""
    statuses = repo_git.ensure_all(TASKS_DIR)
    assert len(statuses) == 8, f"任务数不对: {sorted(statuses)}"
    bad = {k: v for k, v in statuses.items() if v not in ("ok", "rebuilt")}
    assert not bad, f"这些任务的 repo/ 不是可用仓库: {bad}"


# ── Sandbox 不得在父仓库上建 worktree ────────────────────────────

def test_sandbox_is_git_false_for_task_snapshot_subdir():
    """真实任务快照嵌在 Aurora 仓库里 —— Sandbox 必须判定它**不是**仓库。

    判对则降级为目录复制（文档允许，功能等价）；
    判错则会在 Aurora 上建 worktree，Diff Reducer 从此分析错对象。
    """
    from backend.necessity.reduce.sandbox import Sandbox

    # 取一个 repo/ 没有内层 .git 的场景：直接在 Aurora 里造一个子目录
    sub = ROOT / "backend" / "necessity" / "eval" / "tasks" / "_probe_no_git"
    try:
        sub.mkdir(parents=True, exist_ok=True)
        sb = Sandbox(str(sub))
        assert sb._is_git() is False, (
            "把 Aurora 仓库里的子目录判成了 git 仓库 —— "
            "worktree 会建在 Aurora 上而不是任务上"
        )
    finally:
        shutil.rmtree(sub, ignore_errors=True)


def test_sandbox_is_git_true_for_standalone_repo(tmp_path):
    from backend.necessity.reduce.sandbox import Sandbox

    repo = tmp_path / "r"
    repo.mkdir()
    _init_repo(repo)
    assert Sandbox(str(repo))._is_git() is True
