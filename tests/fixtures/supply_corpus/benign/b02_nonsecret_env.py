import os
# Non-secret configuration is a normal read.
mode = os.getenv("AURORA_MODE", "offline")
