from pathlib import Path
# A pinned local data file, not a dependency installer.
manifest = Path("package-lock.json")
if manifest.exists():
    manifest.read_text(encoding="utf-8")
