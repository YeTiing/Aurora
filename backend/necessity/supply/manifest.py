"""扩展权限清单。

清单是 A3 的事实信号，不是授权本身。任何无法读取或无法静态归类的内容
都计入 `undetermined`；若把它改成 `not_detected`，未知扩展就会被误授予
低权限，这正是三态设计要避免的安全漏洞。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Tri = Literal["required", "not_detected", "undetermined"]

@dataclass
class PermissionManifest:
    reads_workspace: Tri
    writes_files: Tri
    executes_shell: Tri
    accesses_network: Tri
    reads_env: Tri
    installs_deps: Tri
    calls_remote_mcp: Tri
    writes_database: Tri

    @property
    def undetermined_ratio(self) -> float:
        """返回未知权限占比；未知必须进入准入决策，而不能被静默忽略。"""
        values = self.as_dict().values()
        return sum(value == "undetermined" for value in values) / 8

    def as_dict(self) -> dict[str, Tri]:
        return {
            "reads_workspace": self.reads_workspace,
            "writes_files": self.writes_files,
            "executes_shell": self.executes_shell,
            "accesses_network": self.accesses_network,
            "reads_env": self.reads_env,
            "installs_deps": self.installs_deps,
            "calls_remote_mcp": self.calls_remote_mcp,
            "writes_database": self.writes_database,
        }

    def to_risk_signal(self) -> dict[str, bool | float]:
        """导出给 A3 的纯事实信号，不在这里替 A3 做最终授权决定。"""
        risky = self.executes_shell == "required" and self.accesses_network == "required"
        return {
            "involves_risky_domain": risky,
            "shell_required": self.executes_shell == "required",
            "network_required": self.accesses_network == "required",
            "undetermined_ratio": self.undetermined_ratio,
        }


def _state(required: bool, unknown: bool) -> Tri:
    if unknown:
        return "undetermined"
    return "required" if required else "not_detected"


def _has(text: str, pattern: str) -> bool:
    return bool(re.search(pattern, text, re.I))


def build_manifest(path: str | Path, findings: list | None = None) -> PermissionManifest:
    """从扩展文本和扫描结果生成清单；只读文本，不执行扩展代码。"""
    if path is None:
        return PermissionManifest(*(["undetermined"] * 8))
    root = Path(path)
    if not root.is_dir():
        return PermissionManifest(*(["undetermined"] * 8))
    texts: list[str] = []
    opaque = False
    for item in root.rglob("*"):
        if not item.is_file() or any(part in {".git", "node_modules", "__pycache__"} for part in item.parts):
            continue
        try:
            texts.append(item.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            opaque = True
    blob = "\n".join(texts)
    rule_ids = {getattr(item, "rule_id", "") for item in (findings or [])}
    unknown = opaque or not texts
    return PermissionManifest(
        reads_workspace=_state(_has(blob, r"(?:open\s*\(|read_text\s*\(|read_bytes\s*\(|Path\([^)]*\)\.read|fs\.read|readFile)"), unknown),
        writes_files=_state(_has(blob, r"(?:write_text\s*\(|write_bytes\s*\(|open\([^)]*['\"]w|fs\.write|writeFile|unlink\s*\(|rmtree)"), unknown),
        executes_shell=_state(_has(blob, r"(?:os\.system|subprocess\.|child_process|spawn\s*\(|exec\s*\(|eval\s*\(|shell\s*=\s*true|curl\s+.*\|)"), unknown),
        accesses_network=_state(_has(blob, r"(?:requests?\.|urllib\.request|httpx\.|fetch\s*\(|socket\.|axios|https?://)"), unknown),
        reads_env=_state(_has(blob, r"(?:os\.environ|getenv\s*\(|process\.env|Deno\.env|\$[A-Z_]+)"), unknown),
        installs_deps=_state(_has(blob, r"(?:pip\s+install|npm\s+install|pnpm\s+add|subprocess.*install|postinstall|requirements\.txt|package\.json)"), unknown),
        calls_remote_mcp=_state(_has(blob, r"(?:mcp|model context protocol|tools/call|jsonrpc)"), unknown),
        writes_database=_state(_has(blob, r"(?:sqlite|postgres|mysql|mongodb|redis|INSERT\s+INTO|UPDATE\s+\w+\s+SET|\.execute\s*\()"), unknown),
    )


def manifest_from_findings(path: str | Path, findings: list) -> PermissionManifest:
    """兼容入口；保留 findings 参数以便调用方把同一轮扫描结果传入。"""
    return build_manifest(path, findings)


__all__ = ["Tri", "PermissionManifest", "build_manifest", "manifest_from_findings"]
