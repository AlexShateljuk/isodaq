"""core/i18n.py — lightweight JSON-catalog translations.

Deliberately not the Qt Linguist (.ts/.qm) workflow: that needs the `lrelease`
toolchain, which isn't a Python dependency. Instead each language is a flat JSON
file ``translations/<code>.json`` mapping the English source string to its
translation — human-editable, no build step, contributor-friendly.

Usage:
    from core.i18n import tr
    label = tr("Save triggers…")           # → translated, or the source on miss

    # For interpolated strings, translate the template then format:
    tr("Connected: {port} @ {baud}").format(port=p, baud=b)

Language is chosen once at startup (call :func:`init` before building the UI):
explicit arg → ``ISODAQ_LANG`` env → system locale → English. Changing language
takes effect on the next launch (strings are resolved as widgets are built).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_TRANS_DIR = Path(__file__).resolve().parent.parent / "translations"

# Human-readable names for the language picker (fallback: the raw code).
LANGUAGE_NAMES = {"en": "English", "uk": "Українська"}

_catalog: dict[str, str] = {}
_lang: str = "en"


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, code)


def available_languages() -> list[str]:
    """'en' (the source language) plus every translations/<code>.json present."""
    langs = ["en"]
    if _TRANS_DIR.is_dir():
        langs += sorted(p.stem for p in _TRANS_DIR.glob("*.json"))
    return langs


def init(preferred: str = "") -> str:
    """Pick and load a language. Priority: *preferred* arg → ``ISODAQ_LANG`` env →
    system locale → English. Returns the language code actually loaded."""
    code = (preferred or os.environ.get("ISODAQ_LANG", "")).strip()
    if not code:
        try:
            from PyQt6.QtCore import QLocale
            code = QLocale.system().name()   # e.g. "uk_UA"
        except Exception:
            code = "en"
    return set_language(code)


def set_language(code: str) -> str:
    """Load ``translations/<code>.json`` (region stripped, e.g. uk_UA → uk).
    Falls back to English (empty catalog) if the file is absent or invalid.
    Returns the language code in effect."""
    global _catalog, _lang
    code = (code or "en").replace("-", "_").split("_")[0].lower()
    if code == "en":
        _catalog, _lang = {}, "en"
        return "en"
    try:
        _catalog = json.loads((_TRANS_DIR / f"{code}.json").read_text(encoding="utf-8"))
        _lang = code
    except Exception:
        _catalog, _lang = {}, "en"
    return _lang


def current_language() -> str:
    return _lang


def tr(text: str) -> str:
    """Translate *text* using the loaded catalog; returns *text* unchanged on a
    miss (so untranslated strings still render in English)."""
    return _catalog.get(text, text)
