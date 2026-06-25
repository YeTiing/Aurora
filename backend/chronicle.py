"""Aurora Chronicle — screen capture state manager (placeholder).

This module manages the lifecycle and configuration of screen recording.
Actual screen capture requires OS-level APIs (DirectX/Windows.Graphics.Capture
on Windows, AVFoundation on macOS) that must be provided by a native backend.

ChronicleManager here handles:
  - State tracking: running / paused / disabled
  - Configuration persistence
  - Lifecycle: start, pause, resume, stop, toggle
  - Logging state transitions to a local log file

When a native capture backend is plugged in, it should call these methods
to synchronize state.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

CHRONICLE_DATA_DIR = Path(__file__).parent / "data"
CHRONICLE_STATE_FILE = CHRONICLE_DATA_DIR / "chronicle_state.json"
CHRONICLE_LOG_FILE = CHRONICLE_DATA_DIR / "chronicle_log.jsonl"

ChronicleState = Literal["running", "paused", "disabled"]


@dataclass
class ChronicleConfig:
    """Configuration for screen capture."""
    enabled: bool = False
    fps: int = 5
    quality: int = 80
    output_dir: str = ""

    def __post_init__(self):
        if not self.output_dir:
            self.output_dir = str(CHRONICLE_DATA_DIR / "captures")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ChronicleConfig":
        return cls(
            enabled=d.get("enabled", False),
            fps=d.get("fps", 5),
            quality=d.get("quality", 80),
            output_dir=d.get("output_dir", ""),
        )


class ChronicleManager:
    """State machine for screen capture. Does NOT perform actual capture.

    This is a placeholder that:
      - Tracks state (running/paused/disabled)
      - Persists config
      - Logs transitions to a JSONL log file
      - Waits for a native backend to do the real work
    """

    def __init__(self):
        CHRONICLE_DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._state: ChronicleState = "disabled"
        self._config = ChronicleConfig()
        self._started_at: float | None = None
        self._paused_at: float | None = None
        self._total_runtime: float = 0.0
        self._load()

    def _load(self) -> None:
        """Load persisted state and config from disk."""
        if CHRONICLE_STATE_FILE.exists():
            try:
                data = json.loads(CHRONICLE_STATE_FILE.read_text(encoding="utf-8"))
                self._state = data.get("state", "disabled")
                self._config = ChronicleConfig.from_dict(data.get("config", {}))
                self._total_runtime = data.get("total_runtime", 0.0)
            except Exception as e:
                logger.warning("Failed to load chronicle state: %s", e)

    def _save(self) -> None:
        """Persist state and config to disk."""
        data = {
            "state": self._state,
            "config": self._config.to_dict(),
            "total_runtime": self._total_runtime,
        }
        CHRONICLE_STATE_FILE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def _log_event(self, event: str, **extra) -> None:
        """Append a JSONL event to the chronicle log."""
        entry = {
            "timestamp": time.time(),
            "event": event,
            "state": self._state,
            **extra,
        }
        try:
            with CHRONICLE_LOG_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    # ---- Lifecycle ----

    def start(self) -> bool:
        """Start or resume recording. Returns True if state changed."""
        if self._state == "running":
            return False
        if not self._config.enabled:
            logger.warning("Chronicle is disabled in config; cannot start")
            return False
        self._state = "running"
        self._started_at = time.time()
        self._paused_at = None
        self._save()
        self._log_event("started")
        logger.info("Chronicle recording started (placeholder — no native backend)")
        return True

    def pause(self) -> bool:
        """Pause recording. Returns True if state changed."""
        if self._state != "running":
            return False
        self._state = "paused"
        self._paused_at = time.time()
        if self._started_at:
            self._total_runtime += self._paused_at - self._started_at
        self._save()
        self._log_event("paused")
        return True

    def resume(self) -> bool:
        """Resume from paused. Returns True if state changed."""
        if self._state != "paused":
            return False
        self._state = "running"
        self._started_at = time.time()
        self._paused_at = None
        self._save()
        self._log_event("resumed")
        return True

    def stop(self) -> bool:
        """Stop recording. Returns True if state changed."""
        if self._state == "disabled":
            return False
        if self._state == "running" and self._started_at:
            self._total_runtime += time.time() - self._started_at
        self._state = "disabled"
        self._started_at = None
        self._paused_at = None
        self._save()
        self._log_event("stopped")
        logger.info("Chronicle recording stopped")
        return True

    def toggle(self) -> bool:
        """Toggle between running and paused/disabled."""
        if self._state == "running":
            return self.pause()
        elif self._state == "paused":
            return self.resume()
        else:
            return self.start()

    # ---- Queries ----

    def get_state(self) -> dict:
        """Return current state and runtime info."""
        current_runtime = self._total_runtime
        if self._state == "running" and self._started_at:
            current_runtime += time.time() - self._started_at
        return {
            "state": self._state,
            "config": self._config.to_dict(),
            "runtime_seconds": round(current_runtime, 1),
            "started_at": self._started_at,
            "note": "Placeholder: native capture backend not plugged in",
        }

    def get_config(self) -> dict:
        """Return current config."""
        return self._config.to_dict()

    def set_config(self, config: dict) -> dict:
        """Update configuration. Does not restart recording."""
        if "enabled" in config:
            self._config.enabled = bool(config["enabled"])
        if "fps" in config:
            self._config.fps = max(1, min(60, int(config["fps"])))
        if "quality" in config:
            self._config.quality = max(1, min(100, int(config["quality"])))
        if "output_dir" in config:
            self._config.output_dir = str(config["output_dir"])
        self._save()
        self._log_event("config_updated", config=self._config.to_dict())
        return self._config.to_dict()


# Singleton
chronicle = ChronicleManager()
