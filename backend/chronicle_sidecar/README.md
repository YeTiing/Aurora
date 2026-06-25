# Aurora Chronicle Sidecar (Rust)

Screen capture via DXGI Desktop Duplication API, controlled over Named Pipe JSON-RPC 2.0.

## Architecture
```
Python (FastAPI)  <--JSON-RPC/2.0-->  Named Pipe  <-->  Rust Sidecar (DXGI Capture)
                                                    |
                                                    +--> ffmpeg (MP4 encode)
```

## Build
```powershell
# Requires: Rust + MSVC Build Tools + ffmpeg
cd backend/chronicle_sidecar
cargo build --release
# Binary at: target/release/chronicle-sidecar.exe
```

## Fallback
When the Rust binary is not compiled, the Python `chronicle.py` falls back to `mss`-based capture (pip install mss imageio-ffmpeg).

## Named Pipe Protocol
- Pipe: `\\.\pipe\aurora-chronicle-{instance_id}`
- Format: JSON-RPC 2.0, one message per line
- Methods: start, stop, pause, resume, get_state, set_config

## Match with Codex
| Codex | Aurora |
|-------|--------|
| Rust Sidecar (codex.exe embedded) | Rust Sidecar (standalone binary) |
| Named Pipe `\\.\pipe\codex-chronicle-*` | Named Pipe `\\.\pipe\aurora-chronicle-*` |
| JSON-RPC 2.0 | JSON-RPC 2.0 |
| DXGI Desktop Duplication | DXGI Desktop Duplication |
| ChronicleSidecarControlState | ChronicleManager status |
| Shared Object codex_chronicle_config | SharedObject codex_chronicle_config |
