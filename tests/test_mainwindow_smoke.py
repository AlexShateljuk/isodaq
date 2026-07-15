"""Smoke test: MainWindow must construct headless without raising.

This is the safety net for the OSS6 decomposition. There are no full UI tests,
so this catches the most likely refactor break — a mis-wired signal or a missing
attribute surfacing during construction. It is deliberately hermetic:

  * runs under the offscreen Qt platform (no display needed),
  * sandboxes the settings file to a tmp dir (never touches the real config),
  * stubs the update check (no network).

Skipped when PyQt6 (and the GUI stack) isn't installed, so the lean core CI job
that only has pytest stays green; it runs wherever the GUI deps are present.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")
pytest.importorskip("pyqtgraph")


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    app.setApplicationVersion("0.0.0-test")
    yield app


def test_mainwindow_constructs(qapp, tmp_path, monkeypatch):
    from ui.controllers.update_manager import UpdateManager
    from ui.main_window import MainWindow

    # Sandbox persisted settings and skip the network update check.
    monkeypatch.setattr(MainWindow, "_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(UpdateManager, "start", lambda self: None)

    w = MainWindow()
    try:
        # Core services are wired.
        assert w._logger is not None
        assert w._engine is not None
        assert w._reader is not None
        assert w._parser is not None
        assert w._macro_runner is not None
        # The window shell was built.
        assert w._terminal is not None
        assert w._main_splitter is not None
        assert w.centralWidget() is not None
        # OSS6 controllers are attached.
        assert w._session is not None
        assert w._updates is not None
        assert w._devtools is not None
        assert w._search is not None
        assert w._settings is not None
    finally:
        w.close()


def test_mainwindow_ukrainian_locale(qapp, tmp_path, monkeypatch):
    """With the uk catalog loaded, wrapped UI strings render in Ukrainian.

    Guards the i18n coverage: if a widget's text is un-wrapped (or a uk.json key
    drifts from the source string), these assertions fail.
    """
    from core import i18n
    from ui.controllers.update_manager import UpdateManager
    from ui.main_window import MainWindow

    monkeypatch.setattr(MainWindow, "_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(UpdateManager, "start", lambda self: None)

    prev = i18n.current_language()
    assert i18n.set_language("uk") == "uk"
    try:
        w = MainWindow()
        try:
            # Buttons, tabs and the status bar resolve through the uk catalog.
            assert w._conn_btn.text() == i18n.tr("Connect") == "Підключити"
            assert w._send_btn.text() == "Надіслати"
            assert w._tabs.tabText(0) == "Графіки"
            assert "Відключено" in w._sb_conn.text()
            # menuBar first menu is "File" → "Файл"
            assert w.menuBar().actions()[0].text() == "Файл"
        finally:
            w.close()
    finally:
        i18n.set_language(prev)   # don't leak locale into other tests


def test_settings_roundtrip(qapp, tmp_path, monkeypatch):
    """SettingsManager.save() then a fresh MainWindow.load() restores UI state."""
    from ui.controllers.update_manager import UpdateManager
    from ui.main_window import MainWindow

    cfg = tmp_path / "config.json"
    monkeypatch.setattr(MainWindow, "_CONFIG_PATH", cfg)
    monkeypatch.setattr(UpdateManager, "start", lambda self: None)

    a = MainWindow()
    a._scrollback_limit = 12345
    a._signaling_url = "https://relay.example.test"
    a._terminal_font_size = 17
    a._apply_theme("light")
    a._settings.save()
    a.close()

    assert cfg.exists()

    b = MainWindow()   # __init__ runs _settings.load()
    try:
        assert b._scrollback_limit == 12345
        assert b._signaling_url == "https://relay.example.test"
        assert b._terminal_font_size == 17
        assert b._current_theme == "light"
    finally:
        b.close()
