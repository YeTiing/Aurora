"""扩展内容指纹。

准入依据的是一组具体文件内容而不是文件时间戳；时间戳可能因复制、解压或
文件系统精度而不变。因而任何文件新增、删除或字节变化都会得到新 SHA-256，
调用方即可强制重新审批，而不是继续信任旧报告。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

_SKIP = {".git", "__pycache__", "node_modules", ".venv", "venv", ".pytest_cache"}


def _files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and not any(part in _SKIP for part in p.parts)
    )


def fingerprint(path: str | Path) -> str:
    """计算目录的稳定指纹；路径、内容和文件边界都参与哈希。"""
    if path is None:
        return hashlib.sha256(b"<missing>").hexdigest()
    root = Path(path)
    digest = hashlib.sha256()
    if not root.is_dir():
        return hashlib.sha256(f"<missing>:{root}".encode("utf-8")).hexdigest()
    for file in _files(root):
        try:
            data = file.read_bytes()
        except OSError:
            data = b"<unreadable>"
        rel = file.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(rel).to_bytes(8, "big"))
        digest.update(rel)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def has_changed(path: str | Path, previous: str) -> bool:
    """判断当前内容是否与旧指纹不同；空旧值也视为需要审批。"""
    return not previous or fingerprint(path) != previous


__all__ = ["fingerprint", "has_changed"]
