"""A4 扩展供应链安全：静态扫描、权限清单、指纹、准入与审计。"""

from . import audit, fingerprint, gate, manifest, scan
from .audit import AuditEvent, AuditLog
from .fingerprint import fingerprint as content_fingerprint, has_changed
from .gate import AdmissionReport, admit, is_admitted
from .manifest import PermissionManifest, Tri, build_manifest
from .scan import ScanFinding, scan, scan_extension

__all__ = [
    "audit", "fingerprint", "gate", "manifest", "scan",
    "AuditEvent", "AuditLog", "content_fingerprint", "has_changed",
    "AdmissionReport", "admit", "is_admitted", "PermissionManifest", "Tri",
    "build_manifest", "ScanFinding", "scan", "scan_extension",
]
