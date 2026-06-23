"""Aurora RE Engine - native reverse engineering workspace.

Built-in modules:
- session: SQLite RE session DB, request storage, interaction recording
- capture: HTTP/WS capture via mitmproxy hook + browser CDP
- deobfuscator: JS deobfuscation, syntax tree extraction, crypto call tracing
- miner: API endpoint discovery, secret/key extraction, GraphQL/SSE/WS detection
- signature: HMAC/signature generation chain tracing
- analyzer: auto scene detection, auth chain, crypto fingerprinting

No external skill dependency - pure Aurora code.
"""

from .session import RESession, RESessionManager, CapturedRequest
from .capture import CaptureEngine
from .deobfuscator import Deobfuscator
from .miner import APIMiner
from .analyzer import SceneAnalyzer
from .signature import SignatureTracer, get_signature_tracer

def init_workspace():
    from pathlib import Path
    p = Path("re_data")
    p.mkdir(exist_ok=True)
    (p / "sessions").mkdir(exist_ok=True)
    (p / "deobfuscated").mkdir(exist_ok=True)
    (p / "reports").mkdir(exist_ok=True)
    return p
