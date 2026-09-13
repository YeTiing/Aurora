"""验收测试 —— 新能力可用 **且** 既有调用不受影响。"""
from src.textkit.format import truncate


def test_accepts_custom_suffix():
    """新参数必须真的生效 —— 基线没有这个参数，所以这里必失败。"""
    assert truncate("abcdefghijklmno", suffix=">") == "abcdefghij>"


def test_default_suffix_unchanged():
    """不传新参数时行为必须与以前一致（既有调用不受影响）。"""
    assert truncate("abcdefghijklmno") == "abcdefghij..."
