"""调用入口 —— 同时使用两个同名方法。"""
from models.user import User
from config.settings import Settings


def run() -> str:
    u = User(1)
    s = Settings()
    a = u.save()
    b = s.save(dry_run=True)
    return f"{a}|{b['decoy']}"
