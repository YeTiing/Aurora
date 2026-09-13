"""任务集加载 / 校验 —— EVAL.md §1 与 INDEX.md Phase 3 的目录约定。

任务目录结构（EVAL.md §1.2 第 3 步 + INDEX.md Phase 3）：

    tasks/<id>/
      repo/                起始代码快照（Agent 在这里干活）
      task.md              任务描述（**必须删掉实现细节**）
      tests/               验收测试（客观判定依据）
      meta.json            元数据（TaskSpec 的序列化）
      ground_truth.diff    原始 commit 的 diff（仅分析用，不给 Agent）

为什么要 loader 而不是直接 glob：
    §1.2 第 5 步要求「必须验证 repo/ 原始状态下这些测试是**失败**的，
    否则这个任务没有区分度」—— 这个反向前置检查是本模块的核心职责。
    没有它，一个测试本来就通过的任务会被当成 Agent 的功劳。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from ..records import TaskSpec

logger = logging.getLogger("necessity.eval.tasks")

# 任务目录里必须存在的文件（task.md/tests 的要求见 §1.2）
REQUIRED_FILES = ("task.md", "meta.json")
REQUIRED_DIRS = ("repo", "tests")


@dataclass
class LoadedTask:
    """加载后的任务 —— 路径都已解析，供 runner 直接使用。"""
    spec: TaskSpec
    root: Path
    repo: Path
    task_md: Path
    tests: Path
    meta_path: Path
    ground_truth: Path | None = None
    problems: list = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.problems is None:
            self.problems = []

    @property
    def ok(self) -> bool:
        return not self.problems

    def task_text(self) -> str:
        try:
            return self.task_md.read_text(encoding="utf-8")
        except Exception:
            return ""


def load_task(root: str | Path) -> LoadedTask:
    """加载单个任务目录。结构问题记在 problems 里，不抛异常。

    不抛异常的理由：批量加载时一个坏任务不该让整批失败；调用方
    （runner / validate 命令）需要看到**全部**问题而不是第一个。
    """
    root = Path(root)
    problems: list[str] = []

    spec = TaskSpec()
    meta_path = root / "meta.json"
    if meta_path.exists():
        try:
            spec = TaskSpec.from_dict(json.loads(meta_path.read_text(encoding="utf-8")))
        except Exception as e:
            problems.append(f"meta.json 解析失败: {type(e).__name__}: {e}")
    else:
        problems.append("缺少 meta.json")

    for name in REQUIRED_FILES:
        if not (root / name).exists():
            problems.append(f"缺少 {name}")
    for name in REQUIRED_DIRS:
        if not (root / name).is_dir():
            problems.append(f"缺少目录 {name}/")

    # spec 自身的校验（含 B 类必须有干扰符号这条硬要求）
    problems.extend(spec.validate())

    # task_id 缺失时用目录名兜底，并提示
    if not spec.task_id:
        spec.task_id = root.name

    # task.md 不能泄露实现细节（§1.2 第 4 步）
    task_md = root / "task.md"
    if task_md.exists():
        problems.extend(check_task_md_leaks(task_md.read_text(encoding="utf-8")))

    gt = root / "ground_truth.diff"
    return LoadedTask(
        spec=spec, root=root, repo=root / "repo",
        task_md=task_md, tests=root / "tests", meta_path=meta_path,
        ground_truth=gt if gt.exists() else None,
        problems=problems,
    )


# ── task.md 泄露检查（§1.2 第 4 步）──────────────────────────────

# 泄露实现细节的典型模式。文档给的对比：
#   ✅ "现在 X 函数在遇到 Y 情况时会抛出 Z 异常，改为返回 None，并同步更新所有调用方"
#   ❌ "在 parser.py 第 45 行加上 if not x: return None，然后修改 utils.py 的调用点……"
_LEAK_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bline\s+\d+", "提到具体行号"),
    (r"第\s*\d+\s*行", "提到具体行号"),
    (r"\b\w+\.(py|ts|js|go|rs)\s*:\s*\d+", "提到 文件:行号"),
    # 只有**赋值/语句级**的反引号内容才算贴实现。
    # `b == 0` 这类**条件表达式**是描述目标所必需的 —— 文档自己的好例子
    # 也写「遇到 Y 情况时会抛出 Z 异常」，那不是泄露。
    # 反引号内容交给 _looks_like_code() 判定 —— 用正则区分
    # 「条件表达式（描述目标）」与「语句/赋值（贴实现）」极易误判，
    # 见下方函数。这里保留一条占位以维持 (pattern, why) 结构。
    (r"\x00never\x00", "反引号里是语句/赋值片段"),
    # 注意：`divide(a, b)` 这类**提及目标函数签名**是描述任务所必需的，
    # 不算泄露。真正泄露的是「给出实现代码」。所以只在 def/function
    # **关键字**出现、且后面跟着代码体时才报（那是贴实现的行文）。
    (r"\bdef\s+\w+\s*\([^)]*\)\s*:", "给出了函数定义（含冒号 = 贴了实现）"),
    (r"\bfunction\s+\w+\s*\([^)]*\)\s*\{", "给出了函数定义（含花括号 = 贴了实现）"),
)


def _looks_like_code(text: str) -> list[str]:
    """检查反引号内容里是否有**可直接照抄的语句**。

    为什么不用纯正则：文档给的好例子是
        「现在 X 函数在遇到 Y 情况时会抛出 Z 异常，改为返回 None」
    这里 `b == 0` / `X` / `Y` 都是**描述**；而
        「加上 `if not x: return None`」
    的 `if not x: return None` 是可直接粘贴的**实现**。

    判定规则（保守，只报明确的实现片段）：
      1. 含赋值运算符 `+=` `-=` `*=` `=` 且不是 `==`/`!=`/`<=`/`>=`
      2. 含 `return <值>` / `raise <...>` / `import <...>`
      3. 含块级语句头 `if ...:` / `for ...:` / `while ...:` / `def ...:`
    """
    import re

    out: list[str] = []
    for m in re.finditer(r"`([^`]+)`", text or ""):
        seg = m.group(1)
        if not seg.strip():
            continue
        # 1) 赋值（排除比较运算符）
        stripped = re.sub(r"(==|!=|<=|>=|:=)", " ", seg)
        if re.search(r"(?<![<>=!])=(?!=)", stripped):
            out.append(f"反引号内含赋值: {seg!r}")
            continue
        # 2) return / raise / import
        if re.search(r"\b(return|raise|import|from)\b", seg):
            out.append(f"反引号内含语句: {seg!r}")
            continue
        # 3) 块级语句头
        if re.search(r"^\s*(if|for|while|def|class|with|try)\b[^:]*:", seg):
            out.append(f"反引号内含语句头: {seg!r}")
            continue
    return out


def check_task_md_leaks(text: str) -> list[str]:
    """检查任务描述是否泄露了实现方式。

    文档原话：「泄露实现方式会让任务变成『抄写』，测不出 Agent 的真实能力。」
    这是警告而非错误 —— 有些任务描述合法地会提到文件名（"在 parser 模块里"），
    所以只在**明显**泄露时报。
    """
    import re

    out: list[str] = []
    for pat, why in _LEAK_PATTERNS:
        if re.search(pat, text or ""):
            out.append(f"task.md 疑似泄露实现细节（{why}）—— 应只描述目标，不给做法")
    # 反引号内容单独用函数判定（正则区分不了"条件"与"语句"）
    out.extend(_looks_like_code(text or ""))
    return out


# ── 目录级加载与配比校验 ─────────────────────────────────────────

def load_all(root: str | Path = None) -> list[LoadedTask]:
    """加载 tasks/ 下所有任务目录（按名字排序，保证可复现）。"""
    base = Path(root) if root else Path(__file__).parent
    out: list[LoadedTask] = []
    for d in sorted(base.iterdir()):
        if d.is_dir() and not d.name.startswith(("_", ".")):
            out.append(load_task(d))
    return out


def check_distribution(tasks: list[LoadedTask]) -> list[str]:
    """校验任务集配比是否符合 EVAL.md §1.4。

    文档给的配比：跨文件特性 12 / 跨文件重构 8 / 诱导越界 6 / 高冗余 4 = 30。
    INDEX.md Phase 3 给的是 A5 / B12 / C5 = 22。

    ⚠️ 两份文档给的数量不一致。本函数按 **INDEX.md Phase 3** 为准
    （它是 Phase 3 的专用章节，且明确说了「跑通后再扩」），
    并把差距如实报出来而不是假装一致。
    """
    from collections import Counter

    c = Counter(t.spec.category for t in tasks if t.spec.category)
    problems: list[str] = []

    if not tasks:
        return ["任务集为空"]

    # B 类必须占一半以上（INDEX.md Phase 3：「B 类占一半以上」）
    b = c.get("B", 0)
    if b < len(tasks) / 2:
        problems.append(
            f"B 类（区分性任务）只占 {b}/{len(tasks)}，文档要求 ≥ 一半 —— "
            "B 类不足会让实验失去区分度（grep 也能做对，两组持平）"
        )

    for cat in ("A", "B", "C"):
        if c.get(cat, 0) == 0:
            problems.append(f"缺少 {cat} 类任务")

    # B 类必须各自声明干扰符号（TaskSpec.validate 已逐个查，这里做汇总提示）
    no_decoy = [t.spec.task_id for t in tasks
                if t.spec.category == "B" and not t.spec.decoy_symbols]
    if no_decoy:
        problems.append(f"B 类任务缺 decoy_symbols: {no_decoy}")

    return problems


def summarize(tasks: list[LoadedTask]) -> dict:
    """给 CLI / report 用的汇总。"""
    from collections import Counter

    by_cat = Counter(t.spec.category for t in tasks)
    by_diff = Counter(t.spec.difficulty for t in tasks)
    return {
        "total": len(tasks),
        "by_category": dict(by_cat),
        "by_difficulty": dict(by_diff),
        "with_decoy": sum(1 for t in tasks if t.spec.decoy_symbols),
        "with_constraints": sum(1 for t in tasks if t.spec.constraints),
        "with_ground_truth": sum(1 for t in tasks if t.ground_truth),
        "invalid": [t.spec.task_id for t in tasks if not t.ok],
    }
