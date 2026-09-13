"""调用入口 —— 同时使用两个同名方法。"""
from models.session import Session
from cache.buffer import Settings


def run() -> str:
    u = Session(1)
    s = Settings()
    a = u.flush()
    b = s.flush(dry_run=True)
    return f"{a}|{b['decoy']}"
