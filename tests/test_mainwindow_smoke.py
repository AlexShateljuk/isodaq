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
    from ui.main_window import MainWindow

    # Sandbox persisted settings and skip the network update check.
    monkeypatch.setattr(MainWindow, "_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(MainWindow, "_start_update_check", lambda self: None)

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
    finally:
        w.close()
