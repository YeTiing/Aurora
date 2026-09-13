"""调用入口。"""
from src.textkit.format import truncate, count_words


def preview(text: str) -> str:
    return truncate(text)


def describe(text: str) -> str:
    return f"{count_words(text)} words"
