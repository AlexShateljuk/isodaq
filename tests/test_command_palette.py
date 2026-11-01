"""Command palette: fuzzy ranking and the command set the main window builds."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")
pytest.importorskip("pyqtgraph")

from ui.command_palette import fuzzy_score  # noqa: E402


def test_fuzzy_score_matches_subsequence_and_ranks_word_starts():
    assert fuzzy_score("xyz", "Show tool window Graphs") is None
    assert fuzzy_score("", "anything") == 0
    tg = fuzzy_score("tg", "Toggle Graphs")
    other = fuzzy_score("tg", "Settings › Log colorizer")
    assert tg is not None and other is not None and tg > other


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def test_palette_commands_are_runnable(qapp, tmp_path, monkeypatch):
    from ui.controllers.update_manager import UpdateManager
    from ui.main_window import MainWindow

    monkeypatch.setattr(MainWindow, "_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(UpdateManager, "start", lambda self: None)
    w = MainWindow()
    try:
        cmds = w._palette_commands()
        titles = {f"{c.category}: {c.title}" for c in cmds}
        assert "Show tool window: Graphs" in titles
        assert any(t.startswith("Terminal: HEX view") for t in titles)
        assert any("Theme" in t for t in titles)          # pulled from the menus
        assert all(callable(c.run) for c in cmds)

        hex_cmd = next(c for c in cmds if c.title.startswith("HEX view"))
        hex_cmd.run()
        assert w._chk_hex.isChecked() and w._hex_btn.isChecked()

        next(c for c in cmds if c.title == "Graphs").run()
        assert not w._dock_right.isHidden() and w._dock_right.current == "graphs"
    finally:
        w.close()
