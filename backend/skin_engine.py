
"""Aurora Skin Engine — Theme/skin system with built-in catalog + hot-swap.

Skins stored as JSON files in .aurora/skins/. Each skin defines:
  - Colors (bg, surface, border, text, accent, etc.)
  - Typography (font, sizes)
  - Radii / Shadows / Spacing
  - Optional: custom CSS snippet

Built-in catalog: 8 skins. Users can create, edit, export, import.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SKINS_DIR = Path(os.environ.get("AURORA_HOME", ".aurora")) / "skins"

# ── Data Classes ──

@dataclass
class SkinColors:
    bg: str = "#0d1117"
    surface: str = "#161b22"
    surface_elevated: str = "#1c2333"
    surface_hover: str = "#1a1f2b"
    border: str = "#30363d"
    border_light: str = "#2a3140"
    text: str = "#e6edf3"
    text_secondary: str = "#8b949e"
    text_muted: str = "#5d6578"
    accent: str = "#8b5cf6"
    accent_hover: str = "#a78bfa"
    accent_glow: str = "rgba(139, 92, 246, 0.18)"
    accent_subtle: str = "rgba(139, 92, 246, 0.08)"
    success: str = "#3fb950"
    success_subtle: str = "rgba(63, 185, 80, 0.12)"
    error: str = "#f85149"
    error_subtle: str = "rgba(248, 81, 73, 0.12)"
    warning: str = "#d29922"
    warning_subtle: str = "rgba(210, 153, 34, 0.12)"
    code_bg: str = "#0d1117"
    status_bar_bg: str = "#0d1117"
    status_bar_text: str = "#8b949e"
    terminal_bg: str = "#0d1117"
    terminal_text: str = "#e6edf3"

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, d: dict) -> "SkinColors":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class SkinTypography:
    font_family: str = "'Inter', -apple-system, sans-serif"
    font_mono: str = "'JetBrains Mono', 'Cascadia Code', monospace"
    font_size_sm: str = "11px"
    font_size_base: str = "13px"
    font_size_lg: str = "15px"
    line_height: str = "1.5"

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict) -> "SkinTypography":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class SkinShape:
    radius_sm: str = "6px"
    radius_base: str = "10px"
    radius_lg: str = "14px"
    radius_xl: str = "18px"
    radius_full: str = "9999px"
    shadow: str = "0 2px 12px rgba(0,0,0,0.4)"
    shadow_lg: str = "0 8px 32px rgba(0,0,0,0.6)"
    spacing_unit: str = "4px"

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict) -> "SkinShape":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Skin:
    name: str
    label: str
    description: str = ""
    author: str = "Aurora"
    version: str = "1.0"
    colors: SkinColors = field(default_factory=SkinColors)
    typography: SkinTypography = field(default_factory=SkinTypography)
    shape: SkinShape = field(default_factory=SkinShape)
    custom_css: str = ""  # Additional CSS rules
    accent_color_name: str = "purple"  # For quick reference

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "author": self.author,
            "version": self.version,
            "accent_color_name": self.accent_color_name,
            "colors": self.colors.to_dict(),
            "typography": self.typography.to_dict(),
            "shape": self.shape.to_dict(),
            "custom_css": self.custom_css,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Skin":
        return cls(
            name=d.get("name", "unnamed"),
            label=d.get("label", "Unnamed"),
            description=d.get("description", ""),
            author=d.get("author", "Aurora"),
            version=d.get("version", "1.0"),
            accent_color_name=d.get("accent_color_name", "purple"),
            colors=SkinColors.from_dict(d.get("colors", {})),
            typography=SkinTypography.from_dict(d.get("typography", {})),
            shape=SkinShape.from_dict(d.get("shape", {})),
            custom_css=d.get("custom_css", ""),
        )

    def to_export(self) -> dict:
        """Export format — standalone .aurora-skin.json."""
        return {
            "format_version": "1.0",
            "type": "aurora-skin",
            **self.to_dict(),
        }

    @classmethod
    def from_export(cls, d: dict) -> "Skin":
        return cls.from_dict(d)

    def to_css_vars(self) -> str:
        """Generate CSS custom properties string."""
        c = self.colors
        t = self.typography
        s = self.shape
        vars = [
            f"--aurora-bg: {c.bg};",
            f"--aurora-surface: {c.surface};",
            f"--aurora-surface-elevated: {c.surface_elevated};",
            f"--aurora-surface-hover: {c.surface_hover};",
            f"--aurora-border: {c.border};",
            f"--aurora-border-light: {c.border_light};",
            f"--aurora-text: {c.text};",
            f"--aurora-text-secondary: {c.text_secondary};",
            f"--aurora-text-muted: {c.text_muted};",
            f"--aurora-accent: {c.accent};",
            f"--aurora-accent-hover: {c.accent_hover};",
            f"--aurora-accent-glow: {c.accent_glow};",
            f"--aurora-accent-subtle: {c.accent_subtle};",
            f"--aurora-success: {c.success};",
            f"--aurora-success-subtle: {c.success_subtle};",
            f"--aurora-error: {c.error};",
            f"--aurora-error-subtle: {c.error_subtle};",
            f"--aurora-warning: {c.warning};",
            f"--aurora-warning-subtle: {c.warning_subtle};",
            f"--aurora-code-bg: {c.code_bg};",
            f"--aurora-status-bar-bg: {c.status_bar_bg};",
            f"--aurora-status-bar-text: {c.status_bar_text};",
            f"--aurora-terminal-bg: {c.terminal_bg};",
            f"--aurora-terminal-text: {c.terminal_text};",
            f"--aurora-font: {t.font_family};",
            f"--aurora-font-mono: {t.font_mono};",
            f"--aurora-font-sm: {t.font_size_sm};",
            f"--aurora-font-base: {t.font_size_base};",
            f"--aurora-font-lg: {t.font_size_lg};",
            f"--aurora-radius-sm: {s.radius_sm};",
            f"--aurora-radius: {s.radius_base};",
            f"--aurora-radius-lg: {s.radius_lg};",
            f"--aurora-radius-xl: {s.radius_xl};",
            f"--aurora-radius-full: {s.radius_full};",
            f"--aurora-shadow: {s.shadow};",
            f"--aurora-shadow-lg: {s.shadow_lg};",
        ]
        return "\n".join(vars)


# ── Built-in Skins ──

BUILTIN_SKINS: dict[str, Skin] = {}

def _init_builtins():
    global BUILTIN_SKINS
    if BUILTIN_SKINS:
        return
    BUILTIN_SKINS = {
        "aurora-dark": Skin(
            name="aurora-dark", label="Aurora Dark",
            description="Deep navy base with purple accent. The original Aurora look.",
            accent_color_name="purple",
            colors=SkinColors(
                bg="#0b0e14", surface="#13171f", surface_elevated="#191e28",
                surface_hover="#1a1f2b", border="#1e2430", border_light="#2a3140",
                text="#e8edf5", text_secondary="#8892a4", text_muted="#5d6578",
                accent="#5b9cf5", accent_hover="#74aff7",
                accent_glow="rgba(91, 156, 245, 0.18)", accent_subtle="rgba(91, 156, 245, 0.08)",
                success="#42be65", success_subtle="rgba(66, 190, 101, 0.12)",
                error="#f4605e", error_subtle="rgba(244, 96, 94, 0.12)",
                warning="#d4a72c", warning_subtle="rgba(212, 167, 44, 0.12)",
                code_bg="#0d1117", status_bar_bg="#0b0e14", terminal_bg="#0b0e14",
            ),
        ),
        "aurora-light": Skin(
            name="aurora-light", label="Aurora Light",
            description="Clean light theme. Good for daytime coding.",
            accent_color_name="blue",
            colors=SkinColors(
                bg="#ffffff", surface="#f6f8fa", surface_elevated="#ffffff",
                surface_hover="#f3f4f6", border="#d0d7de", border_light="#e8eaed",
                text="#1f2328", text_secondary="#656d76", text_muted="#8c959f",
                accent="#0969da", accent_hover="#0550ae",
                accent_glow="rgba(9, 105, 218, 0.15)", accent_subtle="rgba(9, 105, 218, 0.08)",
                success="#1a7f37", success_subtle="rgba(26, 127, 55, 0.12)",
                error="#cf222e", error_subtle="rgba(207, 34, 46, 0.12)",
                warning="#9a6700", warning_subtle="rgba(154, 103, 0, 0.12)",
                code_bg="#f6f8fa", status_bar_bg="#f6f8fa", terminal_bg="#1f2328",
                terminal_text="#e6edf3",
            ),
        ),
        "midnight-purple": Skin(
            name="midnight-purple", label="Midnight Purple",
            description="Deep indigo with violet accent. Premium dark aesthetic.",
            accent_color_name="purple",
            colors=SkinColors(
                bg="#0f0a1a", surface="#1a1130", surface_elevated="#231842",
                surface_hover="#1c1335", border="#2d1f50", border_light="#352560",
                text="#e8e0f0", text_secondary="#9b8ec4", text_muted="#6958a0",
                accent="#a855f7", accent_hover="#c084fc",
                accent_glow="rgba(168, 85, 247, 0.2)", accent_subtle="rgba(168, 85, 247, 0.1)",
                success="#34d399", success_subtle="rgba(52, 211, 153, 0.12)",
                error="#fb7185", error_subtle="rgba(251, 113, 133, 0.12)",
                warning="#fbbf24", warning_subtle="rgba(251, 191, 36, 0.12)",
                code_bg="#0f0a1a", status_bar_bg="#0a0615", terminal_bg="#0a0615",
            ),
        ),
        "forest-green": Skin(
            name="forest-green", label="Forest Green",
            description="Earthy dark green tones. Calm and focused.",
            accent_color_name="green",
            colors=SkinColors(
                bg="#0a1a10", surface="#112618", surface_elevated="#183220",
                surface_hover="#142b1c", border="#1e3a28", border_light="#244530",
                text="#e0f0e0", text_secondary="#8cb894", text_muted="#5a8a64",
                accent="#4ade80", accent_hover="#86efac",
                accent_glow="rgba(74, 222, 128, 0.18)", accent_subtle="rgba(74, 222, 128, 0.08)",
                success="#22c55e", success_subtle="rgba(34, 197, 94, 0.12)",
                error="#f87171", error_subtle="rgba(248, 113, 113, 0.12)",
                warning="#facc15", warning_subtle="rgba(250, 204, 21, 0.12)",
                code_bg="#0a1a10", status_bar_bg="#06120a", terminal_bg="#06120a",
            ),
        ),
        "ocean-blue": Skin(
            name="ocean-blue", label="Ocean Blue",
            description="Deep ocean navy. Professional and clean.",
            accent_color_name="cyan",
            colors=SkinColors(
                bg="#0c1929", surface="#14273e", surface_elevated="#1b3452",
                surface_hover="#182e46", border="#1e3d5c", border_light="#264a6e",
                text="#dceefb", text_secondary="#7bafd4", text_muted="#4a7ea8",
                accent="#38bdf8", accent_hover="#7dd3fc",
                accent_glow="rgba(56, 189, 248, 0.18)", accent_subtle="rgba(56, 189, 248, 0.08)",
                success="#2dd4bf", success_subtle="rgba(45, 212, 191, 0.12)",
                error="#f472b6", error_subtle="rgba(244, 114, 182, 0.12)",
                warning="#fb923c", warning_subtle="rgba(251, 146, 60, 0.12)",
                code_bg="#0c1929", status_bar_bg="#091420", terminal_bg="#091420",
            ),
        ),
        "sunset-orange": Skin(
            name="sunset-orange", label="Sunset Orange",
            description="Warm dark brown with orange accent. Cozy and creative.",
            accent_color_name="orange",
            colors=SkinColors(
                bg="#1a120c", surface="#261915", surface_elevated="#33201a",
                surface_hover="#2c1c17", border="#3d2a22", border_light="#4a352c",
                text="#f5e6d8", text_secondary="#c4a88c", text_muted="#8c6c52",
                accent="#fb923c", accent_hover="#fbbf24",
                accent_glow="rgba(251, 146, 60, 0.2)", accent_subtle="rgba(251, 146, 60, 0.1)",
                success="#a3e635", success_subtle="rgba(163, 230, 53, 0.12)",
                error="#ef4444", error_subtle="rgba(239, 68, 68, 0.12)",
                warning="#facc15", warning_subtle="rgba(250, 204, 21, 0.12)",
                code_bg="#1a120c", status_bar_bg="#120a06", terminal_bg="#120a06",
            ),
        ),
        "rose-dawn": Skin(
            name="rose-dawn", label="Rose Dawn",
            description="Soft rose-pink tones on warm dark. Gentle and elegant.",
            accent_color_name="pink",
            colors=SkinColors(
                bg="#1a1018", surface="#261a22", surface_elevated="#33242e",
                surface_hover="#2c1e27", border="#3d2c36", border_light="#4a3642",
                text="#f0dde8", text_secondary="#c498b0", text_muted="#8c5c78",
                accent="#f472b6", accent_hover="#fb8ec4",
                accent_glow="rgba(244, 114, 182, 0.18)", accent_subtle="rgba(244, 114, 182, 0.08)",
                success="#6ee7b7", success_subtle="rgba(110, 231, 183, 0.12)",
                error="#ef4444", error_subtle="rgba(239, 68, 68, 0.12)",
                warning="#fcd34d", warning_subtle="rgba(252, 211, 77, 0.12)",
                code_bg="#1a1018", status_bar_bg="#120810", terminal_bg="#120810",
            ),
        ),
        "monochrome": Skin(
            name="monochrome", label="Monochrome",
            description="Pure grayscale. Maximum focus, zero distraction.",
            accent_color_name="slate",
            colors=SkinColors(
                bg="#121212", surface="#1e1e1e", surface_elevated="#282828",
                surface_hover="#242424", border="#333333", border_light="#404040",
                text="#e0e0e0", text_secondary="#a0a0a0", text_muted="#707070",
                accent="#ffffff", accent_hover="#e0e0e0",
                accent_glow="rgba(255, 255, 255, 0.12)", accent_subtle="rgba(255, 255, 255, 0.06)",
                success="#888888", success_subtle="rgba(136, 136, 136, 0.12)",
                error="#ff6b6b", error_subtle="rgba(255, 107, 107, 0.12)",
                warning="#dddddd", warning_subtle="rgba(221, 221, 221, 0.12)",
                code_bg="#121212", status_bar_bg="#0a0a0a", terminal_bg="#0a0a0a",
            ),
        ),
    }


# ── Skin Manager ──

class SkinManager:
    """Manage skins: built-in catalog, user skins, import/export."""

    def __init__(self):
        _init_builtins()
        SKINS_DIR.mkdir(parents=True, exist_ok=True)
        self._active_skin_name: str | None = None
        self._ensure_builtins()

    def _ensure_builtins(self):
        """Copy built-in skins to disk if missing."""
        for name, skin in BUILTIN_SKINS.items():
            path = SKINS_DIR / f"{name}.json"
            if not path.exists():
                path.write_text(json.dumps(skin.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    def list_skins(self) -> list[dict]:
        """List all available skins."""
        skins = []
        seen = set()
        for path in sorted(SKINS_DIR.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                skin = Skin.from_dict(data)
                if skin.name not in seen:
                    skins.append({
                        "name": skin.name, "label": skin.label,
                        "description": skin.description, "author": skin.author,
                        "accent": skin.colors.accent, "accent_color_name": skin.accent_color_name,
                        "is_builtin": skin.name in BUILTIN_SKINS,
                        "bg_preview": skin.colors.bg,
                        "surface_preview": skin.colors.surface,
                        "text_preview": skin.colors.text,
                    })
                    seen.add(skin.name)
            except Exception:
                pass
        return skins

    @staticmethod
    def _sanitize_name(name: str) -> str:
        """Reject names containing path separators or traversal."""
        name = name.strip()
        if not name or ".." in name or "/" in name or "\\" in name:
            raise ValueError(f"Invalid skin name: {name!r}")
        return name

    def get_skin(self, name: str) -> Skin | None:
        name = self._sanitize_name(name)
        path = SKINS_DIR / f"{name}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return Skin.from_dict(data)
            except Exception:
                pass
        return BUILTIN_SKINS.get(name)

    def save_skin(self, name: str, data: dict) -> Skin:
        """Create or update a skin."""
        name = self._sanitize_name(name)
        skin = Skin.from_dict(data)
        skin.name = name
        path = SKINS_DIR / f"{name}.json"
        path.write_text(json.dumps(skin.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return skin

    def delete_skin(self, name: str) -> bool:
        name = self._sanitize_name(name)
        if name in BUILTIN_SKINS:
            return False  # Cannot delete built-in skins
        path = SKINS_DIR / f"{name}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    def export_skin(self, name: str) -> dict | None:
        skin = self.get_skin(name)
        if skin:
            return skin.to_export()
        return None

    def import_skin(self, data: dict) -> Skin:
        skin = Skin.from_export(data)
        path = SKINS_DIR / f"{skin.name}.json"
        path.write_text(json.dumps(skin.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return skin

    def get_active_skin(self) -> dict:
        """Get currently active skin with full CSS vars."""
        from backend.config import config
        name = self._active_skin_name or config.get("theme", "aurora-dark")
        skin = self.get_skin(name)
        if not skin:
            skin = BUILTIN_SKINS.get("aurora-dark")
        return {
            "name": skin.name,
            "label": skin.label,
            "css_vars": skin.to_css_vars(),
            "colors": skin.colors.to_dict(),
            "typography": skin.typography.to_dict(),
            "shape": skin.shape.to_dict(),
            "custom_css": skin.custom_css,
        }

    def apply_skin(self, name: str) -> bool:
        skin = self.get_skin(name)
        if not skin:
            return False
        self._active_skin_name = name
        # Update config
        from backend.config import config as cfg
        try:
            cfg._project["theme"] = name
            p = Path("aurora.json")
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                data["theme"] = name
                p.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        return True

    def preview_css(self, name: str) -> str | None:
        skin = self.get_skin(name)
        if skin:
            return skin.to_css_vars()
        return None


# ── Singleton ──

_skin_mgr: SkinManager | None = None


def get_skin_manager() -> SkinManager:
    global _skin_mgr
    if _skin_mgr is None:
        _skin_mgr = SkinManager()
    return _skin_mgr
