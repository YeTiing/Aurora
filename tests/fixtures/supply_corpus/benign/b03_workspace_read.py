from pathlib import Path
workspace = Path("workspace")
text = (workspace / "README.txt").read_text(encoding="utf-8")
