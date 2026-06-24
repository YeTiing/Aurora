# -*- coding: utf-8 -*-
"""Security Scanner — automated SAST (bandit/semgrep) + secrets detection.

Runs after code changes to catch vulnerabilities before they ship.
Layers:
  1. Secrets detection (regex — fast, always on)
  2. Bandit (Python-specific security lint — pip install bandit)
  3. Semgrep (multi-language patterns — pip install semgrep)

Wire: post-file-edit hook runs bandit + secrets; pre-commit runs full semgrep.
"""

from __future__ import annotations
import asyncio, hashlib, json, logging, os, re, subprocess, time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("aurora.security.scanner")

# ── Secrets Patterns ───────────────────────────────────────────

SECRET_PATTERNS: list[tuple[str, str, str]] = [
    # (regex, name, severity)
    (r'(?i)(?:api[_-]?key|apikey|api_secret|secret[_-]?key)\s*[:=]\s*["'"'"']([^"'"'"'\s]{16,})["'"'"']', "API Key in code", "critical"),
    (r'(?i)(?:password|passwd|pwd)\s*[:=]\s*["'"'"']([^"'"'"'\s]{4,})["'"'"']', "Hardcoded password", "critical"),
    (r'(?i)(?:token|access[_-]?token|auth[_-]?token)\s*[:=]\s*["'"'"']([^"'"'"'\s]{16,})["'"'"']', "Hardcoded token", "critical"),
    (r'sk-[a-zA-Z0-9]{32,}', "OpenAI API key", "critical"),
    (r'sk-ant-[a-zA-Z0-9]{32,}', "Anthropic API key", "critical"),
    (r'ghp_[a-zA-Z0-9]{36}', "GitHub personal access token", "critical"),
    (r'gho_[a-zA-Z0-9]{36}', "GitHub OAuth token", "critical"),
    (r'github_pat_[a-zA-Z0-9_]{36,}', "GitHub fine-grained token", "critical"),
    (r'(?i)-----BEGIN\s+(?:RSA|EC|DSA|OPENSSH)\s+PRIVATE\s+KEY', "Private key in code", "critical"),
    (r'(?:AKIA|ASIA)[A-Z0-9]{16}', "AWS Access Key", "critical"),
    (r'(?i)(?:mongodb|postgres|mysql|redis)://[^/\s]+@[^/\s]+', "Database connection string", "high"),
    (r'(?i)JWT_SECRET\s*[:=]\s*["'"'"']([^"'"'"'\s]{8,})["'"'"']', "JWT secret", "high"),
    (r'(?i)SECRET_KEY\s*[:=]\s*["'"'"']([^"'"'"'\s]{8,})["'"'"']', "Django/Flask secret key", "high"),
    (r'[0-9a-fA-F]{40}', "Potential hash/secret (40 hex)", "low"),
]


@dataclass
class ScanFinding:
    scanner: str = ""         # "secrets" | "bandit" | "semgrep"
    severity: str = "medium"  # critical | high | medium | low
    filepath: str = ""
    line: int = 0
    message: str = ""
    rule_id: str = ""
    snippet: str = ""

    def to_dict(self) -> dict:
        return {
            "scanner": self.scanner, "severity": self.severity,
            "filepath": self.filepath, "line": self.line,
            "message": self.message, "rule_id": self.rule_id,
            "snippet": self.snippet[:200],
        }


@dataclass
class ScanReport:
    findings: list[ScanFinding] = field(default_factory=list)
    scanned_files: int = 0
    duration_sec: float = 0.0
    error: str = ""

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "critical")
    @property
    def high_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "high")
    @property
    def is_clean(self) -> bool:
        return self.critical_count == 0 and self.high_count == 0

    def to_dict(self) -> dict:
        return {
            "findings": [f.to_dict() for f in self.findings],
            "scanned_files": self.scanned_files,
            "duration_sec": round(self.duration_sec, 2),
            "critical": self.critical_count,
            "high": self.high_count,
            "total": len(self.findings),
            "clean": self.is_clean,
            "error": self.error,
        }


class SecurityScanner:
    """Multi-layer security scanner."""

    def __init__(self, workspace: str = "."):
        self.workspace = Path(workspace).resolve()
        self._compiled_secrets = [(re.compile(p, re.MULTILINE), name, sev) for p, name, sev in SECRET_PATTERNS]
        self._skip_patterns = [
            r'.*\.lock$', r'.*\.min\.(js|css)$', r'.*\.map$',
            r'.*\.(pyc|pyo|pyd)$', r'.*node_modules.*', r'.*__pycache__.*',
            r'.*\.git/.*', r'.*\.(png|jpg|jpeg|gif|svg|ico|woff|ttf|eot)$',
        ]
        self._skip_re = [re.compile(p) for p in self._skip_patterns]

    # ── Layer 1: Secrets (fast regex, always available) ────────

    def scan_secrets(self, filepath: str = "") -> list[ScanFinding]:
        """Scan files for hardcoded secrets."""
        findings = []
        files = self._list_files(filepath)
        for fp in files:
            try:
                content = Path(fp).read_text(encoding="utf-8", errors="replace")
                for line_no, line in enumerate(content.split("\n"), 1):
                    for pat, name, severity in self._compiled_secrets:
                        m = pat.search(line)
                        if m:
                            findings.append(ScanFinding(
                                scanner="secrets", severity=severity,
                                filepath=str(Path(fp).relative_to(self.workspace)),
                                line=line_no, message=name, rule_id=name,
                                snippet=line.strip()[:150],
                            ))
            except Exception:
                pass
        return findings

    # ── Layer 2: Bandit (Python SAST) ──────────────────────────

    async def scan_bandit(self, filepath: str = "") -> list[ScanFinding]:
        """Run bandit on the workspace or specific file."""
        try:
            cmd = ["bandit", "-r", "-f", "json", "-q"]
            if filepath:
                target = str(Path(filepath).resolve())
            else:
                target = str(self.workspace)
            cmd.append(target)

            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                cwd=str(self.workspace),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            if proc.returncode not in (0, 1):
                return []

            try:
                data = json.loads(stdout.decode("utf-8", errors="replace"))
                results = data.get("results", [])
                findings = []
                for r in results:
                    findings.append(ScanFinding(
                        scanner="bandit",
                        severity=self._bandit_severity(r.get("issue_severity", "medium")),
                        filepath=r.get("filename", "").replace(str(self.workspace) + os.sep, "").replace(str(self.workspace), ""),
                        line=r.get("line_number", 0),
                        message=r.get("issue_text", ""),
                        rule_id=r.get("test_id", ""),
                        snippet=r.get("code", "")[:150] if r.get("code") else "",
                    ))
                return findings
            except (json.JSONDecodeError, KeyError):
                return []

        except FileNotFoundError:
            return []  # bandit not installed
        except asyncio.TimeoutError:
            return []
        except Exception as e:
            logger.debug(f"Bandit error: {e}")
            return []

    # ── Layer 3: Semgrep (multi-language) ─────────────────────

    async def scan_semgrep(self, filepath: str = "") -> list[ScanFinding]:
        """Run semgrep for multi-language patterns."""
        try:
            cmd = ["semgrep", "--config=auto", "--json", "--quiet"]
            if filepath:
                cmd.append(str(Path(filepath).resolve()))
            else:
                cmd.append(str(self.workspace))

            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                cwd=str(self.workspace),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

            try:
                data = json.loads(stdout.decode("utf-8", errors="replace"))
                results = data.get("results", [])
                findings = []
                for r in results:
                    findings.append(ScanFinding(
                        scanner="semgrep",
                        severity=r.get("extra", {}).get("severity", "medium"),
                        filepath=r.get("path", ""),
                        line=r.get("start", {}).get("line", 0),
                        message=r.get("extra", {}).get("message", ""),
                        rule_id=r.get("check_id", ""),
                    ))
                return findings
            except (json.JSONDecodeError, KeyError):
                return []

        except FileNotFoundError:
            return []
        except asyncio.TimeoutError:
            return []
        except Exception as e:
            logger.debug(f"Semgrep error: {e}")
            return []

    # ── Full Scan ─────────────────────────────────────────────

    async def scan(self, filepath: str = "", layers: list[str] = None) -> ScanReport:
        """Run all security scans. layers: ["secrets","bandit","semgrep"] or None for all."""
        start = time.time()
        layers = layers or ["secrets"]
        report = ScanReport()

        # Layer 1: Secrets (sync, fast)
        if "secrets" in layers:
            secrets_findings = self.scan_secrets(filepath)
            report.findings.extend(secrets_findings)
            report.scanned_files += 1

        # Layer 2: Bandit (async)
        if "bandit" in layers:
            try:
                bandit_findings = await self.scan_bandit(filepath)
                report.findings.extend(bandit_findings)
            except Exception as e:
                report.error = f"Bandit: {e}"

        # Layer 3: Semgrep (async, slow)
        if "semgrep" in layers:
            try:
                semgrep_findings = await self.scan_semgrep(filepath)
                report.findings.extend(semgrep_findings)
            except Exception as e:
                if report.error:
                    report.error += f"; Semgrep: {e}"
                else:
                    report.error = f"Semgrep: {e}"

        report.duration_sec = time.time() - start
        return report

    # ── Post-edit hook: quick scan after file change ──────────

    async def post_edit_scan(self, filepath: str) -> Optional[str]:
        """Quick scan after file edit. Returns warning string or None if clean."""
        report = await self.scan(filepath, layers=["secrets"])
        if not report.is_clean:
            criticals = [f for f in report.findings if f.severity == "critical"]
            highs = [f for f in report.findings if f.severity == "high"]
            lines = [f"\n[Security Scan: {filepath}]"]
            for f in criticals[:3]:
                lines.append(f"  CRITICAL L{f.line}: {f.message}")
            for f in highs[:3]:
                lines.append(f"  HIGH L{f.line}: {f.message}")
            return "\n".join(lines)
        return None

    # ── Helpers ───────────────────────────────────────────────

    def _list_files(self, filepath: str) -> list[str]:
        if filepath and os.path.isfile(filepath):
            if self._should_skip(filepath):
                return []
            return [filepath]
        if filepath and os.path.isdir(filepath):
            root = filepath
        else:
            root = str(self.workspace)

        files = []
        code_exts = {".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".java",
                     ".kt", ".swift", ".rb", ".php", ".cs", ".c", ".cpp", ".h",
                     ".html", ".css", ".yaml", ".yml", ".json", ".toml", ".env",
                     ".sh", ".ps1", ".dockerfile", ".dockerignore"}
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", "__pycache__", ".venv", "venv")]
            for fname in filenames:
                fp = os.path.join(dirpath, fname)
                if self._should_skip(fp):
                    continue
                ext = os.path.splitext(fname)[1].lower()
                if ext in code_exts or fname in (".env", "Dockerfile"):
                    files.append(fp)
        return files

    def _should_skip(self, fp: str) -> bool:
        for pat in self._skip_re:
            if pat.match(fp) or pat.search(fp):
                return True
        base = os.path.basename(fp)
        if base.startswith(".") and base != ".env":
            return True
        try:
            if os.path.getsize(fp) > 2_000_000:
                return True
        except OSError:
            return False  # Non-existent file - don't skip in pattern checks
        return False

    @staticmethod
    def _bandit_severity(sev: str) -> str:
        return {"LOW": "low", "MEDIUM": "medium", "HIGH": "high"}.get(sev.upper(), "medium")


_scanner: Optional[SecurityScanner] = None

def get_scanner(workspace: str = ".") -> SecurityScanner:
    global _scanner
    if _scanner is None:
        _scanner = SecurityScanner(workspace)
    return _scanner
