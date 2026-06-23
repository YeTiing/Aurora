# 工具类模块 — 日志 / 异步 / 类型
from __future__ import annotations
import logging, sys, asyncio, time, functools, json
from pathlib import Path
from typing import Any

# ── 日志 ──
def setup_logging(level: str = "INFO", log_file: str | None = None):
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
    root = logging.getLogger("aurora")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    h = logging.StreamHandler(sys.stderr); h.setFormatter(fmt); root.addHandler(h)
    if log_file:
        fh = logging.FileHandler(log_file); fh.setFormatter(fmt); root.addHandler(fh)
    return root

logger = setup_logging()

# ── 异步工具 ──
def async_retry(max_retries: int = 3, base_delay: float = 1.0, backoff: float = 2.0):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            delay = base_delay
            last_error = None
            for attempt in range(max_retries):
                try: return await func(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    if attempt < max_retries - 1:
                        logger.warning(f"Retry {attempt+1}/{max_retries} for {func.__name__}: {e}")
                        await asyncio.sleep(delay); delay *= backoff
            raise last_error
        return wrapper
    return decorator

async def gather_with_limit(*coros, limit: int = 5):
    sem = asyncio.Semaphore(limit)
    async def limited(coro):
        async with sem: return await coro
    return await asyncio.gather(*(limited(c) for c in coros))

# ── 时间工具 ──
class Timer:
    def __init__(self, name: str = ""): self.name = name; self.start = time.time()
    def elapsed(self) -> float: return time.time() - self.start
    def __enter__(self): return self
    def __exit__(self, *args):
        logger.debug(f"{self.name} took {self.elapsed():.3f}s")

# ── JSON 工具 ──
def safe_json_loads(text: str) -> Any:
    try: return json.loads(text)
    except json.JSONDecodeError:
        import re
        match = re.search(r'\{[\s\S]*\}|\[[\s\S]*\]', text)
        if match:
            try: return json.loads(match.group())
            except: pass
        return None

def safe_json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str, indent=2)

# ── 文件工具 ──
def find_project_root(start: str | Path = ".") -> Path | None:
    current = Path(start).resolve()
    for _ in range(20):
        if (current / ".git").exists() or (current / "aurora.json").exists():
            return current
        if current.parent == current: break
        current = current.parent
    return None

# ── Hash ──
import hashlib
def file_hash(path: str | Path, algo: str = "md5") -> str:
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""): h.update(chunk)
    return h.hexdigest()

def content_hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:12]