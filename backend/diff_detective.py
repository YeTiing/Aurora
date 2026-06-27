"""Diff Detective — git bisect + blame root cause analysis.

When a bug surfaces, Detective traces it back to the exact commit and explains WHY.
Not just "line X changed" — it correlates related commits to tell the full story.
"""
from __future__ import annotations
import asyncio, re, json, time, subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

@dataclass
class CommitInfo:
    hash: str
    short_hash: str = ""
    author: str = ""
    date: str = ""
    message: str = ""
    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0

@dataclass
class BlameLine:
    line_no: int
    content: str
    commit_hash: str
    commit_short: str
    author: str
    date: str

@dataclass
class DetectiveReport:
    file: str
    suspicious_lines: list[BlameLine] = field(default_factory=list)
    recent_commits: list[CommitInfo] = field(default_factory=list)
    suspect_commits: list[CommitInfo] = field(default_factory=list)
    root_cause_hypothesis: str = ""
    bisect_result: dict = field(default_factory=dict)

class DiffDetective:
    """git bisect + blame analysis engine."""

    def __init__(self, workspace: str = "."):
        self.workspace = Path(workspace).resolve()

    async def _git(self, *args: str) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            "git", *args, cwd=str(self.workspace),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        return proc.returncode or 0, stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace")

    def _git_sync(self, *args: str) -> tuple[int, str, str]:
        r = subprocess.run(["git"] + list(args), cwd=str(self.workspace), capture_output=True, text=True, timeout=30)
        return r.returncode, r.stdout, r.stderr

    async def analyze_file(self, filepath: str, lines: list[int] | None = None) -> DetectiveReport:
        """Analyze a file: blame suspicious lines, find recent commits, build hypothesis."""
        _, blame_out, _ = await self._git("blame", "-L", "1,2000", "--line-porcelain", "--", filepath)
        blame_lines = self._parse_blame(blame_out)
        if lines:
            blame_lines = [bl for bl in blame_lines if bl.line_no in lines]

        _, log_out, _ = await self._git("log", "--oneline", "-30", "--", filepath)
        commits = self._parse_log(log_out)

        _, diff_log, _ = await self._git("log", "-p", "-10", "--", filepath)
        suspect = self._find_suspects(diff_log, blame_lines)

        hypothesis = self._build_hypothesis(blame_lines, suspect, filepath)
        return DetectiveReport(
            file=filepath, suspicious_lines=blame_lines[:30],
            recent_commits=commits[:15], suspect_commits=suspect[:8],
            root_cause_hypothesis=hypothesis,
        )

    async def bisect(self, bad_commit: str, good_commit: str, test_cmd: str) -> dict:
        """Automated git bisect: find the breaking commit."""
        self._git_sync("bisect", "start")
        self._git_sync("bisect", "bad", bad_commit)
        _, out, _ = self._git_sync("bisect", "good", good_commit)

        steps = []
        for _ in range(20):
            _, status, _ = self._git_sync("bisect", "run", "sh", "-c", test_cmd + "; exit $?")
            steps.append(status.strip())
            if "is the first bad commit" in status:
                break

        self._git_sync("bisect", "reset")
        return {"bad": bad_commit, "good": good_commit, "steps": steps, "test": test_cmd}

    async def trace_bug_origin(self, filepath: str, bug_description: str, target_lines: list[int] | None = None) -> dict:
        """Full analysis: blame + log + bisect if commits range known."""
        report = await self.analyze_file(filepath, target_lines)
        _, log_out, _ = await self._git("log", "--format=%H %s", "-50", "--", filepath)
        hashes = [l.split()[0] for l in log_out.strip().split('\n') if l]
        recent = hashes[:10] if hashes else []
        oldest = hashes[-1] if len(hashes) > 10 else (hashes[-1] if hashes else "")

        result = {
            "file": filepath,
            "bug": bug_description,
            "suspicious_lines": [{"line": bl.line_no, "content": bl.content[:100], "commit": bl.commit_short, "author": bl.author, "date": bl.date} for bl in report.suspicious_lines],
            "suspect_commits": [{"hash": c.short_hash, "message": c.message[:120], "author": c.author, "files": c.files_changed} for c in report.suspect_commits],
            "root_cause_hypothesis": report.root_cause_hypothesis,
            "recent_commits_range": {"newest": recent[0] if recent else "", "oldest": oldest},
            "bisect_ready": bool(recent and oldest and len(recent) > 2),
        }
        if result["bisect_ready"] and result["suspect_commits"]:
            result["bisect_command"] = f"git bisect start {recent[0]} {oldest}"
            result["suggested_test"] = f"python -m pytest tests/ -k 'test_related' -x"
        return result

    def _parse_blame(self, output: str) -> list[BlameLine]:
        lines = []
        current = {}
        for raw in output.split('\n'):
            if not raw.strip() and not raw.startswith('\t'):
                continue
            if re.match(r'^[0-9a-f]{40}\s', raw):
                parts = raw.split()
                current["hash"] = parts[0]
            elif raw.startswith("author "):
                current["author"] = raw[7:]
            elif raw.startswith("author-time "):
                try:
                    current["date"] = time.strftime('%Y-%m-%d', time.gmtime(int(raw.split()[-1])))
                except Exception: current["date"] = ""
                current["commit_short"] = current.get("hash", "")[:8]
            elif raw.startswith('\t'):
                current["content"] = raw[1:]
                lines.append(BlameLine(
                    line_no=len(lines)+1, content=current.get("content","")[:200],
                    commit_hash=current.get("hash",""), commit_short=current.get("commit_short",""),
                    author=current.get("author",""), date=current.get("date","")))
                current = {}
        return lines

    def _parse_log(self, output: str) -> list[CommitInfo]:
        commits = []
        for line in output.strip().split('\n'):
            if not line.strip(): continue
            m = re.match(r'^([0-9a-f]+)\s+(.+)', line)
            if m:
                commits.append(CommitInfo(hash=m.group(1), short_hash=m.group(1)[:8], message=m.group(2).strip()))
        return commits

    def _find_suspects(self, diff_log: str, blame_lines: list[BlameLine]) -> list[CommitInfo]:
        blame_commits = set(bl.commit_hash for bl in blame_lines)
        suspects = []
        for m in re.finditer(r'commit\s+([0-9a-f]{40})', diff_log):
            h = m.group(1)
            if h in blame_commits:
                msg = ""
                msg_m = re.search(rf'{re.escape(h)}\nAuthor:.*?\nDate:.*?\n\n\s+(.*)', diff_log[m.start():m.start()+500], re.DOTALL)
                if msg_m: msg = msg_m.group(1).strip()[:200]
                suspects.append(CommitInfo(hash=h, short_hash=h[:8], message=msg))
        return suspects

    def _build_hypothesis(self, blame_lines: list[BlameLine], suspects: list[CommitInfo], filepath: str) -> str:
        if not blame_lines and not suspects:
            return f"No suspicious changes found in {filepath}."
        parts = []
        by_commit: dict = {}
        for bl in blame_lines:
            by_commit.setdefault(bl.commit_short, []).append(bl)
        top = sorted(by_commit.items(), key=lambda x: -len(x[1]))[:5]
        for ch, bls in top:
            author = bls[0].author
            date = bls[0].date
            line_nums = ", ".join(str(b.line_no) for b in bls[:5])
            parts.append(f"Commit {ch} by {author} ({date}): changed lines {line_nums} ({len(bls)} total)")

        if suspects:
            parts.append(f"\n{len(suspects)} suspect commits found that match the changed lines.")
            for s in suspects[:3]:
                parts.append(f"  - {s.short_hash}: {s.message[:150]}")

        return "\n".join(parts)

_detective: DiffDetective | None = None
def get_detective(workspace: str = ".") -> DiffDetective:
    global _detective
    if _detective is None: _detective = DiffDetective(workspace)
    return _detective
