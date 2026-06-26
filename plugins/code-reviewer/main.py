"""code-reviewer plugin — automated code review utilities."""
from __future__ import annotations
import re, os
from pathlib import Path

PATTERNS = {
    "print_debug": (r"\bprint\(", "Debug print statement"),
    "todo_comment": (r"#\s*TODO", "TODO comment found"),
    "fixme_comment": (r"#\s*FIXME", "FIXME comment found"),
    "bare_except": (r"except\s*:", "Bare except clause"),
    "hardcoded_secret": (r"(?i)(password|secret|api_key|token)\s*=\s*['""][^'""]+['""]", "Potential hardcoded secret"),
}

def review_file(filepath: str) -> list[dict]:
    """Scan a file for common issues."""
    issues = []
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except Exception:
        return [{"file": filepath, "severity": "error", "message": "Could not read file"}]

    for i, line in enumerate(lines, 1):
        for name, (pattern, message) in PATTERNS.items():
            if re.search(pattern, line):
                issues.append({
                    "file": filepath,
                    "line": i,
                    "severity": "warning" if name != "hardcoded_secret" else "error",
                    "message": message,
                    "code": line.strip()[:120],
                })

    # Check file size
    if len(lines) > 500:
        issues.append({
            "file": filepath,
            "line": 0,
            "severity": "info",
            "message": f"Large file: {len(lines)} lines",
        })

    return issues

def review_directory(dirpath: str, extensions: list[str] | None = None) -> list[dict]:
    """Scan a directory for common issues."""
    if extensions is None:
        extensions = [".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs"]
    all_issues = []
    for root, dirs, files in os.walk(dirpath):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__", ".git")]
        for f in files:
            if any(f.endswith(ext) for ext in extensions):
                fp = os.path.join(root, f)
                issues = review_file(fp)
                all_issues.extend(issues)
                if len(all_issues) > 200:
                    break
        if len(all_issues) > 200:
            break
    return all_issues[:200]

def summary(issues: list[dict]) -> dict:
    """Summarize review results."""
    errors = [i for i in issues if i["severity"] == "error"]
    warnings = [i for i in issues if i["severity"] == "warning"]
    infos = [i for i in issues if i["severity"] == "info"]
    return {
        "total": len(issues),
        "errors": len(errors),
        "warnings": len(warnings),
        "info": len(infos),
        "files_affected": len(set(i["file"] for i in issues)),
    }

__all__ = ["review_file", "review_directory", "summary"]
