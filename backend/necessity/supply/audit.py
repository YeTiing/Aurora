"""运行时审计日志。

静态扫描只描述导入前的风险，运行时审计记录扩展实际请求的事件，两者不可
互相冒充。审计器默认追加 JSONL 且不执行网络操作；日志损坏时只抛出明确
异常，避免把「没有记录」伪装成「没有行为」。
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

@dataclass(frozen=True)
class AuditEvent:
    extension_id: str
    action: str
    target: str = ""
    allowed: bool | None = None
    details: dict[str, Any] | None = None
    timestamp: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["timestamp"] = self.timestamp or time.time()
        return data


class AuditLog:
    """追加并读取扩展运行时事件。"""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, extension_id: str, action: str, target: str = "",
               allowed: bool | None = None, details: dict[str, Any] | None = None) -> AuditEvent:
        """记录一次行为；持久化失败必须显式反馈给准入调用方。"""
        event = AuditEvent(extension_id, action, target, allowed, details, time.time())
        if self.path is not None:
            try:
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
            except OSError as exc:
                raise OSError(f"无法写入扩展审计日志: {self.path}") from exc
        return event

    def read(self) -> list[dict[str, Any]]:
        """读取完整 JSONL；坏行不能静默跳过，否则审计链会出现不可见缺口。"""
        if self.path is None or not self.path.exists():
            return []
        result = []
        for number, line in enumerate(self.path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"扩展审计日志第 {number} 行损坏") from exc
        return result

    @property
    def event_count(self) -> int:
        return len(self.read())


def audit_event(log: AuditLog, extension_id: str, action: str, **kwargs: Any) -> AuditEvent:
    """函数式审计入口，方便加载器或 MCP 适配层注入。"""
    return log.record(extension_id, action, **kwargs)


__all__ = ["AuditEvent", "AuditLog", "audit_event"]
