"""调用入口 —— 同时使用两个同名方法。"""
from models.order import Order
from cache.store import Settings


def run() -> str:
    u = Order(1)
    s = Settings()
    a = u.reload()
    b = s.reload(dry_run=True)
    return f"{a}|{b['decoy']}"
