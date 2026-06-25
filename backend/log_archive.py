"""Aurora Log Archive — compress and manage old log entries.

Archives old logs to gzipped JSONL files under .aurora/logs/archive/.
"""

from __future__ import annotations
import gzip, json, os, time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ARCHIVE_DIR = Path(os.environ.get("AURORA_HOME", ".aurora")) / "logs" / "archive"

@dataclass
class ArchiveInfo:
    name: str
    path: str
    size_bytes: int
    entry_count: int
    date_start: str
    date_end: str
    created_at: float

class LogArchiveManager:
    def __init__(self, base_dir: str | Path | None = None):
        base = Path(base_dir) if base_dir else Path.home() / ".aurora" / "sqlite"
        self._base = base
        self._db_path = base / "logs.sqlite"
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        import sqlite3
        self._sqlite3 = sqlite3

    def archive_old_logs(self, before_days: int = 30) -> ArchiveInfo | None:
        cutoff = time.time() - before_days * 86400
        if not self._db_path.exists():
            return None
        conn = self._sqlite3.connect(str(self._db_path))
        conn.row_factory = self._sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM logs WHERE ts < ? ORDER BY ts ASC",
                (int(cutoff),)
            ).fetchall()
            if not rows:
                conn.close()
                return None
            ts_start = rows[0]["ts"]
            ts_end = rows[-1]["ts"]
            d1 = time.strftime("%Y%m%d", time.localtime(ts_start))
            d2 = time.strftime("%Y%m%d", time.localtime(ts_end))
            name = f"logs_{d1}_{d2}.jsonl.gz"
            path = ARCHIVE_DIR / name
            count = 0
            with gzip.open(str(path), "wt", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(dict(r), default=str, ensure_ascii=False) + "\n")
                    count += 1
            conn.execute("DELETE FROM logs WHERE ts < ?", (int(cutoff),))
            conn.commit()
            return ArchiveInfo(
                name=name, path=str(path), size_bytes=path.stat().st_size,
                entry_count=count,
                date_start=time.strftime("%Y-%m-%d", time.localtime(ts_start)),
                date_end=time.strftime("%Y-%m-%d", time.localtime(ts_end)),
                created_at=time.time(),
            )
        finally:
            conn.close()

    def cleanup_old_logs(self, before_days: int = 90) -> int:
        cutoff = time.time() - before_days * 86400
        if not self._db_path.exists():
            return 0
        conn = self._sqlite3.connect(str(self._db_path))
        try:
            c = conn.execute("DELETE FROM logs WHERE ts < ?", (int(cutoff),))
            conn.commit()
            return c.rowcount
        finally:
            conn.close()

    def get_archive_list(self) -> list[dict]:
        archives = []
        if ARCHIVE_DIR.exists():
            for f in sorted(ARCHIVE_DIR.glob("*.jsonl.gz"), reverse=True):
                archives.append({
                    "name": f.name, "path": str(f),
                    "size_bytes": f.stat().st_size,
                    "created_at": f.stat().st_mtime,
                })
        return archives

    def restore_archive(self, archive_name: str) -> int:
        path = ARCHIVE_DIR / archive_name
        if not path.exists():
            return 0
        conn = self._sqlite3.connect(str(self._db_path))
        count = 0
        try:
            with gzip.open(str(path), "rt", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    conn.execute(
                        "INSERT OR IGNORE INTO logs (id, ts, ts_nanos, level, target, feedback_log_body, module_path, file, line, thread_id, process_uuid) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (d.get("id"), d.get("ts"), d.get("ts_nanos"), d.get("level"),
                         d.get("target"), d.get("feedback_log_body"), d.get("module_path"),
                         d.get("file"), d.get("line"), d.get("thread_id"), d.get("process_uuid"))
                    )
                    count += 1
            conn.commit()
        finally:
            conn.close()
        return count

    def get_log_stats(self) -> dict:
        stats = {"total_entries": 0, "db_size_bytes": 0, "by_level": {}, "date_range": {}}
        if self._db_path.exists():
            stats["db_size_bytes"] = self._db_path.stat().st_size
            conn = self._sqlite3.connect(str(self._db_path))
            try:
                total = conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
                stats["total_entries"] = total
                levels = conn.execute("SELECT level, COUNT(*) as c FROM logs GROUP BY level ORDER BY c DESC LIMIT 10").fetchall()
                stats["by_level"] = {r[0]: r[1] for r in levels}
                first = conn.execute("SELECT MIN(ts) FROM logs").fetchone()[0]
                last = conn.execute("SELECT MAX(ts) FROM logs").fetchone()[0]
                if first:
                    stats["date_range"] = {
                        "first": time.strftime("%Y-%m-%d", time.localtime(first)),
                        "last": time.strftime("%Y-%m-%d", time.localtime(last)),
                    }
            except Exception:
                pass
            finally:
                conn.close()
        return stats


log_archive = LogArchiveManager()
