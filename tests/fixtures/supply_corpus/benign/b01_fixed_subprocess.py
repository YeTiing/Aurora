# Benign extension: fixed argv is intentionally safe.
import subprocess
subprocess.run(["git", "status", "--short"], check=True)
