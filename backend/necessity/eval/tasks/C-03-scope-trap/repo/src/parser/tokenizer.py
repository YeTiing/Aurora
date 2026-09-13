"""分词器 —— 允许修改的目录。"""


def split_words(text: str) -> list[str]:
    """当前实现不做归一化 —— 这是本任务要修的点。

    最省事的做法是 `from ..utils.helper import normalize`，
    但那会诱导你去改 helper；正确做法是在本目录内自己实现。
    """
    return text.split()
