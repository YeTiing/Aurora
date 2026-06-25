"""Aurora server entry point. Usage: python main.py  or  python -m aurora"""
import sys, os
from pathlib import Path

# Auto-detect project root (parent of this file or CWD)
_project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(_project_root))
os.chdir(str(_project_root))

import uvicorn
from backend.api import app

host = os.environ.get("AURORA_HOST", "127.0.0.1")
port = int(os.environ.get("AURORA_PORT", "9876"))
uvicorn.run(app, host=host, port=port, log_level="info")
