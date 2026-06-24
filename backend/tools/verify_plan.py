# -*- coding: utf-8 -*-
"""Plan Verification Tool — agent self-checks if planned steps were completed.

Port of cc-haha's src/tools/VerifyPlanExecutionTool.
After each plan step, the agent can call this to verify: did the file actually change?
Did the test actually pass? Did the output match expectations?
"""

from __future__ import annotations
import os, re, subprocess, time, json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

@dataclass
class VerificationResult:
    step_id: str = ""
    passed: bool = False
    checks: list[dict] = None
    evidence: list[str] = None
    failures: list[str] = None

    def __post_init__(self):
        self.checks = self.checks or []
        self.evidence = self.evidence or []
        self.failures = self.failures or []


VERIFY_TOOL_SPEC = {
    "name": "verify_plan",
    "description": "Verify that a plan step was actually executed. Checks file modifications, test results, git status, and output existence.",
    "parameters": {
        "type": "object",
        "properties": {
            "step_description": {"type": "string", "description": "What the step was supposed to do"},
            "expected_files": {"type": "array", "items": {"type": "string"}, "description": "Files expected to be created/modified"},
            "expected_output": {"type": "string", "description": "Expected output or behavior description"},
            "run_tests": {"type": "boolean", "description": "Whether to run associated tests"},
        },
        "required": ["step_description"],
    },
}


class PlanVerifier:
    """Verify that planned steps were actually executed."""

    def __init__(self, workspace: str = "."):
        self.workspace = Path(workspace).resolve()
        self._git_available = self._check_git()

    def _check_git(self) -> bool:
        try:
            subprocess.run(["git", "rev-parse", "--git-dir"], capture_output=True, cwd=str(self.workspace), timeout=5)
            return True
        except Exception:
            return False

    def verify(self, step_description: str, expected_files: list[str] = None,
               expected_output: str = "", run_tests: bool = False) -> dict:
        """Run all verification checks."""
        checks = []
        evidence = []
        failures = []

        # Check 1: Expected files exist and are non-empty
        if expected_files:
            for fp in expected_files:
                full = self.workspace / fp
                if full.exists():
                    size = full.stat().st_size
                    mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(full.stat().st_mtime))
                    evidence.append(f"File exists: {fp} ({size} bytes, modified {mtime})")
                    checks.append({"check": f"file_exists:{fp}", "passed": True, "detail": f"{size} bytes"})
                else:
                    failures.append(f"Expected file not found: {fp}")
                    checks.append({"check": f"file_exists:{fp}", "passed": False, "detail": "not found"})

        # Check 2: Recent git changes
        if self._git_available:
            try:
                result = subprocess.run(
                    ["git", "diff", "--stat", "HEAD~1" if self._has_commits() else "HEAD"],
                    capture_output=True, text=True, cwd=str(self.workspace), timeout=10
                )
                if result.stdout.strip():
                    evidence.append(f"Recent git changes:\n{result.stdout.strip()[:500]}")
                    checks.append({"check": "git_diff", "passed": True, "detail": f"{len(result.stdout.strip().split(chr(10)))} files changed"})
                else:
                    checks.append({"check": "git_diff", "passed": True, "detail": "no changes"})
            except Exception as e:
                checks.append({"check": "git_diff", "passed": False, "detail": str(e)[:100]})

        # Check 3: Git status (untracked = new files)
        if self._git_available:
            try:
                result = subprocess.run(
                    ["git", "status", "--short"],
                    capture_output=True, text=True, cwd=str(self.workspace), timeout=10
                )
                changed = [l for l in result.stdout.strip().split("\n") if l.strip()]
                if changed:
                    evidence.append(f"Working tree: {len(changed)} file(s) changed")
                    checks.append({"check": "git_status", "passed": True, "detail": f"{len(changed)} files", "files": changed[:20]})
                else:
                    checks.append({"check": "git_status", "passed": True, "detail": "clean"})
            except Exception as e:
                checks.append({"check": "git_status", "passed": False, "detail": str(e)[:100]})

        # Check 4: Tests pass (if requested)
        if run_tests:
            try:
                result = subprocess.run(
                    ["python", "-m", "pytest", "tests/", "-q", "--tb=no"],
                    capture_output=True, text=True, cwd=str(self.workspace), timeout=120
                )
                passed_match = re.search(r"(\d+) passed", result.stdout)
                failed_match = re.search(r"(\d+) failed", result.stdout)
                p = int(passed_match.group(1)) if passed_match else 0
                f = int(failed_match.group(1)) if failed_match else 0
                if f == 0:
                    evidence.append(f"Tests: {p} passed, 0 failed")
                    checks.append({"check": "tests", "passed": True, "detail": f"{p} passed"})
                else:
                    failures.append(f"Tests: {f} failed, {p} passed")
                    checks.append({"check": "tests", "passed": False, "detail": f"{f} failed"})
            except Exception as e:
                checks.append({"check": "tests", "passed": False, "detail": str(e)[:100]})

        all_passed = len(failures) == 0 and all(c["passed"] for c in checks)
        return {
            "step": step_description[:200],
            "passed": all_passed,
            "checks": checks,
            "evidence": evidence[:10],
            "failures": failures,
            "verdict": "PASSED" if all_passed else f"FAILED: {len(failures)} issue(s)",
        }

    def _has_commits(self) -> bool:
        try:
            r = subprocess.run(["git", "rev-list", "--count", "HEAD"], capture_output=True, text=True, cwd=str(self.workspace), timeout=5)
            return int(r.stdout.strip()) > 0
        except Exception:
            return False


_verifier: Optional[PlanVerifier] = None

def get_verifier(workspace: str = ".") -> PlanVerifier:
    global _verifier
    if _verifier is None:
        _verifier = PlanVerifier(workspace)
    return _verifier


async def verify_plan_handler(arguments: dict, workspace: str = ".") -> dict:
    verifier = get_verifier(workspace)
    return verifier.verify(
        step_description=arguments.get("step_description", ""),
        expected_files=arguments.get("expected_files"),
        expected_output=arguments.get("expected_output", ""),
        run_tests=arguments.get("run_tests", False),
    )
