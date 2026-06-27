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
    port = int(config.get("server.port", 9876))
    print(f"  Aurora AI Agent v0.2.0")
    print(f"  http://{host}:{port}")
    print(f"  API docs: http://{host}:{port}/docs")

    # Run curator maintenance if needed
    try:
        from backend.dual_memory import get_closed_loop
        cl = get_closed_loop()
        if cl.curator.should():
            print(f"  Curator: running maintenance...")
            cl.curator.light()
            cl.agent_memory.save()
            cl.user_profile.save()
    except Exception as e:
        print(f"  Curator: startup maintenance skipped ({e})", flush=True)

    uvicorn.run(app, host=host, port=port, log_level="info")
