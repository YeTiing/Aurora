# -*- coding: utf-8 -*-
"""Bash Safety Classifier — command pattern matching + auto risk grading.

Port of cc-haha's src/tools/BashTool/bashPermissions.ts.
Analyzes shell commands before execution: detects destructive patterns,
network access, file system mutations, and assigns risk levels.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class BashRisk(str, Enum):
    SAFE = "safe"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    BLOCKED = "blocked"


@dataclass
class Classification:
    risk: BashRisk = BashRisk.SAFE
    reason: str = ""
    matched_pattern: str = ""
    requires_approval: bool = False


# BLOCKED — never allowed
BLOCKED = [
    (r"rm\s+-rf\s+/\s*$", "Recursive delete from root"),
    (r"rm\s+-rf\s+~", "Recursive delete home"),
    (r"dd\s+if=", "Raw disk write"),
    (r">\s*/dev/sd[a-z]", "Write to raw block device"),
    (r"mkfs\.", "Filesystem format"),
    (r"fdisk\s", "Disk partitioning"),
    (r":\(\)\s*\{", "Fork bomb"),
    (r"chmod\s+777\s+/", "World-writable root"),
    (r"curl.*\|\s*(ba)?sh", "Pipe curl to shell"),
    (r"wget.*-O\s*-\s*\|\s*(ba)?sh", "Pipe wget to shell"),
]

# CRITICAL — data destruction
CRITICAL = [
    (r"rm\s+-rf\s", "Recursive force delete"),
    (r"git\s+push\s+--force", "Force push"),
    (r"git\s+reset\s+--hard", "Hard git reset"),
    (r"git\s+clean\s+-", "Git clean"),
    (r"drop\s+(table|database)", "SQL drop"),
    (r"truncate\s+.*table", "SQL truncate"),
    (r"docker\s+rm\s+-f", "Force remove container"),
    (r"docker\s+system\s+prune", "Docker prune"),
    (r"shutdown\s", "System shutdown"),
    (r"reboot\s", "System reboot"),
    (r"taskkill\s+/f", "Force kill (Windows)"),
    (r"kill\s+-9", "Force kill (SIGKILL)"),
    (r"sudo\s+rm\s", "Sudo delete"),
    (r"del\s+/[fq].*system32", "Delete system files"),
]

# HIGH — system modification, package install
HIGH = [
    (r"sudo\s", "Sudo execution"),
    (r"pip\s+install\s", "Pip install"),
    (r"pip3\s+install\s", "Pip3 install"),
    (r"npm\s+install\s+-g", "Npm global install"),
    (r"npm\s+i\s+-g", "Npm global install"),
    (r"apt-get\s+install", "Apt install"),
    (r"apt\s+install", "Apt install"),
    (r"brew\s+install", "Homebrew install"),
    (r"choco\s+install", "Chocolatey install"),
    (r"winget\s+install", "Winget install"),
    (r"cargo\s+install", "Cargo install"),
    (r"go\s+install", "Go install"),
    (r"npm\s+publish", "Npm publish"),
    (r"docker\s+run\s", "Docker run"),
    (r"docker-compose\s+up", "Docker compose up"),
    (r"systemctl\s+(start|stop|restart|enable|disable)", "Systemd control"),
    (r"chmod\s+[0-7]{3,4}", "Permission change"),
    (r"chown\s", "Ownership change"),
    (r"mount\s", "Mount"),
    (r"umount\s", "Unmount"),
]

# MEDIUM — network, file writes outside workspace
MEDIUM = [
    (r"git\s+(commit|push|pull|fetch|merge|rebase|checkout)", "Git mutation"),
    (r"git\s+stash\s", "Git stash"),
    (r"git\s+tag", "Git tag"),
    (r"curl\s+.*-o\s", "Curl download"),
    (r"wget\s", "Wget download"),
    (r"pip\s+uninstall", "Pip uninstall"),
    (r"npm\s+uninstall", "Npm uninstall"),
    (r"mv\s", "Move/rename"),
    (r"cp\s+-r", "Recursive copy"),
    (r"scp\s", "Secure copy"),
    (r"rsync\s", "Rsync"),
    (r"tar\s+-[cx]", "Tar extract/create"),
    (r"zip\s", "Create zip"),
    (r"unzip\s", "Extract zip"),
    (r"make\s+install", "Make install"),
    (r"cmake\s+--install", "CMake install"),
]

# LOW — writes within workspace
LOW = [
    (r"mkdir\s", "Create directory"),
    (r"touch\s", "Create file"),
    (r"cp\s", "Copy file"),
    (r"rm\s(?!\s*-rf)", "Remove file"),
    (r"echo\s+.*>\s", "Redirect to file"),
    (r"cat\s+.*>\s", "Cat redirect"),
    (r"git\s+(add|stash\s+list)", "Git workspace"),
    (r"npm\s+(list|ls|outdated|audit)", "Npm read-only"),
    (r"cargo\s+(build|check|test)\s", "Cargo build"),
    (r"go\s+(build|test|vet)\s", "Go build"),
    (r"python\s+-m\s+pytest", "Run pytest"),
]

# SAFE — read-only
SAFE = [
    (r"git\s+status", "Git status"),
    (r"git\s+log\b", "Git log"),
    (r"git\s+diff\b", "Git diff"),
    (r"git\s+show\b", "Git show"),
    (r"git\s+branch\b", "Git branch"),
    (r"git\s+remote\b", "Git remote"),
    (r"^ls\s", "List directory"),
    (r"^dir\s", "List directory (Windows)"),
    (r"^cat\s", "Read file"),
    (r"^type\s", "Read file (Windows)"),
    (r"^head\s", "Read file head"),
    (r"^tail\s", "Read file tail"),
    (r"^less\s", "Read file"),
    (r"^more\s", "Read file"),
    (r"^grep\s", "Search text"),
    (r"^rg\s", "Ripgrep search"),
    (r"^find\s", "Find files"),
    (r"^wc\s", "Word count"),
    (r"^stat\s", "File stat"),
    (r"^file\s", "File type"),
    (r"^du\s", "Disk usage"),
    (r"^df\s", "Disk free"),
    (r"^which\s", "Which command"),
    (r"^where\s", "Where (Windows)"),
    (r"^echo\s", "Echo"),
    (r"^pwd$", "Print working directory"),
    (r"^pwd\s", "Print working directory"),
    (r"^whoami$", "Current user"),
    (r"^whoami\s", "Current user"),
    (r"^date\s", "Print date"),
    (r"^env$", "Print environment"),
    (r"^printenv", "Print environment"),
    (r"^python\s+--version", "Python version"),
    (r"^node\s+--version", "Node version"),
    (r"^npm\s+--version", "NPM version"),
    (r"^git\s+--version", "Git version"),
    (r"^docker\s+--version", "Docker version"),
    (r"^uname\s", "System info"),
    (r"^hostname", "Hostname"),
    (r"^ps\s", "Process list"),
    (r"^tasklist", "Task list (Windows)"),
]


class BashClassifier:
    LAYERS = [
        (BLOCKED, BashRisk.BLOCKED),
        (CRITICAL, BashRisk.CRITICAL),
        (HIGH, BashRisk.HIGH),
        (MEDIUM, BashRisk.MEDIUM),
        (LOW, BashRisk.LOW),
        (SAFE, BashRisk.SAFE),
    ]

    def __init__(self, workspace_root: str = "."):
        self.workspace_root = workspace_root
        self._compiled: dict = {}
        for patterns, risk in self.LAYERS:
            self._compiled[risk.value] = [(re.compile(p, re.I), d) for p, d in patterns]

    def classify(self, command: str) -> Classification:
        cmd = command.strip()
        if not cmd:
            return Classification(risk=BashRisk.SAFE, reason="Empty command")
        for patterns, risk in self.LAYERS:
            for pat, desc in self._compiled[risk.value]:
                if pat.search(cmd):
                    req = risk in (BashRisk.BLOCKED, BashRisk.CRITICAL, BashRisk.HIGH, BashRisk.MEDIUM)
                    return Classification(risk=risk, reason=desc, matched_pattern=pat.pattern, requires_approval=req)
        return Classification(risk=BashRisk.MEDIUM, reason="Unknown command - requires review", requires_approval=True)

    def classify_pipeline(self, command: str) -> Classification:
        # First check the full command against BLOCKED (pipeline-level patterns like curl|bash)
        for pat, desc in self._compiled[BashRisk.BLOCKED.value]:
            if pat.search(command):
                return Classification(risk=BashRisk.BLOCKED, reason=desc, matched_pattern=pat.pattern, requires_approval=True)
        # Then split and check each sub-command
        parts = re.split(r"\s*[|;&]\s*", command)
        highest = Classification(risk=BashRisk.SAFE)
        for part in parts:
            part = part.strip()
            if not part:
                continue
            cls = self.classify(part)
            if self._risk_gt(cls.risk, highest.risk):
                highest = cls
                if cls.risk == BashRisk.BLOCKED:
                    break
        return highest

    def approve_for_policy(self, command: str, policy: str) -> tuple:
        cls = self.classify_pipeline(command)
        if cls.risk == BashRisk.BLOCKED:
            return False, f"BLOCKED: {cls.reason}"
        if policy == "never":
            return True, "auto-approved"
        if policy == "on-failure":
            return cls.risk in (BashRisk.SAFE, BashRisk.LOW, BashRisk.MEDIUM), cls.reason
        if policy == "on-request":
            return cls.risk in (BashRisk.SAFE, BashRisk.LOW), cls.reason
        if policy == "untrusted":
            return cls.risk == BashRisk.SAFE, cls.reason
        return cls.risk != BashRisk.BLOCKED, cls.reason

    def is_safe(self, command: str) -> bool:
        return self.classify_pipeline(command).risk in (BashRisk.SAFE, BashRisk.LOW)

    @staticmethod
    def _risk_gt(a: BashRisk, b: BashRisk) -> bool:
        order = {BashRisk.SAFE:0, BashRisk.LOW:1, BashRisk.MEDIUM:2, BashRisk.HIGH:3, BashRisk.CRITICAL:4, BashRisk.BLOCKED:5}
        return order.get(a, 0) > order.get(b, 0)


_classifier: Optional[BashClassifier] = None

def get_classifier(workspace: str = ".") -> BashClassifier:
    global _classifier
    if _classifier is None:
        _classifier = BashClassifier(workspace)
    return _classifier

def classify_command(command: str) -> dict:
    cls = get_classifier().classify_pipeline(command)
    return {"command": command[:200], "risk": cls.risk.value, "reason": cls.reason, "requires_approval": cls.requires_approval}