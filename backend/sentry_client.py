# Sentry integration for Aurora
from __future__ import annotations
import os, sys, traceback

_sentry_initialized = False

def init_sentry(dsn: str = "", sample_rate: float = 1.0):
    global _sentry_initialized
    if _sentry_initialized:
        return True
    dsn = dsn or os.environ.get("AURORA_SENTRY_DSN", "")
    if not dsn:
        return False
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=dsn,
            traces_sample_rate=sample_rate,
            profiles_sample_rate=sample_rate,
            environment=os.environ.get("AURORA_ENV", "development"),
            release="aurora@0.2.0",
        )
        _sentry_initialized = True
        return True
    except ImportError:
        return False

def capture_exception(exc: Exception, context: dict = None):
    if not _sentry_initialized:
        return
    try:
        import sentry_sdk
        sentry_sdk.capture_exception(exc, extras=context or {})
    except: pass

def capture_message(msg: str, level: str = "info"):
    if not _sentry_initialized:
        return
    try:
        import sentry_sdk
        sentry_sdk.capture_message(msg, level=level)
    except: pass