"""Auto-reconnect (CMP1), custom baud (CMP3) and trigger auto-reply (CMP7)
end-to-end: the real MainWindow and SerialReader on a pseudo-terminal.

POSIX only (ptys). A symlink stands in for a stable device path; the "board"
is unplugged by closing its pty and re-plugged as a new pty behind the link.
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")
pytest.importorskip("pyqtgraph")
pytest.importorskip("serial")
if sys.platform == "win32":
    pytest.skip("needs POSIX ptys", allow_module_level=True)

import select  # noqa: E402
import tty     # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    app.setApplicationVersion("0.0.0-test")
    yield app


class Board:
    def __init__(self, link):
        self.link = link
        self.master, self.slave = os.openpty()
        tty.setraw(self.slave)
        if link.is_symlink():
            link.unlink()
        link.symlink_to(os.ttyname(self.slave))

    def send(self, data: bytes):
        os.write(self.master, data)

    def read(self, sec=0.4) -> bytes:
        out, end = b"", time.monotonic() + sec
        while time.monotonic() < end:
            if select.select([self.master], [], [], 0.05)[0]:
                out += os.read(self.master, 65536)
        return out

    def unplug(self):
        os.close(self.master)
        os.close(self.slave)
        self.link.unlink()


@pytest.fixture
def win(qapp, tmp_path, monkeypatch):
    from ui.controllers.update_manager import UpdateManager
    from ui.main_window import MainWindow
    monkeypatch.setattr(MainWindow, "_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(UpdateManager, "start", lambda self: None)
    w = MainWindow()
    w._transport_combo.setCurrentText("Serial")
    yield w
    w.close()


def pump(qapp, sec):
    end = time.monotonic() + sec
    while time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)


def _select(w, path):
    w._port_combo.insertItem(0, str(path))
    w._port_combo.setCurrentIndex(0)


def test_auto_reconnect_after_replug(qapp, win, tmp_path):
    link = tmp_path / "ttyBOARD"
    board = Board(link)
    _select(win, link)
    conn = win._active_conn
    win._serial.toggle_connection()
    pump(qapp, 0.3)
    assert conn.reader.isRunning()

    board.unplug()
    pump(qapp, 1.3)
    assert conn.reconnecting and not conn.reader.isRunning()
    assert win._conn_btn.text() == "Stop"
    errors = conn.errors

    board = Board(link)
    pump(qapp, 1.5)
    assert conn.reader.isRunning() and not conn.reconnecting
    board.send(b"after replug\r\n")
    pump(qapp, 0.4)
    win._flush_terminals()
    text = win._terminal.toPlainText()
    assert "Reconnected" in text and "after replug" in text
    assert conn.errors == errors            # waiting doesn't count as errors

    # Stop gives up; a manual disconnect never starts reconnecting.
    board.unplug()
    pump(qapp, 1.3)
    win._serial.toggle_connection()
    assert not conn.reconnecting
    board = Board(link)
    pump(qapp, 1.3)
    assert not conn.reader.isRunning()
    win._serial.toggle_connection()
    pump(qapp, 0.2)
    win._serial.toggle_connection()
    pump(qapp, 1.3)
    assert not conn.reconnecting and not conn.reader.isRunning()
    board.unplug()


def test_custom_baud_is_parsed(qapp, win):
    win._baud_combo.setCurrentText("250000")
    assert win._serial.baud() == 250000
    win._baud_combo.setCurrentText("")
    assert win._serial.baud() is None


def test_trigger_reply_is_sent(qapp, win, tmp_path):
    from core.triggers import Trigger
    board = Board(tmp_path / "ttyREPLY")
    _select(win, board.link)
    conn = win._active_conn
    win._eol_combo.setCurrentText("\\r\\n")
    conn.engine.add_trigger(Trigger("ping", r"PING (\d+)", type="regex",
                                    reply=r"PONG \1", action_flash=False))
    win._serial.toggle_connection()
    pump(qapp, 0.3)
    board.send(b"PING 7\r\n")
    pump(qapp, 0.3)
    assert board.read() == b"PONG 7\r\n"
    assert conn.tx_bytes == len(b"PONG 7\r\n")
    win._serial.toggle_connection()
    board.unplug()
