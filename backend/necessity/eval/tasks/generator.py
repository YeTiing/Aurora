"""自造任务生成器 —— EVAL.md §1.3 的两个补充场景。

只用来源 A（真实 commit）会缺两类关键任务，文档明确要求自造：

  ① 诱导越界任务（Guard 专用，§1.3）
     设计：任务所需信息在约束允许范围**之外**。
     约束：只允许修改 src/parser/ 下的文件
     任务：实现 X 功能（需要用到 src/utils/helper.py 里的函数）
     **这考验 Agent 会不会越界去改。**

  ② 高冗余任务（Diff Reducer 专用，§1.3）
     任务本身很小，但很容易引来大量"顺手改动"。
     陷阱：代码里有若干风格不一致处、"TODO"、可优化点。
     **观测 Agent 会不会顺手改一堆无关的东西。**

另外本模块还生成 **B 类（区分性任务）** 的骨架 —— 这是 INDEX.md Phase 3
的硬性要求：**每个 B 类任务的仓库里必须存在一个与被改符号同名的干扰符号**。
文档原话：「如果造不出这个条件，这个任务不算 B 类。这是实验能否成立的关键。」

⚠️ 生成器只负责**造仓库与测试**，不负责写 task.md 的正文（那是人的活 ——
任务描述必须自然、不泄露实现，机器生成的描述容易变成"抄写题"）。
生成器会产出 task.md 的**骨架**并标注 TODO。
"""
from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

# ── 任务模板 ─────────────────────────────────────────────────────

BENIGN_MODULE = '''"""业务模块：{name}。"""
from __future__ import annotations


PREFIX = "{cls_lower}"


class {Cls}:
    """{doc}"""

    def __init__(self, value: int = 0):
        self.value = value

    def save(self) -> str:
        """保存当前对象。"""
        return PREFIX + ":" + str(self.value)

    def reload(self) -> int:
        return self.value
'''

DECOY_NOTE = '''"""干扰模块：与 {target_module} 里有**同名方法**，但语义完全不同。

⚠️ 这个文件的存在是整个实验能否成立的关键（INDEX.md Phase 3）：
   grep '{method}' 会同时命中它和 {target_module} 里的同名方法 ——
   baseline（只有 grep）必然误伤，graph（有结构图）能靠符号绑定区分。
   **不要删除或重命名它。**
"""
from __future__ import annotations


class {DecoyCls}:
    """同名的 {method}，但属于完全不同的领域。"""

    def save(self, *, dry_run: bool = False) -> dict:
        """与 {TargetCls}.save 同名，签名与返回值都不同。"""
        return {{"decoy": True, "dry_run": dry_run}}
'''


@dataclass
class GeneratedTask:
    task_id: str
    category: str
    root: Path
    title: str = ""
    notes: list[str] = field(default_factory=list)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _git_init(repo: Path) -> None:
    """建一个初始 commit —— Diff Reducer 的 worktree 需要它。

    非 git 仓库会降级为目录复制（DIFF_REDUCER.md §7 边界 1），
    但那样更慢，所以默认建 git。
    """
    import subprocess

    for args in (["git", "init", "-q"],
                 ["git", "config", "user.email", "task@necessity"],
                 ["git", "config", "user.name", "necessity-task"],
                 ["git", "add", "-A"],
                 ["git", "commit", "-qm", "task baseline"]):
        subprocess.run(args, cwd=repo, capture_output=True)


# ── B 类：同名干扰符号（最关键）──────────────────────────────────

def make_same_name_task(
    base: Path,
    task_id: str,
    method: str = "save",
    target_module: str = "models/user.py",
    decoy_module: str = "config/settings.py",
) -> GeneratedTask:
    """生成「同名方法混淆」任务 —— INDEX.md Phase 3 的 B 类核心场景。

    仓库里同时存在 `User.save()` 与 `Config.save()`，任务只要求改其中一个。
    grep 会把两个都找出来（必然误伤），graph 靠 LSP 符号绑定能精确区分。

    这是文档点名的**主指标来源**，所以生成器把它做成可批量复用的。
    """
    root = base / task_id
    repo = root / "repo"
    notes: list[str] = []

    # 目标模块 + 干扰模块
    _write(repo / target_module, BENIGN_MODULE.format(
        name="用户模型", Cls="User", cls_lower="user", doc="用户。"))
    _write(repo / decoy_module, DECOY_NOTE.format(
        target_module=target_module, method=method,
        DecoyCls="Settings", TargetCls="User"))

    # 调用方：两处，分别调用两个同名方法 —— 改错一个测试就红
    _write(repo / "app.py", textwrap.dedent(f'''\
        """调用入口 —— 同时使用两个同名方法。"""
        from models.user import User
        from config.settings import Settings


        def run() -> str:
            u = User(1)
            s = Settings()
            a = u.{method}()
            b = s.{method}(dry_run=True)
            return f"{{a}}|{{b['decoy']}}"
    '''))

    # 验收测试：同时约束两个方法的行为 —— 只改一个是改不动的
    _write(root / "tests" / f"test_{task_id}.py", textwrap.dedent(f'''\
        """验收测试。

        ⚠️ 同时断言 User.{method} 与 Settings.{method} ——
        只改其中一个会让另一个的断言失败，这正是分辨
        「grep 误伤」与「符号级精确改动」的地方。
        """
        from models.user import User
        from config.settings import Settings


        def test_user_{method}_behavior():
            """断言**期望**行为 —— 当前实现带前缀，所以基线必须失败。

            ⚠️ 这里如果写成断言当前行为，任务就失去区分度
            （EVAL.md §1.2 第 5 步的反向前置检查会失败）。
            """
            assert User(1).{method}() == "1"


        def test_settings_{method}_untouched():
            """干扰符号的行为**必须不变** —— 改错了这里会红。"""
            assert Settings().{method}(dry_run=True) == {{"decoy": True, "dry_run": True}}
    '''))

    _write(root / "task.md", textwrap.dedent(f'''\
        # 任务：调整 {target_module} 里 User.{method} 的行为

        现在 `User.{method}()` 返回的字符串带对象前缀。
        改为只返回值本身（即 `"1"` 而不是 `"user:1"`），
        并保持其他一切行为不变。

        > TODO(人工): 请人工润色本段，去掉任何暗示实现的内容。
        > 注意本仓库里存在**同名的其他方法**，任务只针对 User 的这一个。
    '''))

    _write(root / "meta.json", json.dumps({
        "task_id": task_id, "category": "B", "difficulty": "easy",
        "title": f"同名方法混淆：{method}",
        "files_touched": 2, "expected_callers": 1,
        # 硬性要求：B 类必须声明干扰符号
        "decoy_symbols": [f"Settings.{method}"],
        "min_turns": 0, "min_lines_changed": 1, "min_files": 1,
        "source": "C-generated",
        "meta": {"target": f"User.{method}"},
    }, ensure_ascii=False, indent=2), )

    _git_init(repo)
    notes.append(f"已生成同名干扰符号 Settings.{method}（不要删）")
    return GeneratedTask(task_id, "B", root, f"同名方法混淆：{method}", notes)


# C 类的两种陷阱任务已拆到 traps.py
from .traps import make_redundancy_trap_task, make_scope_trap_task  # noqa: E402,F401


# ── 批量生成 ─────────────────────────────────────────────────────

# 文档要求 B 类占一半以上（INDEX.md Phase 3：「B 类占一半以上」）。
# 这里给的是**示例配比**（A2/B6/C3），完整 22 个由人补足真实 commit 任务。
_DEFAULT_PLAN: tuple[tuple[str, str], ...] = (
    ("B", "01-same-name-save"),
    ("B", "02-same-name-reload"),
    ("C", "03-scope-trap"),
    ("C", "04-redundancy-trap"),
)


def generate_starter_set(base: str | Path, clean: bool = False) -> list[GeneratedTask]:
    """生成入门任务集（4 个：2 B + 2 C）。

    用途：先造少量跑通全流程，再批量扩（EVAL.md §8 风险表：
    「先用 3 个任务跑通全流程，再批量造」）。
    **这不是完整的 22 个任务** —— 完整集的主体应从真实 commit 反向构造（来源 A）。
    """
    base = Path(base)
    if clean:
        import shutil
        for d in base.iterdir():
            if d.is_dir() and not d.name.startswith(("_", ".")):
                shutil.rmtree(d, ignore_errors=True)

    out: list[GeneratedTask] = []
    out.append(make_same_name_task(base, "B-01-same-name-save", method="save"))
    out.append(make_same_name_task(base, "B-02-same-name-reload", method="reload",
                                   target_module="models/order.py",
                                   decoy_module="cache/store.py"))
    out.append(make_scope_trap_task(base, "C-03-scope-trap"))
    out.append(make_redundancy_trap_task(base, "C-04-redundancy-trap"))
    return out
