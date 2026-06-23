# Heartbeat 心跳系统 — 对齐 Codex 自动化心跳
from __future__ import annotations
import time, uuid, json, asyncio
from dataclasses import dataclass, field
from typing import Any

@dataclass
class HeartbeatConfig:
    interval_seconds: float = 300  # 默认5分钟
    enabled: bool = True
    automation_id: str = ""

@dataclass
class Heartbeat:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    automation_id: str = ""
    decision: str = "NOTIFY"  # NOTIFY / DONT_NOTIFY
    message: str = ""
    created_at: float = field(default_factory=time.time)

class HeartbeatManager:
    """管理心跳消息，让Agent主动感知并采取行动"""

    _instance = None
    def __new__(cls):
        if cls._instance is None: cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        self._config = HeartbeatConfig()
        self._last_beat: float = 0
        self._enabled = False

    def configure(self, interval: float = 300, enabled: bool = True):
        self._config.interval_seconds = interval
        self._enabled = enabled

    def should_beat(self) -> bool:
        if not self._enabled: return False
        return time.time() - self._last_beat >= self._config.interval_seconds

    def create(self, decision: str = "NOTIFY", message: str = "") -> Heartbeat:
        self._last_beat = time.time()
        return Heartbeat(
            automation_id=self._config.automation_id,
            decision=decision,
            message=message,
        )

    def to_xml(self, beat: Heartbeat) -> str:
        return f"""<heartbeat>
  <automation_id>{beat.automation_id}</automation_id>
  <decision>{beat.decision}</decision>
  <message>{beat.message}</message>
</heartbeat>"""

    def notify(self, message: str) -> str:
        """生成通知心跳"""
        beat = self.create("NOTIFY", message)
        return self.to_xml(beat)

    def dont_notify(self, message: str) -> str:
        """生成静默心跳"""
        beat = self.create("DONT_NOTIFY", message)
        return self.to_xml(beat)

heartbeat_manager = HeartbeatManager()