"""Aurora Package Manager — npm/pip/gem/cargo abstraction.

Mirrors the Worker's package management capability from Codex reverse engineering.
Provides a unified interface for installing, listing, and updating packages.
"""

from __future__ import annotations
import asyncio, json, os, subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class PackageEcosystem(str, Enum):
    NPM = "npm"
    PIP = "pip"
    GEM = "gem"
    CARGO = "cargo"
    PNPM = "pnpm"
    YARN = "yarn"
    POETRY = "poetry"
    MAVEN = "maven"
    GRADLE = "gradle"


ECOSYSTEM_META = {
    PackageEcosystem.NPM:    {"lockfile": "package-lock.json", "install": ["npm", "install"], "list": ["npm", "ls", "--json", "--depth=0"], "outdated": ["npm", "outdated", "--json"]},
    PackageEcosystem.PNPM:   {"lockfile": "pnpm-lock.yaml", "install": ["pnpm", "install"], "list": ["pnpm", "ls", "--json", "--depth=0"], "outdated": ["pnpm", "outdated", "--json"]},
    PackageEcosystem.YARN:   {"lockfile": "yarn.lock", "install": ["yarn", "install"], "list": ["yarn", "list", "--json", "--depth=0"], "outdated": ["yarn", "outdated", "--json"]},
    PackageEcosystem.PIP:    {"lockfile": "requirements.txt", "install": ["pip", "install"], "list": ["pip", "list", "--format=json"], "outdated": ["pip", "list", "--outdated", "--format=json"]},
    PackageEcosystem.POETRY: {"lockfile": "poetry.lock", "install": ["poetry", "install"], "list": ["poetry", "show", "--no-dev"], "outdated": ["poetry", "show", "--outdated"]},
    PackageEcosystem.GEM:    {"lockfile": "Gemfile.lock", "install": ["bundle", "install"], "list": ["bundle", "list"], "outdated": ["bundle", "outdated"]},
    PackageEcosystem.CARGO:  {"lockfile": "Cargo.lock", "install": ["cargo", "build"], "list": ["cargo", "tree", "--depth=0"], "outdated": ["cargo", "outdated"]},
    PackageEcosystem.MAVEN:  {"lockfile": "pom.xml", "install": ["mvn", "install"], "list": ["mvn", "dependency:list"], "outdated": ["mvn", "versions:display-dependency-updates"]},
    PackageEcosystem.GRADLE: {"lockfile": "build.gradle", "install": ["gradle", "build"], "list": ["gradle", "dependencies"], "outdated": ["gradle", "dependencyUpdates"]},
}


@dataclass
class PackageInfo:
    name: str
    version: str = ""
    ecosystem: str = ""
    is_outdated: bool = False
    latest_version: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "version": self.version,
                "ecosystem": self.ecosystem, "outdated": self.is_outdated,
                "latest": self.latest_version}


@dataclass
class PackageResult:
    success: bool
    output: str = ""
    error: str = ""
    packages: list[PackageInfo] = field(default_factory=list)
    ecosystem: str = ""

    def to_dict(self) -> dict:
        return {"success": self.success, "output": self.output[:5000],
                "error": self.error[:2000],
                "packages": [p.to_dict() for p in self.packages],
                "ecosystem": self.ecosystem}


class PackageManagerError(Exception):
    pass


class PackageManager:
    """Unified package manager supporting npm/pip/gem/cargo/maven/gradle."""

    @classmethod
    def detect_ecosystem(cls, project_dir: str | Path = ".") -> list[PackageEcosystem]:
        """Detect which package ecosystems are present in a project directory."""
        project_dir = Path(project_dir)
        found = []
        for eco, meta in ECOSYSTEM_META.items():
            if (project_dir / meta["lockfile"]).exists():
                found.append(eco)
        # Also check pyproject.toml for poetry
        if (project_dir / "pyproject.toml").exists():
            try:
                cfg = (project_dir / "pyproject.toml").read_text(encoding="utf-8")
                if "[tool.poetry]" in cfg:
                    if PackageEcosystem.POETRY not in found:
                        found.append(PackageEcosystem.POETRY)
            except Exception:
                pass
        # Also check for setup.py as pip
        if (project_dir / "setup.py").exists():
            if PackageEcosystem.PIP not in found:
                found.append(PackageEcosystem.PIP)
        return found

    @classmethod
    async def install(cls, project_dir: str | Path = ".",
                       ecosystem: PackageEcosystem | None = None) -> PackageResult:
        """Install dependencies for the detected ecosystem."""
        project_dir = Path(project_dir)
        if ecosystem is None:
            ecosystems = cls.detect_ecosystem(project_dir)
            if not ecosystems:
                return PackageResult(success=False, error="No package ecosystem detected")
            ecosystem = ecosystems[0]
        meta = ECOSYSTEM_META[ecosystem]
        try:
            proc = await asyncio.create_subprocess_exec(
                *meta["install"],
                cwd=str(project_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            return PackageResult(
                success=proc.returncode == 0,
                output=stdout.decode("utf-8", errors="replace"),
                error=stderr.decode("utf-8", errors="replace"),
                ecosystem=ecosystem.value,
            )
        except FileNotFoundError:
            return PackageResult(success=False, error=f"{ecosystem.value} not found",
                                 ecosystem=ecosystem.value)
        except asyncio.TimeoutError:
            return PackageResult(success=False, error="Install timed out",
                                 ecosystem=ecosystem.value)

    @classmethod
    async def list_packages(cls, project_dir: str | Path = ".",
                              ecosystem: PackageEcosystem | None = None) -> PackageResult:
        """List installed packages."""
        project_dir = Path(project_dir)
        if ecosystem is None:
            ecosystems = cls.detect_ecosystem(project_dir)
            if not ecosystems:
                return PackageResult(success=False, error="No package ecosystem detected")
            ecosystem = ecosystems[0]
        meta = ECOSYSTEM_META[ecosystem]
        try:
            proc = await asyncio.create_subprocess_exec(
                *meta["list"],
                cwd=str(project_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            output = stdout.decode("utf-8", errors="replace")
            packages = cls._parse_list_output(output, ecosystem)
            return PackageResult(
                success=proc.returncode == 0, output=output,
                error=stderr.decode("utf-8", errors="replace"),
                packages=packages, ecosystem=ecosystem.value,
            )
        except FileNotFoundError:
            return PackageResult(success=False, error=f"{ecosystem.value} not found",
                                 ecosystem=ecosystem.value)

    @classmethod
    async def check_outdated(cls, project_dir: str | Path = ".",
                               ecosystem: PackageEcosystem | None = None) -> PackageResult:
        """Check for outdated packages."""
        project_dir = Path(project_dir)
        if ecosystem is None:
            ecosystems = cls.detect_ecosystem(project_dir)
            if not ecosystems:
                return PackageResult(success=False, error="No package ecosystem detected")
            ecosystem = ecosystems[0]
        meta = ECOSYSTEM_META[ecosystem]
        try:
            proc = await asyncio.create_subprocess_exec(
                *meta["outdated"],
                cwd=str(project_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            return PackageResult(
                success=proc.returncode == 0,
                output=stdout.decode("utf-8", errors="replace"),
                error=stderr.decode("utf-8", errors="replace"),
                ecosystem=ecosystem.value,
            )
        except FileNotFoundError:
            return PackageResult(success=False, error=f"{ecosystem.value} not found",
                                 ecosystem=ecosystem.value)

    @classmethod
    def _parse_list_output(cls, output: str, ecosystem: PackageEcosystem) -> list[PackageInfo]:
        packages = []
        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            return packages
        if ecosystem in (PackageEcosystem.NPM, PackageEcosystem.PNPM, PackageEcosystem.YARN):
            deps = data.get("dependencies", {})
            for name, info in deps.items():
                packages.append(PackageInfo(name=name, version=info.get("version", ""),
                                            ecosystem=ecosystem.value))
        elif ecosystem == PackageEcosystem.PIP:
            for item in data:
                packages.append(PackageInfo(name=item.get("name", ""),
                                            version=item.get("version", ""),
                                            ecosystem=ecosystem.value))
        return packages

    @classmethod
    async def add_package(cls, name: str, project_dir: str | Path = ".",
                            ecosystem: PackageEcosystem | None = None,
                            dev: bool = False) -> PackageResult:
        """Add/install a single package."""
        project_dir = Path(project_dir)
        if ecosystem is None:
            ecosystems = cls.detect_ecosystem(project_dir)
            if not ecosystems:
                return PackageResult(success=False, error="No package ecosystem detected")
            ecosystem = ecosystems[0]
        install_cmd = {
            PackageEcosystem.NPM: ["npm", "install", name] + (["--save-dev"] if dev else []),
            PackageEcosystem.PNPM: ["pnpm", "add", name] + (["-D"] if dev else []),
            PackageEcosystem.YARN: ["yarn", "add", name] + (["--dev"] if dev else []),
            PackageEcosystem.PIP: ["pip", "install", name],
            PackageEcosystem.POETRY: ["poetry", "add", name] + (["--dev"] if dev else []),
            PackageEcosystem.GEM: ["bundle", "add", name],
            PackageEcosystem.CARGO: ["cargo", "add", name],
        }
        cmd = install_cmd.get(ecosystem, [ecosystem.value, "install", name])
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=str(project_dir),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            return PackageResult(
                success=proc.returncode == 0,
                output=stdout.decode("utf-8", errors="replace"),
                error=stderr.decode("utf-8", errors="replace"),
                ecosystem=ecosystem.value,
            )
        except FileNotFoundError:
            return PackageResult(success=False, error=f"{ecosystem.value} not found",
                                 ecosystem=ecosystem.value)


package_manager = PackageManager()
