"""Aurora 启动入口"""
import sys, uvicorn
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.stdout.reconfigure(encoding='utf-8')

from backend.config import init_config
from backend.api import app

config = init_config(".")

if __name__ == "__main__":
    host = config.get("server.host", "127.0.0.1")
    port = config.get("server.port", 9876)
    print(f"  Aurora AI Agent v0.1.0")
    print(f"  http://{host}:{port}")
    print(f"  API docs: http://{host}:{port}/docs")
    uvicorn.run(app, host=host, port=port, log_level="info")