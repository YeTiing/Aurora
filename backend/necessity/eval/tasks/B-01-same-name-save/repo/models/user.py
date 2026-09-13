"""业务模块：用户模型。"""
from __future__ import annotations


PREFIX = "user"


class User:
    """用户。"""

    def __init__(self, value: int = 0):
        self.value = value

    def save(self) -> str:
        """保存当前对象。"""
        return PREFIX + ":" + str(self.value)

    def reload(self) -> int:
        return self.value
