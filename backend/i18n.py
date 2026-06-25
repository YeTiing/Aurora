"""Aurora i18n — internationalization manager.

Loads locale JSON files and provides key-based translation with interpolation.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

I18N_DIR = Path(__file__).parent / "i18n"
DEFAULT_LOCALE = "zh-CN"
FALLBACK_LOCALE = "en"


class I18nManager:
    """Loads and serves locale strings with fallback support."""

    def __init__(self, default_locale: str = DEFAULT_LOCALE):
        self._default_locale = default_locale
        self._locales: dict[str, dict[str, str]] = {}
        self._load_all()

    def _load_all(self) -> None:
        """Load all .json files from the i18n directory."""
        if not I18N_DIR.exists():
            logger.warning("i18n directory not found: %s", I18N_DIR)
            return
        for json_file in I18N_DIR.glob("*.json"):
            locale = json_file.stem  # e.g. "en", "zh-CN", "ja-JP"
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
                self._locales[locale] = data
                logger.debug("Loaded locale: %s (%d keys)", locale, len(data))
            except Exception as e:
                logger.error("Failed to load locale %s: %s", locale, e)

    def reload(self) -> None:
        """Reload all locale files from disk."""
        self._locales.clear()
        self._load_all()

    def available_locales(self) -> list[str]:
        """Return list of loaded locale codes."""
        return sorted(self._locales.keys())

    def get_all(self, locale: str | None = None) -> dict[str, str]:
        """Get the full locale dictionary for a given locale."""
        loc = locale or self._default_locale
        return self._locales.get(loc, self._locales.get(FALLBACK_LOCALE, {}))

    def t(self, key: str, locale: str | None = None, **kwargs: Any) -> str:
        """Translate a key with optional {placeholder} interpolation.

        Looks up key in: requested locale → default locale → fallback (en).
        If not found, returns the key itself as the fallback string.

        Usage:
            i18n.t("nav.chat")                    # → "对话"
            i18n.t("nav.chat", locale="en")       # → "Chat"
            i18n.t("common.hello", name="Zenos")  # → "Hello, Zenos" (if key has {name})
        """
        loc = locale or self._default_locale
        lookup_chain = [loc, self._default_locale, FALLBACK_LOCALE]

        text: str | None = None
        for lc in lookup_chain:
            if lc in self._locales and key in self._locales[lc]:
                text = self._locales[lc][key]
                break

        if text is None:
            return key  # raw key as fallback

        if kwargs:
            try:
                text = text.format(**kwargs)
            except KeyError:
                pass  # missing interpolation arg — return raw

        return text


# Singleton
i18n = I18nManager()
