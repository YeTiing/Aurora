"""验收测试。

⚠️ 同时断言 Account.describe 与 Settings.describe ——
只改其中一个会让另一个的断言失败，这正是分辨
「grep 误伤」与「符号级精确改动」的地方。
"""
from models.account import Account
from config.registry import Settings


def test_target_describe_behavior():
    """断言**期望**行为 —— 当前实现带前缀，所以基线必须失败。

    ⚠️ 这里如果写成断言当前行为，任务就失去区分度
    （EVAL.md §1.2 第 5 步的反向前置检查会失败）。
    """
    assert Account(1).describe() == "1"


def test_decoy_describe_untouched():
    """干扰符号的行为**必须不变** —— 改错了这里会红。"""
    assert Settings().describe(dry_run=True) == {"decoy": True, "dry_run": True}
