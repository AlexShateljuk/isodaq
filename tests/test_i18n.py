"""Unit tests for core.i18n — catalog lookup, fallback, language selection."""
import json

from core import i18n


def test_english_is_identity():
    i18n.set_language("en")
    assert i18n.current_language() == "en"
    assert i18n.tr("File") == "File"
    assert i18n.tr("anything at all") == "anything at all"


def test_ukrainian_catalog_loads_and_translates():
    code = i18n.set_language("uk")
    assert code == "uk"
    assert i18n.current_language() == "uk"
    assert i18n.tr("File") == "Файл"
    assert i18n.tr("Preferences…") == "Параметри…"


def test_missing_key_falls_back_to_source():
    i18n.set_language("uk")
    assert i18n.tr("String that is not translated") == "String that is not translated"


def test_region_suffix_is_stripped():
    assert i18n.set_language("uk_UA") == "uk"
    assert i18n.tr("File") == "Файл"


def test_unknown_language_falls_back_to_english():
    assert i18n.set_language("zz") == "en"
    assert i18n.tr("File") == "File"


def test_available_languages_includes_en_and_uk():
    langs = i18n.available_languages()
    assert "en" in langs
    assert "uk" in langs


def test_init_respects_explicit_arg():
    assert i18n.init("uk") == "uk"
    assert i18n.tr("Exit") == "Вихід"
    i18n.set_language("en")   # reset for other tests


def test_shipped_catalogs_are_valid_json():
    from pathlib import Path
    trans_dir = Path(i18n.__file__).resolve().parent.parent / "translations"
    for path in trans_dir.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert all(isinstance(k, str) and isinstance(v, str) for k, v in data.items())
