import sys, os
sys.path.insert(0, r"D:\codex_Projects\Aurora")
os.chdir(r"D:\codex_Projects\Aurora")
import uvicorn
from backend.api import app
uvicorn.run(app, host="127.0.0.1", port=9876, log_level="info")