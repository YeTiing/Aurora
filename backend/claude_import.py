"""Claude Import — Import sessions and projects from Claude Desktop/Code/Cowork.

Supports importing:
  - Claude Code transcripts (*.jsonl)
  - Claude Cowork project configs
  - Claude Desktop config

Imported data is converted to Aurora sessions and AGENTS.md files.
"""
from __future__ import annotations
import json, os, hashlib, shutil
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ImportResult:
    source: str
    sessions_imported: int = 0
    agents_md_imported: int = 0
    configs_imported: int = 0
    errors: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.sessions_imported + self.agents_md_imported + self.configs_imported


class ClaudeImporter:
    """Import Claude projects and sessions into Aurora."""

    CLAUDE_CODE_DIRS = [
        Path.home() / ".claude",
        Path.home() / ".claude" / "projects",
    ]

    TRANSCRIPTS_DIR = Path.home() / ".claude" / "transcripts"
    PROJECTS_DIR = Path.home() / ".claude" / "projects"
    DESKTOP_CONFIG = Path.home() / "claude_desktop_config.json"

    def __init__(self, import_dir: str | None = None):
        self._import_dir = Path(import_dir) if import_dir else Path(".aurora/imports")
        self._import_history_path = self._import_dir / "claude-import-history.json"
        self._import_dir.mkdir(parents=True, exist_ok=True)

    def _load_history(self) -> dict:
        if self._import_history_path.exists():
            return json.loads(self._import_history_path.read_text(encoding="utf-8"))
        return {"imported_hashes": [], "last_import": None}

    def _save_history(self, history: dict) -> None:
        self._import_history_path.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")

    def _hash_content(self, content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def find_claude_sources(self) -> dict:
        """Discover available Claude import sources."""
        sources = {}

        if self.TRANSCRIPTS_DIR.exists():
            transcripts = list(self.TRANSCRIPTS_DIR.glob("*.jsonl"))
            if transcripts:
                sources["transcripts"] = {
                    "path": str(self.TRANSCRIPTS_DIR),
                    "count": len(transcripts),
                    "files": [t.name for t in transcripts[:20]],
                }

        if self.PROJECTS_DIR.exists():
            projects = [d for d in self.PROJECTS_DIR.iterdir() if d.is_dir()]
            if projects:
                sources["projects"] = {
                    "path": str(self.PROJECTS_DIR),
                    "count": len(projects),
                    "names": [p.name for p in projects[:20]],
                }

        if self.DESKTOP_CONFIG.exists():
            sources["desktop_config"] = {
                "path": str(self.DESKTOP_CONFIG),
            }

        return sources

    def import_transcripts(self) -> ImportResult:
        """Import Claude Code transcripts into Aurora sessions."""
        result = ImportResult(source="claude_transcripts")

        if not self.TRANSCRIPTS_DIR.exists():
            result.errors.append("No Claude transcripts directory found")
            return result

        history = self._load_history()
        imported_hashes = set(history.get("imported_hashes", []))

        for jsonl_file in self.TRANSCRIPTS_DIR.glob("*.jsonl"):
            try:
                content_hash = self._hash_content(jsonl_file.read_text(encoding="utf-8"))
                if content_hash in imported_hashes:
                    result.skipped.append(jsonl_file.name)
                    continue

                # Copy to Aurora import directory
                dest = self._import_dir / "transcripts" / jsonl_file.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(jsonl_file, dest)

                imported_hashes.add(content_hash)
                result.sessions_imported += 1
            except Exception as e:
                result.errors.append(f"Failed to import {jsonl_file.name}: {e}")

        history["imported_hashes"] = list(imported_hashes)
        history["last_import"] = datetime.now().isoformat()
        self._save_history(history)

        return result

    def import_projects(self) -> ImportResult:
        """Import Claude Cowork projects."""
        result = ImportResult(source="claude_projects")

        if not self.PROJECTS_DIR.exists():
            result.errors.append("No Claude projects directory found")
            return result

        for project_dir in self.PROJECTS_DIR.iterdir():
            if not project_dir.is_dir():
                continue
            try:
                # Look for AGENTS.md or .claude/settings.json
                agents_md = project_dir / "AGENTS.md"
                if agents_md.exists():
                    dest = self._import_dir / "projects" / project_dir.name / "AGENTS.md"
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(agents_md, dest)
                    result.agents_md_imported += 1

                # Look for project configs
                settings = project_dir / ".claude" / "settings.json"
                if settings.exists():
                    dest = self._import_dir / "projects" / project_dir.name / "settings.json"
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(settings, dest)
                    result.configs_imported += 1
            except Exception as e:
                result.errors.append(f"Failed to import {project_dir.name}: {e}")

        return result

    def import_all(self) -> list[ImportResult]:
        """Run all imports."""
        results = []
        results.append(self.import_transcripts())
        results.append(self.import_projects())
        return results

    def get_imported_sessions(self) -> list[dict]:
        """List imported sessions."""
        transcripts_dir = self._import_dir / "transcripts"
        if not transcripts_dir.exists():
            return []

        sessions = []
        for f in transcripts_dir.glob("*.jsonl"):
            sessions.append({
                "name": f.name,
                "size": f.stat().st_size,
                "imported_at": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            })
        return sorted(sessions, key=lambda s: s["imported_at"], reverse=True)

    def get_imported_projects(self) -> list[dict]:
        """List imported projects."""
        projects_dir = self._import_dir / "projects"
        if not projects_dir.exists():
            return []

        projects = []
        for d in projects_dir.iterdir():
            if d.is_dir():
                files = [f.name for f in d.iterdir()]
                projects.append({
                    "name": d.name,
                    "files": files,
                    "imported_at": datetime.fromtimestamp(d.stat().st_mtime).isoformat(),
                })
        return sorted(projects, key=lambda p: p["imported_at"], reverse=True)


# Singleton
_importer: ClaudeImporter | None = None


def get_claude_importer(import_dir: str | None = None) -> ClaudeImporter:
    global _importer
    if _importer is None:
        _importer = ClaudeImporter(import_dir)
    return _importer
