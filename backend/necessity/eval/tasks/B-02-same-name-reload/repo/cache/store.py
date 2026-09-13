"""干扰模块：与 models/order.py 里有**同名方法**，但语义完全不同。

⚠️ 这个文件的存在是整个实验能否成立的关键（INDEX.md Phase 3）：
   grep 'reload' 会同时命中它和 models/order.py 里的同名方法 ——
   baseline（只有 grep）必然误伤，graph（有结构图）能靠符号绑定区分。
   **不要删除或重命名它。**
"""
from __future__ import annotations


class Settings:
    """同名的 reload，但属于完全不同的领域。"""

    def save(self, *, dry_run: bool = False) -> dict:
        """与 User.save 同名，签名与返回值都不同。"""
        return {"decoy": True, "dry_run": dry_run}
