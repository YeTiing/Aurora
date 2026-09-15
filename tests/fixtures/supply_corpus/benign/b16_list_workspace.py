from pathlib import Path
root = Path("workspace").resolve()
for item in root.glob("*.txt"):
    item.stat()
