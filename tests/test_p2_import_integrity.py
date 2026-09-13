"""导入路径完整性 —— 防止"同一模块两份实例"的隐患复现。

背景：多个测试文件曾把 backend/ 也插进 sys.path，于是
`from tools.plan_store import ...` 与 `from backend.tools.plan_store import ...`
会加载出【两个不同的模块对象】，各自的模块级状态互不可见。
表现为 plan_store 写进去读不到、todo_write._todos 两张表 —— 极难定位。

根治：生产与测试统一用 backend. 前缀，且只把项目根放进 sys.path。
"""
import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_conftest_puts_project_root_on_path():
    assert str(ROOT) in sys.path, "conftest.py 应把项目根加入 sys.path"


def test_no_duplicate_module_instances_for_stateful_modules():
    """有模块级状态的模块不能出现两份实例。

    只检查这几个真正持有全局状态的模块 —— 它们一旦被重复加载，
    症状是静默的数据不可见，而不是报错。
    """
    from backend.tools import plan_store
    from backend.tools import todo_write

    # 若存在 'tools.plan_store' 这个平行模块，两份实例的状态就会分叉
    assert "tools.plan_store" not in sys.modules, \
        "检测到 tools.plan_store 被单独加载，会与 backend.tools.plan_store 状态分叉"
    assert "tools.todo_write" not in sys.modules

    # 反向确认：通过 backend 路径拿到的就是同一对象
    again = importlib.import_module("backend.tools.plan_store")
    assert again is plan_store


def test_plan_store_state_is_shared_across_import_paths():
    """从 backend.tools 导入的 plan_store 写入后，同一模块能读回。"""
    from backend.tools.plan_store import get_plan, set_plan

    set_plan("import-integrity", [{"step": 1, "description": "x", "status": "pending"}])
    assert get_plan("import-integrity")[0]["step"] == 1

    mod = importlib.import_module("backend.tools.plan_store")
    assert mod.get_plan("import-integrity"), "平行实例导致状态不可见"


def test_backend_package_is_the_only_import_root():
    """测试目录下不应再把 backend/ 单独插进 sys.path。"""
    offenders = []
    for f in (ROOT / "tests").glob("*.py"):
        if f.name == Path(__file__).name:
            continue  # 本文件自身含该字面量，跳过
        text = f.read_text(encoding="utf-8", errors="replace")
        if 'parent.parent / "backend"' in text or "parent.parent / 'backend'" in text:
            offenders.append(f.name)
    assert not offenders, f"这些文件仍在把 backend/ 加入 sys.path: {offenders}"


def test_no_bare_top_level_imports_of_backend_modules_in_tests():
    """测试不应裸导入 backend 下的顶层模块（会产生平行实例）。"""
    import re

    pattern = re.compile(
        r"^\s*(?:from|import)\s+(config|security|observability|memory|dual_memory|"
        r"tools|agent|context|rag|multi_agent|skills|plugins|swarm|lsp)\b",
        re.M,
    )
    offenders = []
    for f in (ROOT / "tests").glob("*.py"):
        text = f.read_text(encoding="utf-8", errors="replace")
        for m in pattern.finditer(text):
            line = text[:m.start()].count("\n") + 1
            offenders.append(f"{f.name}:{line} {m.group(0).strip()}")
    assert not offenders, "检测到裸导入（应使用 backend. 前缀）: " + "; ".join(offenders[:10])
