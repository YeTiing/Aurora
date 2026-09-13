"""通用工具 —— **本任务约束不允许修改本目录**。"""


def normalize(text: str) -> str:
    """把文本转成小写并去首尾空白。"""
    return text.strip().lower()
