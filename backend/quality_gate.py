# -*- coding: utf-8 -*-
"""Quality Gate — 自动化质量门禁系统。

Port of cc-haha's scripts/quality-gate/.
Runs tests → checks coverage → compares baseline → blocks degraded code.

Architecture:
  TestRunner → CoverageChecker → BaselineComparator → QualityReport

Usage:
  python -m backend.quality_gate         # Run all gates
  python -m backend.quality_gate --quick  # Fast mode (unit tests only)
  python -m backend.quality_gate --report # Generate report only
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# ── Data Models ─────────────────────────────────────────────────

@dataclass
class TestResult:
    """Results from a test run."""
    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    duration_sec: float = 0.0
    failures_list: list[dict] = field(default_factory=list)
    output: str = ""

    @property
    def pass_rate(self) -> float:
        return self.passed / max(self.total, 1) * 100

    @property
    def is_clean(self) -> bool:
        return self.failed == 0 and self.errors == 0


@dataclass
class CoverageResult:
    """Coverage analysis results."""
    pct: float = 0.0
    covered_lines: int = 0
    total_lines: int = 0
    uncovered_files: list[str] = field(default_factory=list)
    raw_output: str = ""


@dataclass
class BaselineRecord:
    """A stored baseline for comparison."""
    timestamp: str = ""
    test_pass_rate: float = 100.0
    coverage_pct: float = 0.0
    test_count: int = 0
    coverage_count: int = 0
    failures_summary: list[str] = field(default_factory=list)


@dataclass
class QualityReport:
    """Final quality gate report."""
    passed: bool = True
    test_result: TestResult | None = None
    coverage: CoverageResult | None = None
    baseline: BaselineRecord | None = None
    baseline_delta: dict[str, Any] = field(default_factory=dict)
    gates: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_sec: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


# ── Config ──────────────────────────────────────────────────────

@dataclass
class QualityGateConfig:
    """Quality gate configuration."""
    baseline_file: str = ".aurora/quality_baseline.json"
    min_pass_rate: float = 100.0          # 100% must pass
    min_coverage: float = 0.0             # No minimum by default
    coverage_decline_threshold: float = 2.0  # Allow 2% decline
    test_dir: str = "tests"
    pytest_args: list[str] = field(default_factory=lambda: ["-v", "--tb=short"])
    quick_mode: bool = False
    fail_fast: bool = True
    timeout_sec: int = 300

    @classmethod
    def from_args(cls, quick: bool = False) -> "QualityGateConfig":
        return cls(
            baseline_file=os.getenv("AURORA_QUALITY_BASELINE", ".aurora/quality_baseline.json"),
            min_pass_rate=float(os.getenv("AURORA_MIN_PASS_RATE", "100")),
            min_coverage=float(os.getenv("AURORA_MIN_COVERAGE", "0")),
            quick_mode=quick,
            fail_fast=os.getenv("AURORA_FAIL_FAST", "1") not in ("0", "false"),
        )


# ── Test Runner ─────────────────────────────────────────────────

class TestRunner:
    """Run pytest and parse results."""

    def __init__(self, config: QualityGateConfig):
        self.config = config

    def run(self) -> TestResult:
        """Run tests and return results."""
        start = time.time()

        args = ["pytest"] + self.config.pytest_args
        if self.config.quick_mode:
            args = ["pytest", "-x", "--tb=short", "-q"]
        args.append(self.config.test_dir)

        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=self.config.timeout_sec,
                cwd=os.getcwd(),
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
            output = proc.stdout + "\n" + proc.stderr
        except subprocess.TimeoutExpired:
            return TestResult(errors=1, output="Test run timed out", duration_sec=self.config.timeout_sec)
        except FileNotFoundError:
            return TestResult(errors=1, output="pytest not found. Install: pip install pytest")

        result = self._parse_output(output)
        result.duration_sec = time.time() - start
        result.output = output
        return result

    def _parse_output(self, output: str) -> TestResult:
        """Parse pytest output for test counts."""
        result = TestResult()

        # Look for pytest summary line: "X passed, Y failed, Z errors"
        import re
        # Pattern: "= X passed, Y failed in Z.Zs ="
        m = re.search(r"=+\s*(.*?)\s*=+", output)
        if m:
            summary = m.group(1)
            passed_m = re.search(r"(\d+)\s+passed", summary)
            failed_m = re.search(r"(\d+)\s+failed", summary)
            error_m = re.search(r"(\d+)\s+error", summary)

            if passed_m:
                result.passed = int(passed_m.group(1))
            if failed_m:
                result.failed = int(failed_m.group(1))
            if error_m:
                result.errors = int(error_m.group(1))

            result.total = result.passed + result.failed + result.errors

        # Extract failure details
        if result.failed > 0 or result.errors > 0:
            for line in output.split("\n"):
                if "FAILED" in line or "ERROR" in line:
                    result.failures_list.append({"detail": line.strip()[:200]})

        return result


# ── Coverage Checker ────────────────────────────────────────────

class CoverageChecker:
    """Run coverage analysis using coverage.py."""

    def __init__(self, config: QualityGateConfig):
        self.config = config

    def run(self) -> CoverageResult:
        """Run coverage and return results."""
        try:
            # Run coverage
            proc = subprocess.run(
                ["python", "-m", "coverage", "run", "-m", "pytest", self.config.test_dir, "-q", "--tb=no"],
                capture_output=True,
                text=True,
                timeout=self.config.timeout_sec,
                cwd=os.getcwd(),
            )

            # Get report
            proc2 = subprocess.run(
                ["python", "-m", "coverage", "report", "--format=json", "-o", ".aurora/coverage.json"],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=os.getcwd(),
            )

            # Also get text report
            proc3 = subprocess.run(
                ["python", "-m", "coverage", "report"],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=os.getcwd(),
            )

            return self._parse_coverage(proc3.stdout)

        except FileNotFoundError:
            return CoverageResult(raw_output="coverage not installed. pip install coverage")
        except Exception as e:
            return CoverageResult(raw_output=f"Coverage error: {e}")

    def _parse_coverage(self, text: str) -> CoverageResult:
        """Parse coverage report output."""
        import re

        # Look for TOTAL line
        for line in text.split("\n"):
            if "TOTAL" in line.upper():
                parts = line.split()
                # Format: "TOTAL    123    45    78%"
                for p in parts:
                    if "%" in p:
                        try:
                            pct = float(p.replace("%", ""))
                            return CoverageResult(pct=pct, raw_output=text)
                        except ValueError:
                            pass

        return CoverageResult(raw_output=text)


# ── Baseline Comparator ─────────────────────────────────────────

class BaselineComparator:
    """Compare current results against stored baseline."""

    def __init__(self, config: QualityGateConfig):
        self.config = config
        self.baseline_path = Path(config.baseline_file)

    def load_baseline(self) -> BaselineRecord | None:
        """Load the stored baseline."""
        if not self.baseline_path.exists():
            return None

        try:
            with open(self.baseline_path) as f:
                data = json.load(f)
            return BaselineRecord(
                timestamp=data.get("timestamp", ""),
                test_pass_rate=data.get("test_pass_rate", 0),
                coverage_pct=data.get("coverage_pct", 0),
                test_count=data.get("test_count", 0),
                coverage_count=data.get("coverage_count", 0),
                failures_summary=data.get("failures_summary", []),
            )
        except Exception:
            return None

    def save_baseline(self, test_result: TestResult, coverage: CoverageResult) -> None:
        """Save current results as new baseline."""
        self.baseline_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": datetime.now().isoformat(),
            "test_pass_rate": round(test_result.pass_rate, 1),
            "coverage_pct": round(coverage.pct, 1),
            "test_count": test_result.total,
            "coverage_count": coverage.covered_lines,
            "failures_summary": [f["detail"] for f in test_result.failures_list[:10]],
        }
        with open(self.baseline_path, "w") as f:
            json.dump(record, f, indent=2)

    def compare(self, test_result: TestResult, coverage: CoverageResult) -> dict:
        """Compare current results against baseline. Returns delta dict."""
        baseline = self.load_baseline()
        if not baseline:
            return {"status": "no_baseline", "message": "No baseline found — save one first"}

        delta = {
            "status": "ok",
            "warnings": [],
            "failures": [],
        }

        # Test pass rate comparison
        if test_result.pass_rate < baseline.test_pass_rate:
            decline = round(baseline.test_pass_rate - test_result.pass_rate, 1)
            if decline > 0:
                msg = f"Test pass rate declined from {baseline.test_pass_rate}% to {test_result.pass_rate}% (-{decline}%)"
                delta["failures"].append(msg)
                delta["status"] = "failed"

        # Coverage comparison
        if baseline.coverage_pct > 0 and coverage.pct > 0:
            decline = round(baseline.coverage_pct - coverage.pct, 1)
            if decline > self.config.coverage_decline_threshold:
                msg = f"Coverage declined from {baseline.coverage_pct}% to {coverage.pct}% (-{decline}%)"
                delta["warnings"].append(msg)

        # New failures
        if test_result.failed > 0:
            delta["failures"].append(f"{test_result.failed} new test failure(s)")

        return delta


# ── Quality Gate ────────────────────────────────────────────────

class QualityGate:
    """Main quality gate orchestrator."""

    def __init__(self, config: QualityGateConfig | None = None):
        self.config = config or QualityGateConfig.from_args()
        self.runner = TestRunner(self.config)
        self.coverage = CoverageChecker(self.config)
        self.comparator = BaselineComparator(self.config)

    def run(self) -> QualityReport:
        """Run all quality gates and return report."""
        start = time.time()
        report = QualityReport()

        # Gate 1: Tests must pass
        test_result = self.runner.run()
        report.test_result = test_result
        report.gates.append({
            "name": "Tests",
            "passed": test_result.is_clean,
            "detail": f"{test_result.passed}/{test_result.total} passed ({test_result.pass_rate:.1f}%)",
        })

        if not test_result.is_clean:
            report.passed = False
            report.errors.append(f"Test failures: {test_result.failed} failed, {test_result.errors} errors")
            if self.config.fail_fast:
                report.duration_sec = time.time() - start
                return report

        # Gate 2: Coverage check
        cov_result = self.coverage.run()
        report.coverage = cov_result
        if cov_result.pct > 0:
            passed = cov_result.pct >= self.config.min_coverage
            report.gates.append({
                "name": "Coverage",
                "passed": passed,
                "detail": f"{cov_result.pct:.1f}% (min: {self.config.min_coverage}%)",
            })
            if not passed:
                report.warnings.append(f"Coverage below minimum: {cov_result.pct:.1f}% < {self.config.min_coverage}%")
        else:
            report.gates.append({"name": "Coverage", "passed": True, "detail": "Not measured"})

        # Gate 3: Baseline comparison
        delta = self.comparator.compare(test_result, cov_result)
        report.baseline_delta = delta
        report.gates.append({
            "name": "Baseline",
            "passed": delta.get("status") != "failed",
            "detail": delta.get("status", "ok"),
        })

        if delta.get("status") == "failed":
            report.passed = False
            for f in delta.get("failures", []):
                report.errors.append(f)

        for w in delta.get("warnings", []):
            report.warnings.append(w)

        report.duration_sec = time.time() - start
        report.timestamp = datetime.now().isoformat()
        return report

    def save_baseline(self) -> str:
        """Run tests, save results as new baseline."""
        test_result = self.runner.run()
        cov_result = self.coverage.run()
        self.comparator.save_baseline(test_result, cov_result)
        return self.config.baseline_file

    def print_report(self, report: QualityReport) -> None:
        """Pretty-print quality report."""
        icon = "+" if report.passed else "X"
        print(f"\n{'='*60}")
        print(f"  Aurora Quality Gate Report  [{icon}]")
        print(f"  {report.timestamp}")
        print(f"  Duration: {report.duration_sec:.1f}s")
        print(f"{'='*60}")

        for gate in report.gates:
            s = "[PASS]" if gate["passed"] else "[FAIL]"
            print(f"  {s} {gate['name']}: {gate['detail']}")

        if report.warnings:
            print(f"\n  Warnings:")
            for w in report.warnings:
                print(f"    ! {w}")

        if report.errors:
            print(f"\n  Errors:")
            for e in report.errors:
                print(f"    X {e}")

        print(f"\n  Overall: {'PASSED' if report.passed else 'FAILED'}")
        print(f"{'='*60}")

        # Show test failure details
        if report.test_result and report.test_result.failures_list:
            print(f"\n  Recent Failures:")
            for f in report.test_result.failures_list[:5]:
                print(f"    - {f['detail']}")


# ── CLI Entry Point ─────────────────────────────────────────────

def main() -> None:
    """CLI entry point for quality gate."""
    import argparse

    parser = argparse.ArgumentParser(description="Aurora Quality Gate")
    parser.add_argument("--quick", action="store_true", help="Fast mode (unit tests only)")
    parser.add_argument("--save-baseline", action="store_true", help="Save current results as baseline")
    parser.add_argument("--report", action="store_true", help="Generate report only, don't fail")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    config = QualityGateConfig.from_args(quick=args.quick)
    gate = QualityGate(config)

    if args.save_baseline:
        path = gate.save_baseline()
        print(f"Baseline saved to {path}")
        return

    report = gate.run()

    if args.json:
        result = {
            "passed": report.passed,
            "timestamp": report.timestamp,
            "duration_sec": report.duration_sec,
            "gates": report.gates,
            "warnings": report.warnings,
            "errors": report.errors,
            "test_result": {
                "total": report.test_result.total,
                "passed": report.test_result.passed,
                "failed": report.test_result.failed,
                "pass_rate": report.test_result.pass_rate,
            } if report.test_result else None,
            "coverage": {
                "pct": report.coverage.pct,
            } if report.coverage else None,
        }
        print(json.dumps(result, indent=2))
    else:
        gate.print_report(report)

    if not args.report and not report.passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
