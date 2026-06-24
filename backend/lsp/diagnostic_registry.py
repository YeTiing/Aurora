# -*- coding: utf-8 -*-
"""LSP Diagnostic Registry — pending diagnostic storage & deduplication.

Port of cc-haha's src/services/lsp/LSPDiagnosticRegistry.ts.
Stores async diagnostics from LSP servers, deduplicates, limits volume.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("aurora.lsp.diag")

MAX_DIAGNOSTICS_PER_FILE = 10
MAX_TOTAL_DIAGNOSTICS = 30
MAX_DELIVERED_FILES = 500


@dataclass
class PendingDiagnostic:
    """A diagnostic bundle awaiting delivery."""
    server_name: str
    files: list[dict]
    timestamp: float = field(default_factory=time.time)
    attachment_sent: bool = False


class DiagnosticRegistry:
    """Global registry for pending LSP diagnostics."""

    def __init__(self):
        self._pending: OrderedDict[str, PendingDiagnostic] = OrderedDict()
        self._delivered: OrderedDict[str, set[str]] = OrderedDict()

    def register(self, server_name: str, files: list[dict]) -> None:
        """Register diagnostics from an LSP server."""
        diag_id = str(uuid.uuid4())
        logger.debug(f"Registering {len(files)} diagnostic file(s) from {server_name} (id={diag_id})")
        self._pending[diag_id] = PendingDiagnostic(
            server_name=server_name, files=files
        )

    def check_for_diagnostics(self) -> list[PendingDiagnostic]:
        """Retrieve and clear pending diagnostics."""
        if not self._pending:
            return []
        result = list(self._pending.values())
        self._pending.clear()
        return result

    def get_attachments(self) -> list[dict]:
        """Get deduplicated, volume-limited diagnostic attachments."""
        pending = self.check_for_diagnostics()
        if not pending:
            return []

        all_diags: list[tuple[str, dict]] = []  # (key, diagnostic)

        for p in pending:
            for file_diag in p.files:
                uri = file_diag.get("uri", "")
                for diag in file_diag.get("diagnostics", []):
                    key = self._diag_key(diag)
                    # Skip if already delivered in any previous turn
                    if uri in self._delivered and key in self._delivered[uri]:
                        continue
                    all_diags.append((key, {**diag, "_uri": uri, "_source": p.server_name}))

        # Deduplicate within this batch
        seen = set()
        unique = []
        for key, diag in all_diags:
            if key not in seen:
                seen.add(key)
                unique.append(diag)

        # Sort by severity: Error > Warning > Info > Hint
        unique.sort(key=lambda d: self._severity_rank(d.get("severity")))

        # Limit
        truncated = unique[:MAX_TOTAL_DIAGNOSTICS]

        # Mark as delivered for cross-turn dedup
        for d in truncated:
            uri = d.get("_uri", "")
            if uri not in self._delivered:
                self._delivered[uri] = set()
            self._delivered[uri].add(self._diag_key(d))
            # Prune delivered cache
            if len(self._delivered) > MAX_DELIVERED_FILES:
                self._delivered.popitem(last=False)

        return truncated

    def clear(self) -> None:
        """Clear all pending and delivered diagnostics."""
        self._pending.clear()
        self._delivered.clear()

    # ── Helpers ─────────────────────────────────────────────────

    @staticmethod
    def _diag_key(diag: dict) -> str:
        """Create a stable key for diagnostic deduplication."""
        payload = {
            "message": diag.get("message", ""),
            "severity": diag.get("severity", ""),
            "range": diag.get("range", {}),
            "source": diag.get("source", "") or diag.get("_source", ""),
            "code": str(diag.get("code", "")),
        }
        return hashlib.md5(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    @staticmethod
    def _severity_rank(severity) -> int:
        """Map severity to sort rank (lower = more severe)."""
        if isinstance(severity, (int, float)):
            return int(severity)
        s = str(severity).lower()
        ranks = {"error": 1, "warning": 2, "info": 3, "hint": 4}
        return ranks.get(s, 4)


# ── Global singleton ────────────────────────────────────────────

_registry: DiagnosticRegistry | None = None


def get_registry() -> DiagnosticRegistry:
    """Get the global diagnostic registry singleton."""
    global _registry
    if _registry is None:
        _registry = DiagnosticRegistry()
    return _registry
