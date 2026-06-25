"""Aurora Chronicle — Screen capture with Rust Sidecar + Named Pipe RPC.

Architecture matches Codex Chronicle:
  Rust Sidecar: \\.\pipe\aurora-chronicle-{id}  (DXGI Desktop Duplication)
  Python:       JSON-RPC 2.0 control + frame relay
  Config sync:  SharedObject codex_chronicle_config

When the Rust binary is compiled, it's used. Otherwise falls back to
Python mss-based capture (lower performance, same API surface).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Literal, Optional, Any

logger = logging.getLogger(__name__)

CHRONICLE_DATA_DIR = Path(__file__).parent / "data"
CHRONICLE_STATE_FILE = CHRONICLE_DATA_DIR / "chronicle_state.json"

ChronicleStatus = Literal["running", "paused", "stopped", "error"]

SIDECAR_BINARY = Path(__file__).parent / "chronicle_sidecar" / "target" / "release" / "chronicle-sidecar.exe"

@dataclass
class ChronicleConfig:
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
    """Screen capture with Rust DXGI sidecar or Python mss fallback."""

    def __init__(self):
        CHRONICLE_DATA_DIR.mkdir(parents=True, exist_ok=True)
        Path(self.config.output_dir).mkdir(parents=True, exist_ok=True)
        self._instance_id = uuid.uuid4().hex[:12]
        self._status: ChronicleStatus = "stopped"
        self._config = ChronicleConfig()
        self._sidecar_proc: subprocess.Popen | None = None
        self._pipe_reader: asyncio.StreamReader | None = None
        self._pipe_writer: asyncio.StreamWriter | None = None
        self._rpc_id: int = 0
        self._frames_captured: int = 0
        self._started_at: float = 0.0
        self._capture_task: asyncio.Task | None = None
        self._has_rust = SIDECAR_BINARY.exists()
    async def _launch_rust_sidecar(self, output_path: str) -> dict | None:
        """Try to launch the Rust sidecar. Returns result on success, None on failure."""
        try:
            self._sidecar_proc = subprocess.Popen(
                [str(SIDECAR_BINARY), f"aurora-chronicle-{self._instance_id}"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            await asyncio.sleep(0.5)

            try:
                import win32pipe
                import win32file
                import pywintypes
            except ImportError:
                logger.info("pywin32 not installed — cannot connect to Named Pipe. Install: pip install pywin32")
                if self._sidecar_proc:
                    self._sidecar_proc.terminate()
                return None

            pipe_handle = win32file.CreateFile(
                self.pipe_name,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0, None, win32file.OPEN_EXISTING, 0, None,
            )
            reader = asyncio.StreamReader()
            writer = asyncio.StreamWriter(pipe_handle, None, reader, asyncio.get_event_loop())
            self._pipe_reader = reader
            self._pipe_writer = writer

            result = await self._rpc_call("start", {"output_path": output_path})
            if "error" not in result:
                self._status = "running"
                self._started_at = time.time()
                self._frames_captured = 0
                self._save()
                return {"status": "started", "backend": "rust_dxgi", "output": output_path}
            return result
        except Exception as e:
            logger.warning("Rust sidecar failed: %s. Falling back.", e)
            if self._sidecar_proc:
                self._sidecar_proc.terminate()
            return None

        self._load()

    @property
    def config(self) -> ChronicleConfig:
        return self._config

    def _load(self) -> None:
        if CHRONICLE_STATE_FILE.exists():
            try:
                data = json.loads(CHRONICLE_STATE_FILE.read_text(encoding="utf-8"))
                self._config = ChronicleConfig.from_dict(data.get("config", {}))
            except Exception:
                pass

    def _save(self) -> None:
        CHRONICLE_STATE_FILE.write_text(
            json.dumps({"config": self._config.to_dict(), "status": self._status}, indent=2),
            encoding="utf-8",
        )

    @property
    def pipe_name(self) -> str:
        return rf"\\.\pipe\aurora-chronicle-{self._instance_id}"

    # ── RPC ──

    async def _rpc_call(self, method: str, params: Any = None) -> dict:
        """Send JSON-RPC 2.0 request to the sidecar and read response."""
        self._rpc_id += 1
        req = {"jsonrpc": "2.0", "method": method, "id": self._rpc_id}
        if params is not None:
            req["params"] = params

        if self._pipe_writer is None:
            return {"error": "No pipe connection"}

        try:
            self._pipe_writer.write((json.dumps(req) + "\n").encode())
            await self._pipe_writer.drain()

            if self._pipe_reader is None:
                return {"error": "No pipe reader"}
            line = await asyncio.wait_for(self._pipe_reader.readline(), timeout=5)
            return json.loads(line.decode())
        except asyncio.TimeoutError:
            return {"error": "RPC timeout"}
        except Exception as e:
            return {"error": str(e)}

    # ── Lifecycle ──

    async def start(self) -> dict:
        """Start recording. Launches Rust sidecar if available."""
        if self._status == "running":
            return {"status": "already_running"}

        output_dir = Path(self._config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(output_dir / f"chronicle_{time.strftime('%Y%m%d_%H%M%S')}.mp4")

        if self._has_rust:
            # ── Rust Sidecar path ──
            result = await self._launch_rust_sidecar(output_path)
            if result:
                return result

        # ── Python mss fallback ──
        try:
            import mss  # type: ignore
            import numpy as np

            self._status = "running"
            self._started_at = time.time()
            self._frames_captured = 0
            self._save()

            self._capture_task = asyncio.create_task(
                self._capture_loop_python(output_path)
            )
            return {"status": "started", "backend": "python_mss", "output": output_path}
        except ImportError:
            self._status = "error"
            return {"status": "error", "message": "mss not installed. pip install mss imageio-ffmpeg"}

    async def _capture_loop_python(self, output_path: str):
        """Python mss-based capture loop. Writes frames to ffmpeg pipe."""
        import mss
        import numpy as np

        fps = self._config.fps
        frame_interval = 1.0 / fps

        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-f", "rawvideo", "-vcodec", "rawvideo",
            "-s", "{W}x{H}", "-pix_fmt", "bgra",
            "-r", str(fps), "-i", "-",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            "-pix_fmt", "yuv420p",
            output_path,
        ]
        proc = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        with mss.mss() as sct:
            try:
                while self._status == "running":
                    frame = sct.grab(sct.monitors[0])
                    if proc.stdin:
                        proc.stdin.write(frame.bgra)
                        await proc.stdin.drain()
                    self._frames_captured += 1
                    await asyncio.sleep(frame_interval)
            except Exception as e:
                logger.error("Python capture error: %s", e)
            finally:
                if proc.stdin:
                    proc.stdin.close()
                await proc.wait()

    async def pause(self) -> dict:
        if self._status != "running":
            return {"status": self._status}
        self._status = "paused"
        self._save()
        if self._pipe_writer:
            await self._rpc_call("pause")
        return {"status": "paused"}

    async def resume(self) -> dict:
        if self._status != "paused":
            return {"status": self._status}
        self._status = "running"
        self._save()
        if self._pipe_writer:
            await self._rpc_call("resume")
        return {"status": "resumed"}

    async def stop(self) -> dict:
        if self._status in ("stopped", "error"):
            return {"status": self._status}
        self._status = "stopped"
        self._save()

        if self._pipe_writer:
            await self._rpc_call("stop")
            self._pipe_writer.close()
            self._pipe_reader = None
            self._pipe_writer = None

        if self._sidecar_proc:
            self._sidecar_proc.terminate()
            self._sidecar_proc = None

        if self._capture_task:
            self._capture_task.cancel()
            self._capture_task = None

        return {
            "status": "stopped",
            "frames_captured": self._frames_captured,
            "runtime_secs": round(time.time() - self._started_at, 1) if self._started_at else 0,
        }

    async def toggle(self) -> dict:
        if self._status == "running":
            return await self.pause()
        elif self._status == "paused":
            return await self.resume()
        else:
            return await self.start()

    # ── Queries ──

    def get_state(self) -> dict:
        backend = "rust_dxgi" if self._has_rust else "python_mss"
        if not self._has_rust and not self._sidecar_proc and self._status not in ("stopped", "error"):
            backend += " (not compiled — use 'cargo build --release' in backend/chronicle_sidecar/)"

        return {
            "status": self._status,
            "backend": backend,
            "config": self._config.to_dict(),
            "frames_captured": self._frames_captured,
            "runtime_secs": round(time.time() - self._started_at, 1) if self._started_at and self._status != "stopped" else 0,
            "sidecar_path": str(SIDECAR_BINARY) if self._has_rust else None,
            "pipe": self.pipe_name.replace("\\\\", "\\"),
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
