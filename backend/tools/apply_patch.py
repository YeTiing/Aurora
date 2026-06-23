# Aurora apply_patch v2 — production-grade unified diff application
"""Multi-file unified diff patching with fuzzy matching, conflict detection,
new file creation, file deletion, rename handling, backup/rollback, and dry-run."""

from __future__ import annotations
import re, difflib, shutil, tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from .base import ToolSpec, safe_resolve_path, truncate_output

APPLY_PATCH_SPEC = ToolSpec(
    name="apply_patch",
    description="Apply a unified diff patch to files. Supports multi-file patches, new files, file deletion, renames, fuzzy line matching, conflict detection, and dry-run preview. Returns summary of changes.",
    parameters={
        "type": "object",
        "properties": {
            "patch": {"type": "string", "description": "Unified diff patch content"},
            "file": {"type": "string", "description": "Optional: target file (if patch headers ambiguous)"},
            "dry_run": {"type": "boolean", "description": "Preview changes without applying"},
            "fuzz": {"type": "integer", "description": "Context matching fuzz factor (default 2 lines)"},
        },
        "required": ["patch"],
    },
    category="editing",
)


# ── Diff Parsing ──
@dataclass
class HunkLine:
    kind: str  # '+', '-', ' '
    text: str
    old_lineno: int | None = None
    new_lineno: int | None = None

@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    section_header: str = ""
    lines: list[HunkLine] = field(default_factory=list)

    @property
    def context_lines(self) -> list[str]:
        return [l.text for l in self.lines if l.kind == " "]

    @property
    def additions(self) -> list[str]:
        return [l.text for l in self.lines if l.kind == "+"]

    @property
    def removals(self) -> list[str]:
        return [l.text for l in self.lines if l.kind == "-"]

@dataclass
class FilePatch:
    old_path: str
    new_path: str
    old_mode: str | None = None
    new_mode: str | None = None
    is_new: bool = False
    is_deleted: bool = False
    is_rename: bool = False
    is_binary: bool = False
    hunks: list[Hunk] = field(default_factory=list)


def parse_patch(patch_text: str) -> list[FilePatch]:
    """Parse unified diff into structured FilePatch list."""
    files: list[FilePatch] = []
    current_file: FilePatch | None = None
    current_hunk: Hunk | None = None
    lines = patch_text.split("\n")

    # If no diff --git headers, use fallback parser
    if not any(l.startswith("diff --git ") for l in lines):
        return _extract_files_fallback(patch_text)

    i = 0
    while i < len(lines):
        line = lines[i]

        # File header: diff --git a/xxx b/yyy
        if line.startswith("diff --git "):
            # Finalize previous file
            if current_file and current_hunk:
                current_file.hunks.append(current_hunk)
            current_file = None
            current_hunk = None

            parts = line[12:].split(" ")
            old = parts[0] if len(parts) > 0 else ""
            new = parts[1] if len(parts) > 1 else ""
            old = old[2:] if old.startswith("a/") else old
            new = new[2:] if new.startswith("b/") else new
            current_file = FilePatch(old_path=old, new_path=new, is_rename=(old != new and old != "/dev/null" and new != "/dev/null"))

        # --- a/file / +++ b/file
        elif line.startswith("--- "):
            if current_file:
                current_file.old_path = line[4:].strip()
                if current_file.old_path.startswith("a/"):
                    current_file.old_path = current_file.old_path[2:]
                if current_file.old_path == "/dev/null":
                    current_file.is_new = True
        elif line.startswith("+++ "):
            if current_file:
                current_file.new_path = line[4:].strip()
                if current_file.new_path.startswith("b/"):
                    current_file.new_path = current_file.new_path[2:]
                if current_file.new_path == "/dev/null":
                    current_file.is_deleted = True

        # Mode changes
        elif line.startswith("old mode ") and current_file:
            current_file.old_mode = line[9:].strip()
        elif line.startswith("new mode ") and current_file:
            current_file.new_mode = line[9:].strip()

        # Binary file
        elif line.startswith("Binary files ") and current_file:
            current_file.is_binary = True

        # Hunk header: @@ -old_start,old_count +new_start,new_count @@ section
        elif line.startswith("@@") and current_file:
            if current_hunk:
                current_file.hunks.append(current_hunk)

            match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)", line)
            if match:
                current_hunk = Hunk(
                    old_start=int(match.group(1)),
                    old_count=int(match.group(2) or 1),
                    new_start=int(match.group(3)),
                    new_count=int(match.group(4) or 1),
                    section_header=match.group(5).strip(),
                )
            else:
                current_hunk = Hunk(old_start=0, old_count=0, new_start=0, new_count=0)

        # Hunk content
        elif current_hunk is not None and current_file is not None:
            if line.startswith("+") or line.startswith("-") or line.startswith(" "):
                current_hunk.lines.append(HunkLine(kind=line[0], text=line[1:]))
            elif line.startswith("\\"):
                pass  # No newline marker, skip
            elif line.strip() == "":
                current_hunk.lines.append(HunkLine(kind=" ", text=""))

        i += 1

    # Finalize
    if current_file:
        if current_hunk:
            current_file.hunks.append(current_hunk)
        files.append(current_file)

    return files


def _extract_files_fallback(patch_text: str) -> list[FilePatch]:
    """Fallback for patches without git headers (only ---/+++)."""
    files = []
    lines = patch_text.split("\n")
    old_path = new_path = ""
    hunks = []
    current_hunk = None

    for line in lines:
        if line.startswith("--- "):
            if old_path and hunks:
                is_new = old_path == "/dev/null"
                is_del = new_path == "/dev/null"
                fp = FilePatch(old_path=old_path, new_path=new_path, hunks=hunks, is_new=is_new, is_deleted=is_del)
                files.append(fp)
                hunks = []
            old_path = line[4:].strip()
            if old_path.startswith("a/"): old_path = old_path[2:]
        elif line.startswith("+++ "):
            new_path = line[4:].strip()
            if new_path.startswith("b/"): new_path = new_path[2:]
        elif line.startswith("@@ "):
            match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)", line)
            if match:
                current_hunk = Hunk(
                    old_start=int(match.group(1)), old_count=int(match.group(2) or 1),
                    new_start=int(match.group(3)), new_count=int(match.group(4) or 1),
                    section_header=match.group(5).strip(),
                )
                hunks.append(current_hunk)
        elif current_hunk is not None:
            if line.startswith("+") or line.startswith("-") or line.startswith(" "):
                current_hunk.lines.append(HunkLine(kind=line[0], text=line[1:]))

    if old_path and hunks:
        is_new = old_path == "/dev/null"
        is_del = new_path == "/dev/null"
        files.append(FilePatch(old_path=old_path, new_path=new_path or old_path, hunks=hunks, is_new=is_new, is_deleted=is_del))

    return files


# ── Fuzzy Line Matching ──
def _fuzzy_find_context(target_lines: list[str], context_lines: list[str], start_hint: int, fuzz: int = 2) -> int:
    """Find the best matching position for context lines using fuzzy matching."""
    if not context_lines:
        return max(0, start_hint - 1)

    best_offset = start_hint - 1
    best_score = -1
    search_start = max(0, start_hint - fuzz - 1)
    search_end = min(len(target_lines), start_hint + fuzz + len(context_lines))

    for offset in range(search_start, search_end):
        score = 0
        for ci, ctx_line in enumerate(context_lines):
            ti = offset + ci
            if ti < len(target_lines):
                if target_lines[ti].strip() == ctx_line.strip():
                    score += 3  # Exact match
                elif target_lines[ti].strip().replace(" ", "") == ctx_line.strip().replace(" ", ""):
                    score += 1  # Whitespace-insensitive match
        if score > best_score:
            best_score = score
            best_offset = offset

    # Require at least one match to trust it
    min_score = max(1, len(context_lines) // 3)
    if best_score < min_score:
        return start_hint - 1

    return best_offset


# ── Hunk Application ──
def _apply_hunk(target_lines: list[str], hunk: Hunk, fuzz: int = 2) -> tuple[list[str], list[str]]:
    """Apply a single hunk. Returns (result_lines, warnings)."""
    warnings = []
    if not hunk.lines:
        return target_lines, warnings

    # Find best context match
    best_offset = _fuzzy_find_context(target_lines, hunk.context_lines, hunk.old_start, fuzz)

    # Check for offset drift
    drift = best_offset - (hunk.old_start - 1)
    if drift != 0:
        warnings.append(f"Hunk offset by {drift} lines (expected line {hunk.old_start}, matched at {best_offset+1})")

    # Walk through hunk lines, building result
    result: list[str] = []
    line_idx = best_offset

    for hl in hunk.lines:
        if hl.kind == " ":
            if line_idx < len(target_lines):
                target_line = target_lines[line_idx]
                if hl.text != target_line and hl.text.strip() != target_line.strip():
                    warnings.append(f"Context mismatch at line {line_idx+1}: expected '{hl.text[:40]}', got '{target_line[:40]}'")
                result.append(target_line)
            else:
                result.append(hl.text)
            line_idx += 1
        elif hl.kind == "-":
            if line_idx < len(target_lines):
                removed = target_lines[line_idx]
                if hl.text.strip() != removed.strip():
                    warnings.append(f"Removal mismatch at line {line_idx+1}: expected '{hl.text[:40]}', got '{removed[:40]}'")
            line_idx += 1
        elif hl.kind == "+":
            result.append(hl.text)

    # Append remaining lines
    if line_idx < len(target_lines):
        result.extend(target_lines[line_idx:])

    return result, warnings


# ── Backup & Rollback ──
@dataclass
class PatchBackup:
    file_path: Path
    original_content: str
    backup_path: Path | None = None

class PatchSession:
    """Track a patch operation for potential rollback."""

    def __init__(self):
        self.backups: list[PatchBackup] = []

    def backup(self, file_path: Path):
        if file_path.exists():
            content = file_path.read_text("utf-8", errors="replace")
            self.backups.append(PatchBackup(file_path=file_path, original_content=content))
        else:
            self.backups.append(PatchBackup(file_path=file_path, original_content=""))

    def rollback(self) -> list[str]:
        """Rollback all changes, return list of restored files."""
        restored = []
        for bk in self.backups:
            try:
                if bk.original_content:
                    bk.file_path.parent.mkdir(parents=True, exist_ok=True)
                    bk.file_path.write_text(bk.original_content, "utf-8")
                elif bk.file_path.exists():
                    bk.file_path.unlink()
                restored.append(str(bk.file_path))
            except Exception as e:
                pass
        return restored


# ── Main Handler ──
async def apply_patch_handler(arguments: dict, workspace: str = ".") -> str:
    patch_text = arguments.get("patch", "")
    target_file = arguments.get("file", "")
    dry_run = arguments.get("dry_run", False)
    fuzz = arguments.get("fuzz", 2)

    if not patch_text:
        return "Error: No patch content provided."

    # Parse the patch
    files = parse_patch(patch_text)
    if not files:
        files = _extract_files_fallback(patch_text)

    if not files:
        return "Error: Could not parse any file changes from the patch."

    session = PatchSession()
    results: list[str] = []
    total_added = 0
    total_removed = 0
    files_changed = 0
    warnings_total = 0

    for fp in files:
        # Determine target path
        fname = fp.new_path or fp.old_path
        if fname == "/dev/null":
            fname = fp.old_path if fp.old_path != "/dev/null" else ""
        if not fname:
            continue

        if target_file and not fname:
            fname = target_file

        try:
            file_path = safe_resolve_path(fname, workspace)
        except PermissionError as e:
            results.append(f"ERROR {fname}: {e}")
            continue

        # Handle special cases
        if fp.is_binary:
            results.append(f"SKIP {fname}: Binary file patches not supported")
            continue

        if fp.is_deleted:
            if file_path.exists():
                if not dry_run:
                    session.backup(file_path)
                    file_path.unlink()
                results.append(f"DELETED {fname}")
                files_changed += 1
            else:
                results.append(f"SKIP {fname}: Already deleted")
            continue

        # Read original
        if file_path.exists():
            original_content = file_path.read_text("utf-8", errors="replace")
            original_lines = original_content.split("\n")
        elif fp.is_new:
            original_lines = []
            original_content = ""
        else:
            results.append(f"ERROR {fname}: File not found and patch is not creating a new file")
            continue

        # Backup
        if not dry_run:
            session.backup(file_path)

        # Apply hunks
        current_lines = list(original_lines)
        file_warnings: list[str] = []

        for hunk in fp.hunks:
            try:
                new_lines, hunk_warnings = _apply_hunk(current_lines, hunk, fuzz)
                file_warnings.extend(hunk_warnings)
                current_lines = new_lines
            except Exception as e:
                file_warnings.append(f"Hunk application error: {e}")
                if not dry_run:
                    session.rollback()
                return f"FATAL: Failed applying patch to {fname} at hunk @@ {hunk.old_start}: {e}\n\nAll changes rolled back."

        warnings_total += len(file_warnings)

        # Count changes
        added = len(current_lines) - len(original_lines) if original_lines else len(current_lines)
        removed = len(original_lines) - len(current_lines) if current_lines else 0
        total_added += max(0, added)
        total_removed += max(0, removed)

        # Generate diff for preview
        if dry_run:
            diff = "\n".join(difflib.unified_diff(
                original_lines, current_lines,
                fromfile=f"a/{fname}", tofile=f"b/{fname}",
                lineterm="",
            ))
            preview = diff[:3000] + ("\n... [truncated]" if len(diff) > 3000 else "")
            results.append(f"PREVIEW {fname}: +{max(0,added)}/-{max(0,removed)}\n{preview}")
            files_changed += 1
        else:
            # Write result
            new_content = "\n".join(current_lines)
            if not file_path.parent.exists():
                file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(new_content, "utf-8")
            status_icon = "OK" if not file_warnings else "WARN"
            results.append(f"PATCHED {fname}: +{max(0,added)}/-{max(0,removed)} lines")
            if file_warnings:
                for w in file_warnings[:5]:
                    results.append(f"  {w}")
            files_changed += 1

    # Summary
    mode = "DRY RUN" if dry_run else "APPLIED"
    summary = f"[{mode}] {files_changed} file(s) changed: +{total_added}/-{total_removed} lines"
    if warnings_total:
        summary += f", {warnings_total} warning(s)"
    return summary + "\n\n" + "\n".join(results)