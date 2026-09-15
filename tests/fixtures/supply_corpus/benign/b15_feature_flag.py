import os
# This is a fixed, non-secret feature flag.
enabled = os.environ.get("FEATURE_ENABLED", "0")
