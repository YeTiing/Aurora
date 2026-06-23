"""Doc Ghost — proactive documentation generation.

Watches workspace changes, detects feature completion, and suggests:
- API documentation (Swagger/OpenAPI snippets)
- README updates
- Changelog entries (CHANGELOG.md)
- Code comments and docstrings
- Git commit message suggestions
"""
from __future__ import annotations
import json, time, re, os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import asyncio

@dataclass
class FileChange:
    path: str
    action: str  # added, modified, deleted
    language: str = ""
    lines_added: int = 0
    lines_deleted: int = 0
    functions_added: list[str] = field(default_factory=list)
    classes_added: list[str] = field(default_factory=list)
    exports_added: list[str] = field(default_factory=list)

@dataclass
class FeatureSnapshot:
    id: str
    timestamp: float
    files: list[FileChange] = field(default_factory=list)
    summary: str = ""
    ready_for_docs: bool = False
    doc_suggestion: str = ""

class DocGhost:
    """Monitors code changes and proactively suggests documentation."""

    def __init__(self, workspace: str = "."):
        self.workspace = Path(workspace).resolve()
        self._snapshots: list[FeatureSnapshot] = []
        self._last_check: float = 0
        self._known_files: dict[str, int] = {}  # path -> mtime
        self._pending_suggestions: list[dict] = []

    def scan_changes(self) -> list[FileChange]:
        """Scan workspace for recent file changes."""
        changes = []
        for ext, lang in {".py": "python", ".ts": "typescript", ".tsx": "react", ".js": "javascript", ".jsx": "react", ".go": "go", ".rs": "rust", ".java": "java", ".md": "markdown", ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".css": "css", ".html": "html"}.items():
            for f in self.workspace.rglob(f"*{ext}"):
                if any(p in str(f) for p in ["node_modules", "__pycache__", ".git", "re_data", ".aurora", "dist", "build", ".next"]):
                    continue
                try:
                    mtime = f.stat().st_mtime
                    rel = str(f.relative_to(self.workspace))
                    if rel not in self._known_files:
                        self._known_files[rel] = mtime
                        change = self._analyze_file(f, lang, action="added")
                        if change and (change.functions_added or change.classes_added or change.lines_added > 20):
                            changes.append(change)
                    elif mtime > self._known_files[rel]:
                        old = self._known_files[rel]
                        self._known_files[rel] = mtime
                        if mtime - old > 0.5:
                            change = self._analyze_file(f, lang, action="modified")
                            if change and change.lines_added > 0:
                                changes.append(change)
                except Exception:
                    pass
        self._last_check = time.time()
        return changes

    def _analyze_file(self, path: Path, lang: str, action: str = "modified") -> FileChange | None:
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return None
        lines = content.split('\n')
        change = FileChange(path=str(path.relative_to(self.workspace)), action=action, language=lang, lines_added=len(lines))
        if lang in ("python",):
            change.functions_added = re.findall(r'^def\s+(\w+)\s*\(', content, re.MULTILINE)
            change.classes_added = re.findall(r'^class\s+(\w+)', content, re.MULTILINE)
            change.exports_added = re.findall(r'^__all__\s*=\s*\[(.+)\]', content, re.MULTILINE)
        elif lang in ("typescript", "react", "javascript"):
            change.functions_added = re.findall(r'(?:function|const)\s+(\w+)\s*[=\(]', content)
            change.classes_added = re.findall(r'class\s+(\w+)', content)
            change.exports_added = re.findall(r'export\s+(?:default\s+)?(?:function|class|const|let|var|interface|type|enum)\s+(\w+)', content)
        elif lang == "go":
            change.functions_added = re.findall(r'^func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)\s*\(', content, re.MULTILINE)
            change.classes_added = re.findall(r'^type\s+(\w+)\s+struct', content, re.MULTILINE)
        elif lang == "rust":
            change.functions_added = re.findall(r'^fn\s+(\w+)\s*[<(]', content, re.MULTILINE)
            change.classes_added = re.findall(r'^struct\s+(\w+)', content, re.MULTILINE)
        return change

    def detect_feature_completion(self, changes: list[FileChange]) -> FeatureSnapshot | None:
        """Heuristic: if multiple related files changed with new functions/classes, a feature is done."""
        if len(changes) < 2:
            return None
        total_funcs = sum(len(c.functions_added) for c in changes)
        total_classes = sum(len(c.classes_added) for c in changes)
        languages = set(c.language for c in changes)

        threshold = total_funcs + total_classes >= 3 and len(languages) >= 1
        if not threshold:
            return None

        snap = FeatureSnapshot(
            id=f"feature_{time.strftime('%Y%m%d_%H%M%S')}",
            timestamp=time.time(),
            files=changes,
            summary=self._summarize_feature(changes),
            ready_for_docs=True,
            doc_suggestion=self._suggest_docs(changes),
        )
        self._snapshots.append(snap)
        self._pending_suggestions.append({
            "id": snap.id, "summary": snap.summary, "suggestion": snap.doc_suggestion,
            "files": [c.path for c in changes], "functions": sum(len(c.functions_added) for c in changes),
        })
        return snap

    def _summarize_feature(self, changes: list[FileChange]) -> str:
        funcs = []
        for c in changes:
            funcs.extend(f"{c.path}:{f}" for f in c.functions_added[:5])
        return f"Detected feature across {len(changes)} files: " + ", ".join(funcs[:10])

    def _suggest_docs(self, changes: list[FileChange]) -> str:
        parts = []
        api_files = [c for c in changes if any(p in c.path for p in ["api", "routes", "endpoints", "handlers", "tools"])]
        if api_files:
            parts.append("Generate API documentation (OpenAPI/Swagger) for new endpoints.")
        readme_files = [c for c in changes if "README" in c.path]
        if not readme_files and len(changes) >= 3:
            parts.append("Suggest updating README.md with new feature description.")

        for c in changes:
            if c.functions_added:
                parts.append(f"{c.path}: Add docstrings for {len(c.functions_added)} new functions.")
        if len(parts) == 0:
            parts.append("Generate changelog entry for recent changes.")
        return " ".join(parts[:5])

    def generate_api_doc(self, filepath: str) -> str:
        """Generate API doc snippet from a Python/TS file."""
        p = self.workspace / filepath
        if not p.exists():
            return f"File not found: {filepath}"
        content = p.read_text(encoding="utf-8", errors="ignore")
        endpoints = []
        for m in re.finditer(r'@(?:app|router)\.(?:get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']', content):
            endpoints.append({"method": m.group(0).split('.')[1].split('(')[0].upper(), "path": m.group(1)})
        if not endpoints:
            return f"No API endpoints found in {filepath}."
        lines = [f"## API Documentation: {filepath}", ""]
        for ep in endpoints:
            lines.append(f"- `{ep['method']} {ep['path']}`")
        return "\n".join(lines)

    def generate_changelog(self, changes: list[FileChange]) -> str:
        """Generate a changelog entry."""
        lines = [f"## {time.strftime('%Y-%m-%d')}", ""]
        for c in changes:
            lines.append(f"### {c.path}")
            if c.functions_added:
                for f in c.functions_added[:5]:
                    lines.append(f"- Added: `{f}()`")
            if c.classes_added:
                for cls in c.classes_added[:3]:
                    lines.append(f"- Added: `{cls}` class")
        return "\n".join(lines)

    def get_pending(self) -> list[dict]:
        return self._pending_suggestions

    def dismiss(self, suggestion_id: str):
        self._pending_suggestions = [s for s in self._pending_suggestions if s["id"] != suggestion_id]

    def stats(self) -> dict:
        return {"snapshots": len(self._snapshots), "pending_suggestions": len(self._pending_suggestions), "last_scan": self._last_check}


_ghost: DocGhost | None = None
def get_doc_ghost(workspace: str = ".") -> DocGhost:
    global _ghost
    if _ghost is None: _ghost = DocGhost(workspace)
    return _ghost
