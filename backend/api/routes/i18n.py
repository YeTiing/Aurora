"""Aurora API - i18n routes."""
from __future__ import annotations
from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["i18n"])


@router.get("/i18n")
async def list_locales():
    """List all available locales."""
    from backend.i18n import i18n
    return {
        "locales": i18n.available_locales(),
        "default": i18n._default_locale,
    }


@router.get("/i18n/{locale}")
async def get_locale(locale: str):
    """Get translation strings for a locale. Falls back to en."""
    from backend.i18n import i18n
    translations = i18n.get_all(locale)
    if not translations:
        raise HTTPException(404, f"Locale not found: {locale}")
    return {"locale": locale, "translations": translations}
