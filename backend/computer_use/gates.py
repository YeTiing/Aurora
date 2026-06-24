# -*- coding: utf-8 -*-
"""Computer Use Fine-Grained Permission Gates.

Port of cc-haha's src/utils/computerUse/gates.ts.
Sub-gate switches for fine-grained control over computer automation.
"""

from __future__ import annotations
import os, logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("aurora.cu.gates")

# ── Sub-gate Config ────────────────────────────────────────────

@dataclass
class CuGates:
    enabled: bool = True
    pixel_validation: bool = False        # Post-action pixel verification
    clipboard_paste_multiline: bool = True  # Multi-line clipboard paste
    mouse_animation: bool = True           # Show mouse movement animation
    hide_before_action: bool = True        # Hide Aurora before actions
    auto_target_display: bool = True       # Auto-select target display
    clipboard_guard: bool = True           # Save/restore clipboard
    coordinate_mode: str = "pixels"        # "pixels" | "normalized"
    screenshot_quality: int = 75           # JPEG quality 1-100
    move_settle_ms: int = 50               # Settle time after mouse move
    max_screenshot_dim: int = 1920         # Max screenshot dimension

    @classmethod
    def from_env(cls) -> "CuGates":
        return cls(
            enabled=os.getenv("AURORA_CU_ENABLED", "1") not in ("0","false","no"),
            pixel_validation=os.getenv("AURORA_CU_PIXEL_VALIDATION", "0") in ("1","true"),
            clipboard_paste_multiline=os.getenv("AURORA_CU_CLIPBOARD_MULTILINE", "1") not in ("0","false"),
            mouse_animation=os.getenv("AURORA_CU_MOUSE_ANIMATION", "1") not in ("0","false"),
            hide_before_action=os.getenv("AURORA_CU_HIDE_BEFORE", "1") not in ("0","false"),
            auto_target_display=os.getenv("AURORA_CU_AUTO_DISPLAY", "1") not in ("0","false"),
            clipboard_guard=os.getenv("AURORA_CU_CLIPBOARD_GUARD", "1") not in ("0","false"),
            coordinate_mode=os.getenv("AURORA_CU_COORD_MODE", "pixels"),
            screenshot_quality=int(os.getenv("AURORA_CU_JPEG_QUALITY", "75")),
            move_settle_ms=int(os.getenv("AURORA_CU_MOVE_SETTLE", "50")),
            max_screenshot_dim=int(os.getenv("AURORA_CU_MAX_DIM", "1920")),
        )


# ── Global gate state ──────────────────────────────────────────

_gates: Optional[CuGates] = None


def get_gates() -> CuGates:
    """Get the global CU gates config (lazy init)."""
    global _gates
    if _gates is None:
        _gates = CuGates.from_env()
    return _gates


def reset_gates() -> None:
    """Reset gates to env defaults."""
    global _gates
    _gates = CuGates.from_env()


def update_gates(**kwargs) -> CuGates:
    """Update specific gate values at runtime."""
    global _gates
    gates = get_gates()
    for key, value in kwargs.items():
        if hasattr(gates, key):
            setattr(gates, key, value)
    return gates


# ── Permission Checkers ────────────────────────────────────────

class CuPermission:
    def __init__(self, gates=None):
        self.gates = gates or get_gates()

    @property
    def is_enabled(self) -> bool:
        return self.gates.enabled

    def can_screenshot(self) -> tuple[bool, str]:
        if not self.gates.enabled: return False, "CU disabled"
        return True, ""

    def can_mouse_move(self, x, y) -> tuple[bool, str]:
        if not self.gates.enabled: return False, "CU disabled"
        return True, ""

    def can_mouse_click(self, button="left") -> tuple[bool, str]:
        if not self.gates.enabled: return False, "CU disabled"
        return True, ""

    def can_keyboard_type(self, text) -> tuple[bool, str]:
        if not self.gates.enabled: return False, "CU disabled"
        return True, ""

    def can_keyboard_combo(self, keys) -> tuple[bool, str]:
        if not self.gates.enabled: return False, "CU disabled"
        forbidden = {"win+l","win+r","ctrl+alt+del","alt+f4","ctrl+shift+esc"}
        combo = "+".join(k.lower() for k in keys)
        if combo in forbidden:
            return False, f"Forbidden key combo: {combo}"
        return True, ""

    def can_clipboard_read(self) -> tuple[bool, str]:
        if not self.gates.enabled: return False, "CU disabled"
        if self.gates.clipboard_guard:
            return True, "clipboard_guard: will restore after action"
        return True, ""

    def can_clipboard_write(self) -> tuple[bool, str]:
        if not self.gates.enabled: return False, "CU disabled"
        return True, ""

    def can_launch_app(self, app_name) -> tuple[bool, str]:
        if not self.gates.enabled: return False, "CU disabled"
        forbidden = ["taskmgr.exe","regedit.exe","msconfig.exe","cmd.exe","powershell.exe"]
        if app_name.lower() in forbidden:
            return False, f"Forbidden app: {app_name}"
        return True, ""


# ── Security Blacklist ─────────────────────────────────────────

FORBIDDEN_APPS = [
    "cmd.exe","powershell.exe","wt.exe","WindowsTerminal.exe",
    "1Password.exe","Bitwarden.exe","KeePass.exe","LastPass.exe",
    "taskmgr.exe","regedit.exe","msconfig.exe","gpedit.msc",
]

FORBIDDEN_COMBOS = [
    "win+l","win+r","win+d","win+m","ctrl+alt+del",
    "alt+f4","ctrl+shift+esc",
]

def is_app_forbidden(name: str) -> bool:
    return name.lower() in [a.lower() for a in FORBIDDEN_APPS]

def is_combo_forbidden(keys: list[str]) -> bool:
    combo = "+".join(k.lower() for k in keys)
    return combo in [c.lower() for c in FORBIDDEN_COMBOS]


# ── Pixel Validation (optional, off by default) ────────────────

async def validate_pixel(x, y, expected_rgb, screenshot_fn):
    try:
        img = await screenshot_fn()
        if img:
            pixel = img.getpixel((x, y))
            return tuple(pixel) == tuple(expected_rgb)
    except Exception as e:
        logger.debug(f"Pixel validation failed: {e}")
    return True  # Fail open