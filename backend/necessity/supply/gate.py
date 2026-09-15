"""A4 扩展供应链安全准入门。

准入函数是加载器应调用的唯一入口契约：先扫描、生成清单、计算指纹，再按
严重程度决定 reject/ask/restricted/allow。它只返回报告，不导入扩展，因此
不会偷偷改变启动行为；静态分析降低风险 ≠ 消除风险，也不是沙箱。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .audit import AuditLog
from .fingerprint import fingerprint
from .manifest import PermissionManifest, build_manifest
from .scan import ScanFinding, scan_extension

Decision = Literal["reject", "ask", "restricted", "allow"]
Level = Literal["critical", "high", "medium", "low"]

@dataclass
class AdmissionReport:
    extension_id: str
    fingerprint: str
    level: Level
    findings: list[ScanFinding]
    manifest: PermissionManifest
    undetermined_ratio: float
    decision: Decision

    @property
    def safety_statement(self) -> str:
        return "降低风险 ≠ 消除风险；这是静态分析，不是沙箱。"

    def to_dict(self) -> dict:
        return {
            "extension_id": self.extension_id,
            "fingerprint": self.fingerprint,
            "level": self.level,
            "findings": [item.to_dict() for item in self.findings],
            "manifest": self.manifest.as_dict(),
            "undetermined_ratio": self.undetermined_ratio,
            "decision": self.decision,
            "safety_statement": self.safety_statement,
        }


def _level(findings: list[ScanFinding], unknown_ratio: float) -> Level:
    levels = {item.severity for item in findings}
    if "critical" in levels:
        return "critical"
    if "high" in levels or unknown_ratio > 0.3:
        return "high"
    if "medium" in levels:
        return "medium"
    return "low"


# 需要用户确认的高风险权限。
# ⚠️ 此前这些**完全不参与决策** —— 权限清单算出来了，但 `_decision` 只看
# findings。实测：一个读了敏感环境变量的扩展，清单里 `reads_env=required`，
# 却拿到 `allow`。清单成了给人看的装饰，而决策按「没扫到规则」放行。
# 这是安全能力里最危险的一种失效：报告说「有风险」，结论说「可以放」。
#
# 但不能反过来「任一命中就 ask」——实测这样会拦掉 8/20 个正常样本
# （固定 argv 的 subprocess、非密钥的 getenv、本地 socket 都是无害用法）。
# 规范 §5.1 原话：「全问 → 用户被淹没，养成无脑点同意的习惯，
# **比不问更危险**」。所以门既要有效又要不烦人。
#
# 判据分两级：
#   · 单个「强信号」权限（凭据读取、远程调用、数据库写入）足够触发确认 ——
#     正常扩展极少同时需要它们，而一旦需要，风险面本身就很大；
#   · 「执行 / 网络」这类**日常能力**必须**组合**才升级 ——
#     单独 `executes_shell` 或 `accesses_network` 是常态，
#     但「能执行 + 能联网」就是典型的外传通道（规范 §6.5「外传行为」）。
_STRONG_PERMISSIONS = ("reads_env", "calls_remote_mcp", "writes_database")
_COMBO_PERMISSIONS = ("executes_shell", "accesses_network")


def _manifest_risk(manifest: PermissionManifest) -> list[str]:
    """清单里值得升级审批的权限组合。返回触发原因（给用户看的）。"""
    required = {name for name in (
        *_STRONG_PERMISSIONS, *_COMBO_PERMISSIONS,
    ) if getattr(manifest, name, "not_detected") == "required"}

    reasons = sorted(required & set(_STRONG_PERMISSIONS))
    # 执行 + 网络同时具备 = 可外传，这是组合信号
    if set(_COMBO_PERMISSIONS) <= required:
        reasons.append("executes_shell+accesses_network（可外传）")
    return reasons


def _decision(level: Level, unknown_ratio: float,
              manifest: PermissionManifest | None = None) -> Decision:
    if level == "critical":
        return "reject"
    if level == "high":
        return "ask"
    if unknown_ratio > 0:
        return "restricted"
    if level == "medium":
        return "restricted"
    # 到这里 findings 是干净的（low）。此时**权限清单**才是最后的防线：
    # 「干得干净但要凭据 / 远程 / 库写 / 可外传」的扩展不能直接 allow。
    if manifest is not None and _manifest_risk(manifest):
        return "ask"
    return "allow"


def admit(extension_id: str, path: str, description: str = "",
          previous_fingerprint: str = "", audit: AuditLog | None = None) -> AdmissionReport:
    """执行一次只读准入评估；指纹变化会强制回到审批路径。"""
    findings = scan_extension(path, description)
    manifest = build_manifest(path, findings)
    current = fingerprint(path)
    level = _level(findings, manifest.undetermined_ratio)
    decision = _decision(level, manifest.undetermined_ratio, manifest)
    if previous_fingerprint and current != previous_fingerprint and decision == "allow":
        decision = "ask"
    report = AdmissionReport(extension_id, current, level, findings, manifest,
                             manifest.undetermined_ratio, decision)
    if audit is not None:
        audit.record(extension_id, "admission", target=str(path),
                     allowed=decision == "allow", details={"decision": decision, "level": level})
    return report


def is_admitted(report: AdmissionReport) -> bool:
    """加载器应以此布尔契约放行；ask/restricted/reject 都不能直接进入。"""
    return report.decision == "allow"


__all__ = ["AdmissionReport", "admit", "is_admitted"]
