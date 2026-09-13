"""调用入口 —— 同时使用两个同名方法。"""
from models.account import Account
from config.registry import Settings


def run() -> str:
    u = Account(1)
    s = Settings()
    a = u.describe()
    b = s.describe(dry_run=True)
    return f"{a}|{b['decoy']}"
