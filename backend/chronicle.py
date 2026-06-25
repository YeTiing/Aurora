"""Aurora Chronicle — Pure Python screen capture via mss + ffmpeg.

No Rust dependency. FastAPI-native. Matches Codex Chronicle API surface.
"""
from __future__ import annotations
import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data"
STATE_FILE = DATA_DIR / "chronicle_state.json"
ChronicleStatus = Literal["running", "paused", "stopped", "error"]


@dataclass
class ChronicleConfig:
    enabled: bool = False
    fps: int = 5
    quality: int = 80
    output_dir: str = ""

    def __post_init__(self):
        if not self.output_dir:
            self.output_dir = str(DATA_DIR / "captures")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ChronicleConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class ChronicleManager:
    """Screen capture via mss (DXGI-backed, pure Python) + ffmpeg pipe."""

    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._instance_id = uuid.uuid4().hex[:12]
        self._status: ChronicleStatus = "stopped"
        self._config = ChronicleConfig()
        self._capture_task: asyncio.Task | None = None
        self._frames_captured: int = 0
        self._started_at: float = 0.0
        self._current_output: str = ""
        self._load()

    @property
    def config(self) -> ChronicleConfig:
        return self._config

    def _load(self) -> None:
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                self._config = ChronicleConfig.from_dict(data.get("config", {}))
            except Exception:
                pass

    def _save(self) -> None:
        STATE_FILE.write_text(
            json.dumps({"config": self._config.to_dict(), "status": self._status}, indent=2),
            encoding="utf-8",
        )

    # ── Lifecycle ──

    async def start(self) -> dict:
        if self._status == "running":
            return {"status": "already_running"}

        output_dir = Path(self._config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(output_dir / f"chronicle_{time.strftime('%Y%m%d_%H%M%S')}.mp4")
        self._current_output = output_path

        try:
            import mss
            import numpy as np
        except ImportError:
            self._status = "error"
            return {"status": "error", "message": "mss not installed. pip install mss"}

        try:
            subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        except Exception:
            self._status = "error"
            return {"status": "error", "message": "ffmpeg not found. Install from https://ffmpeg.org"}

        self._status = "running"
        self._started_at = time.time()
        self._frames_captured = 0
        self._save()

        self._capture_task = asyncio.create_task(self._capture_loop(output_path))
        return {"status": "started", "output": output_path}

    async def _capture_loop(self, output_path: str):
        import mss
        fps = self._config.fps
        frame_interval = 1.0 / fps

        with mss.mss() as sct:
            monitor = sct.monitors[0]
            W, H = monitor["width"], monitor["height"]

        ffmpeg_cmd = [
            "ffmpeg", "-y", "-f", "rawvideo", "-vcodec", "rawvideo",
            "-s", f"{W}x{H}", "-pix_fmt", "bgra", "-r", str(fps), "-i", "-",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            "-pix_fmt", "yuv420p", output_path,
        ]
        proc = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        try:
            with mss.mss() as sct:
                while self._status == "running":
                    frame = sct.grab(sct.monitors[0])
                    if proc.stdin:
                        proc.stdin.write(frame.bgra)
                        await proc.stdin.drain()
                    self._frames_captured += 1
                    await asyncio.sleep(frame_interval)
        except Exception as e:
            logger.error("Capture error: %s", e)
            self._status = "error"
        finally:
            if proc.stdin:
                proc.stdin.close()
            await proc.wait()

    async def pause(self) -> dict:
        if self._status != "running":
            return {"status": self._status}
        self._status = "paused"
        self._save()
        return {"status": "paused"}

    async def resume(self) -> dict:
        if self._status != "paused":
            return {"status": self._status}
        self._status = "running"
        self._save()
        return {"status": "resumed"}

    async def stop(self) -> dict:
        if self._status in ("stopped", "error"):
            return {"status": self._status}
        self._status = "stopped"
        self._save()
        if self._capture_task:
            self._capture_task.cancel()
        runtime = round(time.time() - self._started_at, 1) if self._started_at else 0
        return {"status": "stopped", "frames_captured": self._frames_captured,
                "runtime_secs": runtime, "output": self._current_output}

    # ── Queries ──

    def get_state(self) -> dict:
        runtime = 0.0
        if self._status == "running" and self._started_at:
            runtime = round(time.time() - self._started_at, 1)
        return {
            "status": self._status,
            "config": self._config.to_dict(),
            "frames_captured": self._frames_captured,
            "runtime_secs": runtime,
            "output": self._current_output,
        }

    def get_config(self) -> dict:
        return self._config.to_dict()

    def set_config(self, d: dict) -> dict:
        if "enabled" in d:
            self._config.enabled = bool(d["enabled"])
        if "fps" in d:
            self._config.fps = max(1, min(60, int(d["fps"])))
        if "quality" in d:
            self._config.quality = max(1, min(100, int(d["quality"])))
        if "output_dir" in d:
            self._config.output_dir = str(d["output_dir"])
        self._save()
        return self._config.to_dict()


chronicle = ChronicleManager()
