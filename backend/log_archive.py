"""Log Archive Manager - archive and restore Aurora logs."""
from __future__ import annotations
import json, gzip, os, time
from pathlib import Path
from dataclasses import dataclass
from typing import Any


@dataclass
class ArchiveEntry:
    name: str
    path: str
    size_bytes: int
    date_start: str
    date_end: str
    log_count: int


class LogArchiveManager:
    """Manages log archiving: compress old logs, cleanup, restore, stats."""

    def __init__(self, base_dir: str | Path | None = None):
        base = Path(base_dir) if base_dir else Path.home() / ".aurora"
        self._archive_dir = base / "logs" / "archive"
        self._archive_dir.mkdir(parents=True, exist_ok=True)

    def archive_old_logs(self, before_days: int = 30) -> dict:
        """Archive logs older than `before_days` days to a compressed JSONL file."""
        from backend.sqlite_persistence import get_logs_db

        cutoff = time.time() - (before_days * 86400)
        db = get_logs_db()

        # Query old logs
        rows = db.db.execute(
            "SELECT * FROM logs WHERE timestamp < ? ORDER BY timestamp ASC",
            (cutoff,),
        ).fetchall()

        if not rows:
            return {"archived": 0, "message": "No old logs to archive"}

        logs = [dict(r) for r in rows]

        # Determine date range
        date_start = time.strftime("%Y%m%d", time.localtime(min(r["timestamp"] for r in logs)))
        date_end = time.strftime("%Y%m%d", time.localtime(max(r["timestamp"] for r in logs)))
        archive_name = f"logs_archive_{date_start}_{date_end}.jsonl.gz"
        archive_path = self._archive_dir / archive_name

        # Write compressed JSONL
        with gzip.open(str(archive_path), "wt", encoding="utf-8") as f:
            for log_entry in logs:
                f.write(json.dumps(log_entry, default=str, ensure_ascii=False) + "\n")

        # Delete archived logs from DB
        ids = [r["id"] for r in logs]
        placeholders = ",".join("?" for _ in ids)
        db.db.execute(f"DELETE FROM logs WHERE id IN ({placeholders})", tuple(ids))
        db.db.commit()

        size = archive_path.stat().st_size
        return {
            "archived": len(logs),
            "archive_name": archive_name,
            "size_bytes": size,
            "date_start": date_start,
            "date_end": date_end,
        }

    def cleanup_old_logs(self, before_days: int = 90) -> dict:
        """Delete logs older than `before_days` days (after archiving)."""
        from backend.sqlite_persistence import get_logs_db

        cutoff = time.time() - (before_days * 86400)
        db = get_logs_db()

        # Count before deleting
        row = db.db.execute(
            "SELECT COUNT(*) as cnt FROM logs WHERE timestamp < ?",
            (cutoff,),
        ).fetchone()

        count = row["cnt"] if row else 0
        if count > 0:
            db.db.execute("DELETE FROM logs WHERE timestamp < ?", (cutoff,))
            db.db.commit()

        return {"deleted": count, "before_days": before_days}

    def get_archive_list(self) -> list[dict]:
        """List archive files with size, date range, and log count."""
        archives = []
        if not self._archive_dir.exists():
            return archives

        for f in sorted(self._archive_dir.glob("logs_archive_*.jsonl.gz"), reverse=True):
            size = f.stat().st_size
            # Parse date range from filename
            name = f.name
            parts = name.replace("logs_archive_", "").replace(".jsonl.gz", "").split("_")
            date_start = parts[0] if len(parts) > 0 else "unknown"
            date_end = parts[1] if len(parts) > 1 else "unknown"

            # Count lines without decompressing the whole file
            log_count = 0
            try:
                with gzip.open(str(f), "rt", encoding="utf-8") as fh:
                    for _ in fh:
                        log_count += 1
            except Exception:
                log_count = -1

            archives.append({
                "name": name,
                "path": str(f),
                "size_bytes": size,
                "size_human": _human_size(size),
                "date_start": date_start,
                "date_end": date_end,
                "log_count": log_count,
            })

        return archives

    def restore_archive(self, archive_name: str) -> int:
        """Restore logs from an archive file back to the database. Returns count."""
        from backend.sqlite_persistence import get_logs_db

        archive_path = self._archive_dir / archive_name
        if not archive_path.exists():
            raise FileNotFoundError(f"Archive not found: {archive_name}")

        db = get_logs_db()
        restored = 0

        with gzip.open(str(archive_path), "rt", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                db.db.execute(
                    """INSERT OR IGNORE INTO logs
                       (id, timestamp, level, module, message, thread_id, process_uuid, estimated_bytes, metadata)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        entry.get("id"),
                        entry.get("timestamp", 0),
                        entry.get("level", "info"),
                        entry.get("module", ""),
                        entry.get("message", ""),
                        entry.get("thread_id", ""),
                        entry.get("process_uuid", ""),
                        entry.get("estimated_bytes", 0),
                        entry.get("metadata", "{}"),
                    ),
                )
                restored += 1

        db.db.commit()
        return restored

    def get_log_stats(self) -> dict:
        """Get log statistics: total, by level, by module, date range, size estimate."""
        from backend.sqlite_persistence import get_logs_db

        db = get_logs_db()

        total_row = db.db.execute("SELECT COUNT(*) as cnt, COALESCE(SUM(estimated_bytes), 0) as sz FROM logs").fetchone()
        total = total_row["cnt"] if total_row else 0
        total_est_bytes = total_row["sz"] if total_row else 0

        # By level
        level_rows = db.db.execute(
            "SELECT level, COUNT(*) as cnt FROM logs GROUP BY level ORDER BY cnt DESC"
        ).fetchall()
        by_level = {r["level"]: r["cnt"] for r in level_rows}

        # By module
        mod_rows = db.db.execute(
            "SELECT module, COUNT(*) as cnt FROM logs WHERE module != '' GROUP BY module ORDER BY cnt DESC LIMIT 20"
        ).fetchall()
        by_module = {r["module"]: r["cnt"] for r in mod_rows}

        # Date range
        first_row = db.db.execute("SELECT MIN(timestamp) as first_ts, MAX(timestamp) as last_ts FROM logs").fetchone()
        first_ts = first_row["first_ts"] if first_row and first_row["first_ts"] else 0
        last_ts = first_row["last_ts"] if first_row and first_row["last_ts"] else 0

        return {
            "total_logs": total,
            "estimated_size_bytes": total_est_bytes,
            "estimated_size_human": _human_size(total_est_bytes),
            "by_level": by_level,
            "by_module": by_module,
            "date_first": _fmt_ts(first_ts),
            "date_last": _fmt_ts(last_ts),
        }


def _human_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def _fmt_ts(ts: float) -> str:
    if ts <= 0:
        return "N/A"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
