"""验收测试。

⚠️ 断言的是**新名字存在且行为不变** + **旧名字已消失**。
两条都要，否则「加个别名保留旧名」也能过 —— 那不是重命名。
"""
import pytest


def test_new_name_works():
    from src.textkit.strings import normalize_text
    assert normalize_text("  hi  ") == "hi"


def test_old_name_removed():
    """必须真的改名，而不是加一个别名了事。

    在函数体内 import（不是模块顶层）—— 顶层失败是 collection
    error，会让两个用例都不跑，基线检查就失去了分辨力。
    """
    with pytest.raises(ImportError):
        from src.textkit.strings import trim_ws  # noqa: F401
