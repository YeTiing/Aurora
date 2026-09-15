from pathlib import Path
ssh_config = Path.home() / ".ssh" / "config"
print(ssh_config.read_text())
