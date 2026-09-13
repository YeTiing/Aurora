"""A 类（基线任务）生成 —— INDEX.md Phase 3 的「防止倒退」对照组。

文档定义：

| 类别 | 任务类型 | grep 会怎样 | 用途 |
|---|---|---|---|
| **A. 基线任务** | 单文件内改函数名；简单参数增加 | 基本正确 | 证明 graph **不比 grep 差**（防止倒退） |

为什么 A 类不可省（而不是「反正 grep 做得好，跳过算了」）：

    Phase 3 的验收标准同时有两条，缺一不可 ——
      ✓ B 类上 graph 明显优于 baseline
      ✓ A 类上 graph **不比** baseline 差（不倒退）
    只有第一条是「半张验收单」：一个把简单任务做坏的方案同样能满足它。
    A 类是**反向证据**，专门用来抓「为了处理复杂场景而牺牲了简单场景」。

A 类与 B 类的区别在**干扰**，不在难度：
    A 类仓库里**没有**同名符号 —— grep 与结构图应当得到同样的答案。
    所以 A 类任务**不允许**声明 decoy_symbols（有就说明造错了类）。

⚠️ A 类必须真的「grep 友好」：目标符号在仓库里唯一，且调用点写法直白
   （`func(...)`，没有 getattr / 装饰器 / 同名）。否则它会变成 B 类，
   而把 B 类混进 A 类会**稀释**主指标的差距（文档 §跑分 明说要分类统计）。
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

from .generator import GeneratedTask, _dotted, _git_init, _write


# ── A① 类：单文件内改函数名 ─────────────────────────────────────

RENAME_MODULE = '''"""字符串工具。

本模块里的函数名 `{old}` 语义偏窄 —— 它只处理一种情况，
但调用方已经在按更通用的语义使用它。
"""
from __future__ import annotations


def {old}(text: str) -> str:
    """把文本 {old_doc}。"""
    return text.strip()


def {other}(text: str) -> str:
    """另一个不相关的函数 —— 与本次改动无关。"""
    return text.lower()
'''

RENAME_APP = '''"""调用入口 —— 调用点在同一个仓库里、写法直白（A 类的关键性质）。"""
from {mod} import {old}, {other}


def normalize(value: str) -> str:
    return {old}(value)


def normalize_lower(value: str) -> str:
    """顺带调用另一个函数 —— 重命名时不该动它。"""
    return {other}(value)
'''


def make_rename_task(base: Path, task_id: str, *, old: str = "trim_ws",
                     new: str = "normalize_text",
                     module: str = "src/textkit/strings.py") -> GeneratedTask:
    """生成「单文件内改函数名」任务 —— A 类的典型场景。

    为什么它属于 A 类：目标名字 `{old}` 在整个仓库里**唯一**，
    调用点也是直白的 `{old}(...)`。grep 能一次找全，
    结构图的优势在这里体现不出来 —— 这正是我们要的（用来验不倒退）。

    ⚠️ 验收测试里「旧名字已消失」这条**不能在模块顶层直接 import**：
    顶层 import 失败会让整个测试模块报 collection error，pytest 退出码变 2、
    **两个用例一个都不跑**。那样基线检查看到的只是「被中断」，
    分不清「任务合格」与「测试写错了」。
    正确做法是在函数体里 import 并断言 `ImportError` —— 这样
    「新名字可用」与「旧名字已删」是两条独立可判定的用例。
    """
    root = base / task_id
    repo = root / "repo"
    notes: list[str] = []
    mod = _dotted(module)
    other = "squash"

    _write(repo / module, RENAME_MODULE.format(
        old=old, new=new, other=other,
        old_doc="两端的空白去掉（只做这一件事）"))
    _write(repo / "app.py", RENAME_APP.format(mod=mod, old=old, other=other))

    _write(root / "tests" / f"test_{task_id}.py", textwrap.dedent(f'''\
        """验收测试。

        ⚠️ 断言的是**新名字存在且行为不变** + **旧名字已消失**。
        两条都要，否则「加个别名保留旧名」也能过 —— 那不是重命名。
        """
        import pytest


        def test_new_name_works():
            from {mod} import {new}
            assert {new}("  hi  ") == "hi"


        def test_old_name_removed():
            """必须真的改名，而不是加一个别名了事。

            在函数体内 import（不是模块顶层）—— 顶层失败是 collection
            error，会让两个用例都不跑，基线检查就失去了分辨力。
            """
            with pytest.raises(ImportError):
                from {mod} import {old}  # noqa: F401
    '''))

    _write(root / "task.md", textwrap.dedent(f'''\
        # 任务：把 {module} 里的 `{old}` 改名为 `{new}`

        `{old}` 的名字只体现了「去空白」，但调用方都在按更通用的语义使用它。
        请把它改名为 `{new}`，并同步更新仓库里所有调用它的地方。

        行为不要变：输入 `"  hi  "` 仍返回 `"hi"`。
        改完后旧名字不应再存在（不要保留一个别名）。
    '''))

    _write(root / "meta.json", json.dumps({
        "task_id": task_id, "category": "A", "difficulty": "easy",
        "title": "单文件内改函数名",
        "files_touched": 2, "expected_callers": 2,
        # A 类**必须没有**干扰符号 —— 有就说明造错类了
        "decoy_symbols": [],
        "constraints": [],
        "min_turns": 0, "min_lines_changed": 2, "min_files": 2,
        "source": "C-generated",
        "meta": {"target": f"{mod}.{old}", "rename_to": new},
    }, ensure_ascii=False, indent=2))

    _git_init(repo)
    notes.append(f"目标名 {old} 在仓库内唯一（A 类要求：grep 也能做对）")
    return GeneratedTask(task_id, "A", root, "单文件内改函数名", notes)


# ── A② 类：简单参数增加 ─────────────────────────────────────────

PARAM_MODULE = '''"""文本截断工具。"""
from __future__ import annotations

DEFAULT_SUFFIX = "..."


def truncate(text: str) -> str:
    """把文本截断到 10 个字符以内。

    ⚠️ 阈值与后缀都是**写死**的 —— 本次任务就是把后缀这个能力露出来。
    写死让「基线必然不满足新验收」：调用方无法传自定义后缀。
    """
    if len(text) <= 10:
        return text
    return text[:10] + DEFAULT_SUFFIX


def count_words(text: str) -> int:
    """与本次改动无关。"""
    return len(text.split())
'''

PARAM_APP = '''"""调用入口。"""
from {mod} import truncate, count_words


def preview(text: str) -> str:
    return truncate(text)


def describe(text: str) -> str:
    return f"{{count_words(text)}} words"
'''


def make_param_task(base: Path, task_id: str, *,
                    module: str = "src/textkit/format.py") -> GeneratedTask:
    """生成「给函数加一个可选参数」任务 —— A 类的第二个典型场景。

    为什么属于 A 类：只加一个带默认值的**可选**参数，
    所有既有调用点无需改动即可继续工作（这正是「简单参数增加」的判据）。
    所以不存在「漏改调用点」的陷阱 —— grep 与结构图同样做对。

    ⚠️ 这个场景最容易造出「无事可做」的假任务：
    如果目标函数**本来就已经**接受该参数，或验收测试断言的是既有行为，
    基线就会全绿 —— 那等于测「Agent 什么都不做」，不是 A 类任务。
    所以这里刻意让后缀写死，并让验收测试断言「新参数真的生效」。
    """
    root = base / task_id
    repo = root / "repo"
    notes: list[str] = []
    mod = _dotted(module)

    _write(repo / module, PARAM_MODULE)
    _write(repo / "app.py", PARAM_APP.format(mod=mod))

    _write(root / "tests" / f"test_{task_id}.py", textwrap.dedent('''\
        """验收测试 —— 新能力可用 **且** 既有调用不受影响。"""
        from {mod} import truncate


        def test_accepts_custom_suffix():
            """新参数必须真的生效 —— 基线没有这个参数，所以这里必失败。"""
            assert truncate("abcdefghijklmno", suffix=">") == "abcdefghij>"


        def test_default_suffix_unchanged():
            """不传新参数时行为必须与以前一致（既有调用不受影响）。"""
            assert truncate("abcdefghijklmno") == "abcdefghij..."
    ''').format(mod=mod))

    _write(root / "task.md", textwrap.dedent(f'''\
        # 任务：让 {module} 的 `truncate` 支持自定义后缀

        现在 `truncate` 截断后固定追加三个点，调用方无法控制。
        请让它接受一个可选的后缀参数（参数名 `suffix`），
        不传时保持现在的行为。

        **不要改动任何既有调用点** —— 新参数必须可选。
    '''))

    _write(root / "meta.json", json.dumps({
        "task_id": task_id, "category": "A", "difficulty": "easy",
        "title": "简单参数增加（可选参数）",
        "files_touched": 1, "expected_callers": 2,
        "decoy_symbols": [],
        "constraints": [],
        "min_turns": 0, "min_lines_changed": 2, "min_files": 1,
        "source": "C-generated",
        "meta": {"target": f"{mod}.truncate", "new_param": "suffix"},
    }, ensure_ascii=False, indent=2))

    _git_init(repo)
    notes.append("新参数是**可选**的 —— 这正是 A 类（不考验调用点更新）的定义")
    return GeneratedTask(task_id, "A", root, "简单参数增加", notes)
