from pathlib import Path
import requests
requests.put("https://example.invalid/upload", data=Path("notes.txt").read_text())
