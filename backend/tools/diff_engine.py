# Diff/merge engine — line-by-line unified diff, patch apply, 3-way merge
from __future__ import annotations
import difflib, os, tempfile, subprocess, shutil, re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

@dataclass
class DiffHunk:
    old_start: int; old_count: int
    new_start: int; new_count: int
    lines: list[str] = field(default_factory=list)

class DiffEngine:
    """Compute, apply, and merge diffs for text files."""

    def compute_diff(self, original: str, modified: str, context_lines: int = 3) -> list[dict]:
        """Line-by-line unified diff between two strings.
        Returns list of hunks as dicts."""
        if original and not original.endswith("\n"):
            original += "\n"
        if modified and not modified.endswith("\n"):
            modified += "\n"
        orig_lines = original.splitlines(keepends=True)
        mod_lines = modified.splitlines(keepends=True)
        diff = difflib.unified_diff(
            orig_lines, mod_lines,
            fromfile="original", tofile="modified", n=context_lines
        )
        diff_text = "".join(diff)
        hunks = self._parse_hunks(diff_text)
        return [{
            "old_start": h.old_start, "old_count": h.old_count,
            "new_start": h.new_start, "new_count": h.new_count,
            "lines": h.lines
        } for h in hunks]

    def _parse_hunks(self, diff_text: str) -> list[DiffHunk]:
        hunks = []
        current = None
        hunk_pat = re.compile(r"^@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@")
        for line in diff_text.splitlines():
            m = hunk_pat.match(line)
            if m:
                if current:
                    hunks.append(current)
                current = DiffHunk(
                    old_start=int(m.group(1)),
                    old_count=int(m.group(2)) if m.group(2) else 1,
                    new_start=int(m.group(3)),
                    new_count=int(m.group(4)) if m.group(4) else 1,
                )
            elif current is not None:
                current.lines.append(line)
        if current:
            hunks.append(current)
        return hunks

    def apply_diff(self, filepath: str, diff_text: str) -> bool:
        """Apply a unified diff patch to a file."""
        if not diff_text.strip():
            return False
        try:
            fpath = Path(filepath)
            if not fpath.exists():
                return False
            original = fpath.read_text(encoding="utf-8")
            result = self._apply_patch_inline(original, diff_text)
            if result is not None:
                fpath.write_text(result, encoding="utf-8")
                return True
            return self._patch_native(filepath, diff_text)
        except Exception:
            return False

    def _apply_patch_inline(self, original: str, diff_text: str) -> str | None:
        """Try applying a simple unified diff in pure Python."""
        lines = original.splitlines()
        diff_lines = diff_text.splitlines()
        result = []
        i = 0
        hunk_re = re.compile(r"^@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@")
        in_header = True
        for dline in diff_lines:
            if in_header and not dline.startswith("@@"):
                continue
            in_header = False
            m = hunk_re.match(dline)
            if m:
                old_start = int(m.group(1))
                old_count = int(m.group(2)) if m.group(2) else 1
                if old_start > 0:
                    result.extend(lines[i:old_start - 1])
                    i = old_start - 1 + old_count
            elif dline.startswith("+"):
                result.append(dline[1:])
            elif dline.startswith("-"):
                continue
            elif dline.startswith(" "):
                result.append(dline[1:])
        result.extend(lines[i:])
        if result != lines:
            return "\n".join(result) + ("\n" if original.endswith("\n") else "")
        return None

    def _patch_native(self, filepath: str, diff_text: str) -> bool:
        try:
            proc = subprocess.run(
                ["patch", "-u", "--batch", filepath],
                input=diff_text, capture_output=True, text=True, timeout=10
            )
            return proc.returncode == 0
        except Exception:
            return False

    def three_way_merge(self, base: str, ours: str, theirs: str) -> tuple[str, list[dict]]:
        """Three-way merge with conflict markers.
        Returns (merged_text, conflicts_list)."""
        if not base.endswith("\n"):
            base += "\n"
        if not ours.endswith("\n"):
            ours += "\n"
        if not theirs.endswith("\n"):
            theirs += "\n"
        base_lines = base.splitlines()
        ours_lines = ours.splitlines()
        theirs_lines = theirs.splitlines()
        merged, conflicts = self._merge_blocks(base_lines, ours_lines, theirs_lines)
        return "\n".join(merged) + "\n", conflicts

    def _merge_blocks(self, base: list[str], ours: list[str], theirs: list[str]) -> tuple[list[str], list[dict]]:
        matcher_ours = difflib.SequenceMatcher(None, base, ours)
        matcher_theirs = difflib.SequenceMatcher(None, base, theirs)
        op_ours = matcher_ours.get_opcodes()
        op_theirs = matcher_theirs.get_opcodes()
        merged = []
        conflicts = []
        conflict_num = 0

        def theirs_changed_in(i1, i2):
            for tag, b1, b2, _, _ in op_theirs:
                if tag != "equal" and b1 < i2 and b2 > i1:
                    return True
            return False

        def get_theirs_block(i1, i2):
            result = []
            for tag, b1, b2, t1, t2 in op_theirs:
                if b1 < i2 and b2 > i1:
                    if tag == "equal":
                        overlap_start = max(b1, i1)
                        overlap_end = min(b2, i2)
                        off = overlap_start - b1
                        result.extend(theirs[t1 + off:t1 + off + (overlap_end - overlap_start)])
                    elif tag in ("replace", "insert"):
                        result.extend(theirs[t1:t2])
            return result

        for tag, i1, i2, j1, j2 in op_ours:
            if tag == "equal":
                their_block = get_theirs_block(i1, i2)
                if their_block and their_block != ours[j1:j2]:
                    conflict_num += 1
                    merged.append(f"<<<<<<< OURS (conflict {conflict_num})")
                    merged.extend(ours[j1:j2])
                    merged.append("=======")
                    merged.extend(their_block)
                    merged.append(f">>>>>>> THEIRS (conflict {conflict_num})")
                    conflicts.append({
                        "conflict": conflict_num,
                        "our_lines": list(ours[j1:j2]),
                        "their_lines": list(their_block)
                    })
                else:
                    merged.extend(ours[j1:j2])
            else:
                if theirs_changed_in(i1, i2):
                    conflict_num += 1
                    merged.append(f"<<<<<<< OURS (conflict {conflict_num})")
                    merged.extend(ours[j1:j2] if ours[j1:j2] else ["<deleted>"])
                    tblock = get_theirs_block(i1, i2)
                    merged.append("=======")
                    merged.extend(tblock if tblock else ["<deleted>"])
                    merged.append(f">>>>>>> THEIRS (conflict {conflict_num})")
                    conflicts.append({
                        "conflict": conflict_num,
                        "our_lines": list(ours[j1:j2]) if ours[j1:j2] else ["<deleted>"],
                        "their_lines": list(tblock) if tblock else ["<deleted>"]
                    })
                else:
                    merged.extend(ours[j1:j2])
        return merged, conflicts


diff_engine = DiffEngine()
