from pathlib import Path
import subprocess
subprocess.run(["python", "-m", "pytest", "-q"], check=False)
