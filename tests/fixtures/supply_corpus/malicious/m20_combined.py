import os
import subprocess
name = os.getenv("DEPLOY_SECRET")
subprocess.run("echo " + name, shell=True)
