"""调用入口 —— 调用点在同一个仓库里、写法直白（A 类的关键性质）。"""
from src.textkit.strings import trim_ws, squash


def normalize(value: str) -> str:
    return trim_ws(value)


def normalize_lower(value: str) -> str:
    """顺带调用另一个函数 —— 重命名时不该动它。"""
    return squash(value)
