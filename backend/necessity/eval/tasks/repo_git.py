"""任务快照的 git 元数据 —— 为什么不入库、以及怎么按需重建。

## 问题（实测确认，不是推测）

任务目录里的 `repo/` 是一个**真 git 仓库**（`_git_init` 建的）。
Diff Reducer 需要它：`reduce/sandbox.py` 用 `git worktree add` 做隔离，
`reduce/hooks.py` 用 `git rev-parse HEAD` 取基线。

但 git 遇到嵌套仓库时会把它记成 **gitlink（mode 160000）** —— 哪怕内层
索引干净、工作区干净、`git status` 全绿。实测结论：

    add -A                        -> 160000，克隆后源码全丢
    add -f（强加内层文件）         -> 160000，无效
    外层 .gitignore 排除 repo/.git/ -> 160000，无效
    删掉内层 .git 后再 add         -> 100644，克隆后源码在

四种策略里只有一个可行，其余三种都**不报错**，只是克隆后少一个目录。
（`git clone` 后 `repo/` 是个空目录 —— 本机测不出来。）

## 选择

让嵌套的 `.git` **永不入库**，克隆后由 `ensure_git()` 按需重建。

- 源码（`.py`）正常入库 —— 这是「克隆后任务集还能不能用」的关键
- `task.md` / `meta.json` / `tests/` 正常入库
- 内层 `.git` 不入库（git 靠 `.gitignore` 跳过它，此时它不会把 repo/ 记成 gitlink）

## 重建的代价（如实说明，不假装免费）

重建出的是**一个全新的初始 commit**，不是原来那个 SHA。所以：

  - 使用任务快照时，Diff Reducer 的基线 = 重建时的 HEAD。这是**对的** ——
    快照本身就是初始状态，没有历史可依赖。
  - `tests/` 下的用例不依赖任何具体 SHA。
  - 若将来引入 `ground_truth.diff` 需要匹配某个固定 SHA，必须显式记录它，
    不能指望重建结果一致。现在没有这个依赖。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

# 入库时被忽略的路径（相对 tasks/ 根）。这段文本会写进 tasks/.gitignore。
COMMITTED_GITIGNORE = """\
# 任务快照的内层 git 元数据 —— **不入库**。
#
# 理由见 tasks/repo_git.py 的文件头：git 会把任何嵌套仓库记成 gitlink
# （mode 160000），只存一个 SHA，克隆后 repo/ 变成空目录且**不报错**。
# 而快照其实不需要历史 —— 克隆后由 ensure_git() 重建初始 commit 即可。
# 源码（*.py）**必须**入库：`**/repo/.git/` 这条只排除元数据目录。
**/repo/.git/
"""

# 重建时排除的运行时目录
_SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")


def is_git_repo(path: str | Path) -> bool:
    """这个目录**自身**是不是一个 git 仓库的根。

    ⚠️ 不能用 `rev-parse --is-inside-work-tree` —— 它对**任何**子目录都返回
    `true`（只要祖先里有个 .git）。实测：在 Aurora 仓库里对
    `backend/necessity/eval/tasks/X/repo`（本身没 .git）调用，
    它返回 true，于是 `git worktree add` 创建的worktree 是
    **Aurora 自己的**（内容里能看到 Aurora 的顶层文件），而不是任务仓库。
    这不会报错，只会让 Diff Reducer 分析错对象 —— 静默的错误结论。

    正确判据：`--show-toplevel` 必须**等于**该目录本身。
    """
    p = Path(path)
    if not p.is_dir():
        return False
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                       cwd=p, capture_output=True, text=True)
    if r.returncode != 0:
        return False
    top = (r.stdout or "").strip()
    if not top:
        return False
    try:
        return Path(top).resolve() == p.resolve()
    except Exception:
        return False


def commit_all(repo: str | Path, message: str = "task baseline") -> bool:
    """把工作区全部提交。返回是否产生了新 commit。

    用 `add -A` + `commit` 而非 `--amend`：初始 commit 是任务内容，
    改写它会改变 `rev-parse HEAD`，而那个值可能已被别处引用。
    """
    repo = Path(repo)
    if not is_git_repo(repo):
        return False
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    r = subprocess.run(["git", "commit", "-qm", message], cwd=repo,
                       capture_output=True, text=True)
    return r.returncode == 0


def ensure_git(task_root: str | Path, *, strict: bool = True) -> str:
    """确保任务的 `repo/` 是一个可用的 git 仓库。

    返回状态字符串，便于调用方如实报告（而不是静默通过）：
        "ok"        已经是仓库（本地场景，什么也没做）
        "rebuilt"   原本不是仓库，已重建
        "auto"      原本不是仓库，且**不在 git 仓库内** —— 重建无意义
                    （Diff Reducer 本就降级为目录复制，见 sandbox.py 的
                     §7 边界 1）
        "no-repo"   repo/ 不存在

    `strict=True` 时，「在 git 仓库内但 repo/ 不是子仓库」视为待修复状态 ——
    它会尝试重建；重建不了就抛出，因为那说明快照是坏的。
    """
    root = Path(task_root)
    repo = root / "repo"
    if not repo.is_dir():
        return "no-repo"
    if is_git_repo(repo):
        return "ok"

    # 不在任何 git 仓库里 -> 快照是「解包出来的普通目录」，
    # 直接按快照用即可，不建仓库（Diff Reducer 会降级，这是文档允许的）
    outer = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                           cwd=repo, capture_output=True, text=True)
    if outer.returncode != 0:
        return "auto"

    ok = _rebuild(repo)
    if not ok and strict:
        raise RuntimeError(
            f"任务的 repo/ 不是 git 仓库且重建失败: {repo}\n"
            "Diff Reducer 的 worktree 隔离会退化为目录复制 —— "
            "功能等价但更慢；若这不符预期，请检查 git 是否可用。"
        )
    return "rebuilt" if ok else "auto"


def _rebuild(repo: Path) -> bool:
    """在 repo/ 里重建一个初始 commit。"""
    for args in (["init", "-q"],
                 ["config", "user.email", "task@necessity"],
                 ["config", "user.name", "necessity-task"]):
        r = subprocess.run(["git", *args], cwd=repo, capture_output=True)
        if r.returncode != 0:
            return False
    return commit_all(repo)


def ensure_all(base: str | Path) -> dict[str, str]:
    """给 base 下所有任务补 git 仓库。返回 {任务名: 状态}。

    为什么需要它：克隆出来的任务集，`repo/.git` 是不在的 ——
    而 `tests/` 与 runner 都假定 `repo/` 是个仓库。缺了它不会有报错，
    只会让 D 组（Diff Reducer）静默降级。
    """
    base = Path(base)
    out: dict[str, str] = {}
    for d in sorted(base.iterdir()):
        if d.is_dir() and not d.name.startswith(("_", ".")):
            if (d / "repo").is_dir():
                out[d.name] = ensure_git(d, strict=False)
    return out


def committed_gitignore_path(tasks_root: str | Path) -> Path:
    """`tasks/.gitignore` 的路径（写 COMMITTED_GITIGNORE 的地方）。"""
    return Path(tasks_root) / ".gitignore"
