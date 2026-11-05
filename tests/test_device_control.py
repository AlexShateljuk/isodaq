"""Auto-reconnect (CMP1), custom baud (CMP3) and trigger auto-reply (CMP7)
end-to-end: the real MainWindow and SerialReader on a pseudo-terminal.

POSIX only (ptys). A symlink stands in for a stable device path; the "board"
is unplugged by closing its pty and re-plugged as a new pty behind the link.
"""
import json
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


# ── Build & flash (CMP12) ───────────────────────────────────────────────────────

def _py(code: str) -> str:
    """A command line running *code* in this Python (found on any CI machine)."""
    return f'"{sys.executable}" -c "{code}"'


def _wait(qapp, cond, sec=10.0):
    end = time.monotonic() + sec
    while time.monotonic() < end and not cond():
        qapp.processEvents()
        time.sleep(0.01)
    assert cond()


def _term(win):
    win._flush_terminals()
    return win._terminal.toPlainText()


def test_flash_releases_port_and_resumes(qapp, win, tmp_path):
    board = Board(tmp_path / "ttyFLASH")
    _select(win, board.link)
    conn = win._active_conn
    win._serial.toggle_connection()
    pump(qapp, 0.3)
    assert conn.reader.isRunning()

    # The tool sees the placeholder, and (here) proves the port is free by the
    # app having let go of it: checked below while it sleeps.
    marker = tmp_path / "ran.txt"
    code = f"import sys,time; open(r'{marker}','w').write(sys.argv[1]); print('Writing 100%'); time.sleep(0.6)"
    conn.flash_panel.set_config(f'{_py(code)} {{port}}', str(tmp_path))
    win._flash.run()
    assert conn.flash_runner is not None
    assert not conn.reader.isRunning()           # released before the tool starts
    assert not win._conn_btn.isEnabled()
    pump(qapp, 0.2)
    assert "Flashing" in win._sb_conn.text()

    _wait(qapp, lambda: conn.flash_runner is None)
    assert marker.read_text() == str(board.link)
    _wait(qapp, conn.reader.isRunning, sec=3)     # resumed by itself
    assert win._conn_btn.isEnabled()
    board.send(b"booted\r\n")
    pump(qapp, 0.4)
    text = _term(win)
    assert "BLD  Writing 100%" in text and "Finished OK" in text and "booted" in text
    win._serial.toggle_connection()
    board.unplug()


def test_flash_stays_disconnected_if_it_was(qapp, win, tmp_path):
    conn = win._active_conn
    conn.flash_panel.set_config(_py("print(1)"), "")
    win._flash.run()
    _wait(qapp, lambda: conn.flash_runner is None)
    pump(qapp, 1.2)
    assert not conn.reader.isRunning() and not conn.reconnecting


def test_flash_failure_is_reported(qapp, win):
    conn = win._active_conn
    conn.flash_panel.set_config(_py("import sys; print('boom'); sys.exit(3)"), "")
    win._flash.run()
    _wait(qapp, lambda: conn.flash_runner is None)
    pump(qapp, 0.1)
    text = _term(win)
    assert "boom" in text and "exit code 3" in text


def test_flash_refuses_what_it_cannot_run(qapp, win, tmp_path):
    conn = win._active_conn
    win._flash.run()                              # no command yet
    assert conn.flash_runner is None

    conn.flash_panel.set_config(_py("print(1)"), str(tmp_path / "nope"))
    win._flash.run()
    assert conn.flash_runner is None and "folder not found" in _term(win)

    win._transport_combo.setCurrentText("Network")
    conn.flash_panel.set_config("tool {port}", "")
    win._flash.run()
    assert conn.flash_runner is None and "no serial port" in _term(win)


def test_flash_stop_kills_the_whole_tree(qapp, win, tmp_path):
    """Stop must reach the grandchild: it is what would hold the serial port."""
    conn = win._active_conn
    alive = tmp_path / "alive.txt"
    child = f"import time,os; open(r'{alive}','w').write(str(os.getpid())); time.sleep(60)"
    parent = ("import subprocess,sys; "
              f"subprocess.run([sys.executable,'-c',{child!r}])")
    script = tmp_path / "tree.py"
    script.write_text(parent)
    conn.flash_panel.set_config(f'"{sys.executable}" "{script}"', "")
    win._flash.run()
    _wait(qapp, alive.exists)
    _wait(qapp, lambda: alive.read_text() != "")
    pid = int(alive.read_text())

    win._flash.stop()
    _wait(qapp, lambda: conn.flash_runner is None, sec=6)
    assert "Stopped after" in _term(win)
    end = time.monotonic() + 3
    while time.monotonic() < end:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    pytest.fail("the grandchild survived Stop")


# ── Controls (CMP10 / CMP11) ────────────────────────────────────────────────────

def test_controls_reach_the_device_and_persist(qapp, win, tmp_path):
    board = Board(tmp_path / "ttyCTL")
    _select(win, board.link)
    conn = win._active_conn
    win._eol_combo.setCurrentText("\\n")
    win._control_panel.from_dict_list([
        {"kind": "button", "label": "Reset", "command": "RST", "key": "F6"},
        {"kind": "slider", "label": "PWM", "command": "PWM={value}", "value": 30},
    ])
    win._serial.toggle_connection()
    pump(qapp, 0.3)
    assert board.read(0.2) == b""           # connecting sends no stored value

    win._control_panel.fire_key("F6")
    assert board.read() == b"RST\n"
    assert conn.tx_bytes == 4
    win._flush_terminals()
    assert "TX   RST" in win._terminal.toPlainText()

    win._serial.toggle_connection()
    pump(qapp, 0.2)
    win._control_panel.fire_key("F6")       # not connected: reported, not counted
    win._flush_terminals()
    assert conn.tx_bytes == 4 and "Not connected" in win._terminal.toPlainText()
    board.unplug()

    win._settings.save()
    saved = json.loads(win._CONFIG_PATH.read_text())["controls"]
    assert [c["label"] for c in saved] == ["Reset", "PWM"] and saved[1]["value"] == 30
