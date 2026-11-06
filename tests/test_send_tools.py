"""Hex frames + checksums (CMP9), repeat (CMP8), snapshots (CMP21) and the TCP
share (CMP22), end to end: the real MainWindow and SerialReader on a pty."""
import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")
pytest.importorskip("pyqtgraph")
pytest.importorskip("serial")

from test_device_control import Board, _select, pump  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    app.setApplicationVersion("0.0.0-test")
    yield app


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    from ui.controllers.update_manager import UpdateManager
    from ui.main_window import MainWindow
    monkeypatch.setattr(MainWindow, "_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(UpdateManager, "start", lambda self: None)
    w = MainWindow()
    w._transport_combo.setCurrentText("Serial")
    yield w
    w.close()


@pytest.fixture
def linked(qapp, window, tmp_path):
    """The window connected to a board."""
    win = window
    board = Board(tmp_path / "ttyBOARD")
    _select(win, board.link)
    win._serial.toggle_connection()
    pump(qapp, 0.2)
    assert win._active_conn.reader.isRunning()
    yield win, board
    conn = win._active_conn
    if conn.reader.isRunning():
        win._serial.toggle_connection()
        pump(qapp, 0.1)
    board.unplug()


def terminal(win):
    win._flush_terminals()
    return win._terminal.toPlainText()


def type_and_send(win, text):
    win._cmd_edit.setText(text)
    win._serial.send_command()


# ── Frames ────────────────────────────────────────────────────────────────────

def test_plain_text_is_unchanged_by_default(linked):
    win, board = linked
    win._eol_combo.setCurrentText("\\r\\n")
    assert win._frame_btn.text() == "Text" and not win._frame_btn.isChecked()
    type_and_send(win, "AT+RST")
    assert board.read() == b"AT+RST\r\n"


def test_hex_frame_with_modbus_crc_on_the_wire(linked):
    win, board = linked
    btn = win._frame_btn
    btn._input_actions[True].trigger()
    btn._algo_actions["crc16-modbus"].trigger()
    assert btn.format == "low_first" and btn.isChecked()
    assert btn.text() == "Hex + CRC-16/Modbus"
    assert not win._eol_combo.isEnabled()
    assert "Hex bytes" in win._cmd_edit.placeholderText()
    type_and_send(win, "01 03 00 00 00 02")
    assert board.read() == bytes.fromhex("010300000002C40B")
    assert "01 03 00 00 00 02 C4 0B" in terminal(win)
    assert win._active_conn.tx_bytes == 8


def test_text_with_star_checksum_then_line_ending(linked):
    win, board = linked
    win._eol_combo.setCurrentText("\\r\\n")
    btn = win._frame_btn
    btn._algo_actions["xor8"].trigger()
    btn._format_actions["star"].trigger()
    type_and_send(win, "$AB")
    assert board.read() == b"$AB*03\r\n"
    assert "$AB   [*03]" in terminal(win)


def test_choosing_a_checksum_uses_its_usual_byte_order_but_keeps_a_text_form(linked):
    win, _board = linked
    btn = win._frame_btn
    btn._algo_actions["crc16-modbus"].trigger()
    assert btn.format == "low_first"
    btn._algo_actions["crc32"].trigger()
    assert btn.format == "high_first"
    btn._format_actions["hex"].trigger()
    btn._algo_actions["crc16-modbus"].trigger()
    assert btn.format == "hex"


def test_bad_hex_sends_nothing_and_keeps_the_text(linked):
    win, board = linked
    win._frame_btn._input_actions[True].trigger()
    type_and_send(win, "01 0G")
    assert board.read(0.2) == b""
    assert win._cmd_edit.text() == "01 0G"
    assert "Not hex bytes" in terminal(win)


def test_controls_and_trigger_replies_get_the_checksum(linked, qapp):
    from core.triggers import Trigger
    win, board = linked
    win._eol_combo.setCurrentText("None")
    win._frame_btn._algo_actions["xor8"].trigger()
    assert win._serial.send_bytes(b"AB", "AB", line_ending=False)
    assert board.read() == b"AB\x03"
    conn = win._active_conn
    conn.engine.add_trigger(Trigger("r", "PING", reply="AB"))
    board.send(b"PING\r\n")
    pump(qapp, 0.3)
    assert board.read() == b"AB\x03"


def test_frame_settings_survive_a_restart(qapp, window, tmp_path, monkeypatch):
    win = window
    from ui.controllers.update_manager import UpdateManager
    from ui.main_window import MainWindow
    win._frame_btn._input_actions[True].trigger()
    win._frame_btn._algo_actions["crc32"].trigger()
    win._frame_btn._format_actions["hex"].trigger()
    win._repeat_ms.setValue(250)
    win._repeat_count.setValue(4)
    win._tcp_share.port = 8123
    win._settings.save()
    monkeypatch.setattr(UpdateManager, "start", lambda self: None)
    again = MainWindow()
    try:
        assert again._frame_btn.to_dict() == {"hex": True, "algorithm": "crc32", "format": "hex"}
        assert not again._eol_combo.isEnabled()
        assert again._repeat_ms.value() == 250 and again._repeat_count.value() == 4
        assert again._tcp_share.port == 8123
        assert again._active_conn.port_share is None        # never on by itself
    finally:
        again.close()


def test_rubbish_frame_settings_are_ignored(window):
    win = window
    win._frame_btn.from_dict({"algorithm": "md5", "format": "sideways"})
    assert win._frame_btn.algorithm == "" and win._frame_btn.format == "high_first"
    win._frame_btn.from_dict("nonsense")


# ── Repeat ────────────────────────────────────────────────────────────────────

def start_repeat(win, text, ms=20, count=0):
    win._custom_cmd.setPlainText(text)
    win._repeat_ms.setValue(ms)
    win._repeat_count.setValue(count)
    win._serial.toggle_repeat()


def test_repeat_sends_the_count_then_stops(linked, qapp):
    win, board = linked
    win._eol_combo.setCurrentText("\\n")
    start_repeat(win, "PING", count=3)
    assert win._active_conn.repeat is not None
    assert win._repeat_btn.text() == "Stop"
    assert win._sb_activity.isVisibleTo(win) and "20 ms" in win._sb_activity.text()
    pump(qapp, 0.3)
    assert board.read(0.1) == b"PING\n" * 3
    assert win._active_conn.repeat is None
    assert win._repeat_btn.text() == "Repeat"
    assert not win._sb_activity.isVisibleTo(win)
    assert "3 sent" in terminal(win)


def test_repeat_once_sends_once(linked, qapp):
    win, board = linked
    win._eol_combo.setCurrentText("\\n")
    start_repeat(win, "ONE", count=1)
    pump(qapp, 0.1)
    assert board.read(0.1) == b"ONE\n"
    assert win._active_conn.repeat is None


def test_repeat_until_stopped(linked, qapp):
    win, board = linked
    win._eol_combo.setCurrentText("\\n")
    start_repeat(win, "T", ms=20)
    pump(qapp, 0.25)
    win._serial.toggle_repeat()
    got = board.read(0.1).count(b"T\n")
    assert got >= 5
    pump(qapp, 0.1)
    assert board.read(0.1) == b""


def test_repeat_frame_is_fixed_when_it_starts(linked, qapp):
    win, board = linked
    win._frame_btn._input_actions[True].trigger()
    win._frame_btn._algo_actions["crc16-modbus"].trigger()
    start_repeat(win, "01 03 00 00 00 02", count=2)
    win._custom_cmd.setPlainText("FF")          # edits don't change a running repeat
    pump(qapp, 0.15)
    assert board.read(0.1) == bytes.fromhex("010300000002C40B") * 2


def test_repeat_refused_with_bad_hex_or_no_connection(qapp, window):
    win = window
    win._frame_btn._input_actions[True].trigger()
    start_repeat(win, "nope")
    assert win._active_conn.repeat is None
    win._frame_btn._input_actions[False].trigger()
    start_repeat(win, "PING")                   # not connected
    assert win._active_conn.repeat is None
    assert win._repeat_btn.text() == "Repeat"
    assert "Not connected" in terminal(win)


def test_repeat_stops_when_the_connection_closes(linked, qapp):
    win, board = linked
    start_repeat(win, "T", ms=20)
    win._serial.toggle_connection()
    pump(qapp, 0.2)
    assert win._active_conn.repeat is None
    assert "the connection closed" in terminal(win)
    assert not win._active_conn.reader.isRunning()


def test_repeat_stops_when_the_board_is_unplugged(qapp, window, tmp_path):
    win = window
    board = Board(tmp_path / "ttyGONE")
    _select(win, board.link)
    win._serial.toggle_connection()
    pump(qapp, 0.2)
    start_repeat(win, "T", ms=20)
    board.unplug()
    pump(qapp, 0.5)
    assert win._active_conn.repeat is None


def test_repeat_belongs_to_its_device(linked, qapp):
    win, board = linked
    win._eol_combo.setCurrentText("\\n")
    first = win._active_conn
    start_repeat(win, "BG", ms=20)
    win._add_connection()
    pump(qapp, 0.05)
    assert win._active_conn is not first
    assert win._repeat_btn.text() == "Repeat"        # the new tab isn't repeating
    assert not win._sb_activity.isVisibleTo(win)
    before = first.tx_bytes
    tx_label = win._sb_tx.text()
    pump(qapp, 0.25)
    assert first.tx_bytes > before                   # carries on in the background
    assert win._sb_tx.text() == tx_label             # active tab's figures untouched
    win._set_active_connection(first)
    assert win._repeat_btn.text() == "Stop"
    win._serial.toggle_repeat()
    assert board.read(0.1).count(b"BG\n") >= 5


# ── Snapshots ─────────────────────────────────────────────────────────────────

def add_snapshot_trigger(conn, tmp_path, **options):
    from core.triggers import Trigger
    conn.logger.set_log_dir(str(tmp_path / "logs"))
    conn.engine.add_trigger(Trigger("Fault!", "FAULT", snapshot=True, snapshot_before=2,
                                    snapshot_after=2, action_flash=False, **options))
    return tmp_path / "logs" / "snapshots"


def test_snapshot_through_the_real_receive_path(linked, qapp, tmp_path):
    win, board = linked
    folder = add_snapshot_trigger(win._active_conn, tmp_path)
    board.send(b"one\r\ntwo\r\nthree\r\nFAULT here\r\nfour\r\nfive\r\nsix\r\n")
    pump(qapp, 0.4)
    files = list(folder.iterdir())
    assert len(files) == 1 and files[0].name.startswith("Fault_")
    text = files[0].read_text()
    body = [ln[14:] for ln in text.splitlines() if ln[:2].isdigit()]
    assert body == ["    two", "    three", ">>  FAULT here", "    four", "    five"]
    assert "Snapshot saved" in terminal(win) and files[0].name in terminal(win)


def test_snapshot_written_when_the_device_goes_silent(linked, qapp, tmp_path, monkeypatch):
    from core.snapshot import SnapshotRecorder
    monkeypatch.setattr(SnapshotRecorder, "IDLE_TIMEOUT", 0.2)
    win, board = linked
    folder = add_snapshot_trigger(win._active_conn, tmp_path)
    board.send(b"before\r\nFAULT\r\n")
    pump(qapp, 0.15)
    assert not folder.exists()
    pump(qapp, 1.3)                      # the once-a-second check
    text = next(folder.iterdir()).read_text()
    assert "Ended 2 line(s) short" in text


def test_snapshot_includes_lines_that_were_only_logged_during_an_overload(linked, qapp, tmp_path):
    win, _board = linked
    conn = win._active_conn
    folder = add_snapshot_trigger(conn, tmp_path)
    old = time.monotonic() - 10
    conn.rx_backlog.extend((t, "12:00:00.000", old, t.encode())
                           for t in ("a", "b", "FAULT", "c", "d"))
    win._shed_overload(conn)
    body = next(folder.iterdir()).read_text()
    assert ">>  FAULT" in body and body.rstrip().endswith("d")


def test_open_snapshot_is_written_on_close(qapp, window, tmp_path):
    win = window
    conn = win._active_conn
    folder = add_snapshot_trigger(conn, tmp_path)
    conn.snapshots.feed("FAULT", "12:00:00.000")
    conn.engine.check("FAULT", "12:00:00.000")
    assert not folder.exists()
    win.close()
    assert len(list(folder.iterdir())) == 1


def _webhook():
    got = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, got


def test_snapshot_goes_to_the_url_only_when_asked(linked, qapp, tmp_path):
    win, board = linked
    server, got = _webhook()
    url = f"http://127.0.0.1:{server.server_port}/hook"
    conn = win._active_conn
    add_snapshot_trigger(conn, tmp_path, notify_url=url)
    board.send(b"a\r\nFAULT\r\nb\r\nc\r\n")
    pump(qapp, 0.5)
    assert [m["line"] for m in got] == ["FAULT"] and "snapshot" not in got[0]

    got.clear()
    conn.engine.get_triggers()[-1].snapshot_notify = True
    conn.snapshots._last_start.clear()
    board.send(b"d\r\nFAULT\r\ne\r\nf\r\n")
    pump(qapp, 0.5)
    server.shutdown()
    with_snapshot = [m for m in got if "snapshot" in m]
    assert len(with_snapshot) == 1
    assert ">>  FAULT" in with_snapshot[0]["snapshot"] and with_snapshot[0]["trigger"] == "Fault!"


def test_snapshot_options_in_the_trigger_editor(qapp, window):
    win = window
    panel = win._active_conn.trigger
    panel._open_editor()
    assert not panel._a_snapshot.isChecked() and not panel._snap_before.isEnabled()
    panel._pat.setText("BOOM")
    panel._a_snapshot.setChecked(True)
    assert panel._snap_before.isEnabled()
    panel._snap_before.setValue(7)
    panel._snap_after.setValue(9)
    panel._snap_notify.setChecked(True)
    panel._save()
    t = win._active_conn.engine.get_triggers()[-1]
    assert (t.pattern, t.snapshot, t.snapshot_before, t.snapshot_after, t.snapshot_notify) == (
        "BOOM", True, 7, 9, True)
    panel._open_editor(panel._rows[-1])
    assert panel._a_snapshot.isChecked() and panel._snap_before.value() == 7
    panel._close_editor()
    panel._open_editor()
    assert not panel._a_snapshot.isChecked() and panel._snap_before.value() == 100


def test_checksum_trigger_from_the_editor_and_refused_algorithm(qapp, window):
    win = window
    panel = win._active_conn.trigger
    count = len(win._active_conn.engine.get_triggers())
    panel._open_editor()
    panel._type_c.setCurrentText("checksum")
    assert "xor8" in panel._hint.text()
    panel._pat.setText("xor8")
    panel._save()
    assert win._active_conn.engine.get_triggers()[-1].type == "checksum"
    assert len(win._active_conn.engine.get_triggers()) == count + 1


# ── TCP share ─────────────────────────────────────────────────────────────────

def share_client(share):
    c = socket.create_connection(("127.0.0.1", share.port), timeout=3)
    c.settimeout(3)
    return c


def read_n(sock, n):
    out = b""
    while len(out) < n:
        got = sock.recv(n - len(out))
        assert got, f"closed early, after {out!r}"
        out += got
    return out


def test_device_and_client_talk_through_the_share(linked, qapp):
    win, board = linked
    conn = win._active_conn
    assert win._tcp_share.start(conn, "127.0.0.1", 0) is None
    share = conn.port_share
    c = share_client(share)
    pump(qapp, 0.1)
    assert f"TCP :{share.port} (1 connected)" in win._sb_activity.text()

    blob = bytes(range(256)) + b"\r\n"
    board.send(blob)
    assert read_n(c, len(blob)) == blob

    c.sendall(b"from tcp\r\n")
    pump(qapp, 0.4)
    assert board.read(0.1) == b"from tcp\r\n"
    assert conn.tx_bytes == 10
    text = terminal(win)
    assert "[tcp] from tcp" in text
    c.close()
    pump(qapp, 0.1)
    assert "(0 connected)" in win._sb_activity.text()


def test_another_isodaq_can_connect_with_socket_url(linked, qapp):
    from core.net_reader import NetworkReader
    win, board = linked
    conn = win._active_conn
    assert win._tcp_share.start(conn, "127.0.0.1", 0) is None
    other = NetworkReader()
    lines = []
    other.lines_received.connect(lambda batch: lines.extend(b[0] for b in batch))
    other.configure(f"socket://127.0.0.1:{conn.port_share.port}")
    assert other.connect_port() is None
    pump(qapp, 0.3)
    board.send(b"hello other app\r\n")
    pump(qapp, 0.4)
    assert lines == ["hello other app"]
    assert other.send(b"reply\r\n") is None
    pump(qapp, 0.4)
    assert board.read(0.1) == b"reply\r\n"
    other.disconnect_port()
    pump(qapp, 0.1)


def test_share_ends_with_the_connection(linked, qapp):
    win, _board = linked
    conn = win._active_conn
    assert win._tcp_share.start(conn, "127.0.0.1", 0) is None
    port = conn.port_share.port
    win._serial.toggle_connection()
    pump(qapp, 0.3)
    assert conn.port_share is None and conn.reader.tap is None
    assert not win._sb_activity.isVisibleTo(win)
    assert "TCP sharing stopped" in terminal(win)
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=0.5)


def test_share_lives_through_an_automatic_reconnect(qapp, window, tmp_path):
    win = window
    link = tmp_path / "ttyPLUG"
    board = Board(link)
    _select(win, link)
    conn = win._active_conn
    win._serial.toggle_connection()
    pump(qapp, 0.2)
    assert win._tcp_share.start(conn, "127.0.0.1", 0) is None
    c = share_client(conn.port_share)
    pump(qapp, 0.1)
    board.unplug()
    pump(qapp, 1.3)
    assert conn.reconnecting and conn.port_share is not None
    board = Board(link)
    pump(qapp, 1.5)
    assert conn.reader.isRunning()
    board.send(b"back\r\n")
    assert read_n(c, 6) == b"back\r\n"
    c.close()
    win._serial.toggle_connection()
    pump(qapp, 0.2)
    assert conn.port_share is None
    board.unplug()


def test_stopping_the_reconnect_wait_ends_the_share(qapp, window, tmp_path):
    win = window
    link = tmp_path / "ttyWAIT"
    board = Board(link)
    _select(win, link)
    conn = win._active_conn
    win._serial.toggle_connection()
    pump(qapp, 0.2)
    assert win._tcp_share.start(conn, "127.0.0.1", 0) is None
    board.unplug()
    pump(qapp, 1.3)
    assert conn.reconnecting
    win._serial.toggle_connection()
    pump(qapp, 0.1)
    assert conn.port_share is None


def test_share_refused_when_not_connected_or_not_a_byte_stream(qapp, window):
    win = window
    from core.modbus_reader import ModbusReader
    from core.mqtt_reader import MQTTReader
    conn = win._active_conn
    assert "Connect" in win._tcp_share.start(conn, "127.0.0.1", 0)
    assert conn.port_share is None
    assert not getattr(MQTTReader(), "shareable", False)
    assert not getattr(ModbusReader(), "shareable", False)
    conn.reader = ModbusReader()
    conn.reader.isRunning = lambda: True
    assert "byte streams" in win._tcp_share.start(conn, "127.0.0.1", 0)


def test_file_transfer_keeps_clients_out(linked, qapp):
    win, board = linked
    conn = win._active_conn
    assert win._tcp_share.start(conn, "127.0.0.1", 0) is None
    c = share_client(conn.port_share)
    pump(qapp, 0.1)
    assert conn.reader.acquire_port()
    c.sendall(b"blocked\r\n")
    pump(qapp, 0.4)
    assert board.read(0.1) == b""
    assert "file transfer" in terminal(win)
    conn.reader.release_port()
    c.close()


def test_dialog_starts_and_stops(linked, qapp):
    from core.port_share import ALL_HOSTS, LOCAL_HOST
    from ui.controllers.tcp_share_controller import TcpShareDialog
    win, _board = linked
    conn = win._active_conn
    dlg = TcpShareDialog(win._tcp_share, conn, win)
    dlg.show()
    assert dlg.scope.currentData() == LOCAL_HOST          # the safe choice first
    assert not dlg.warning.isVisible()
    dlg.scope.setCurrentIndex(dlg.scope.findData(ALL_HOSTS))
    assert dlg.warning.isVisible()
    dlg.scope.setCurrentIndex(dlg.scope.findData(LOCAL_HOST))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
    dlg.port.setValue(free)
    dlg.toggle.click()
    assert conn.port_share is not None and conn.port_share.port == free
    assert dlg.address.text() == f"socket://127.0.0.1:{free}"
    assert dlg.toggle.text() == "Stop" and not dlg.scope.isEnabled()
    assert win._tcp_share.port == free
    dlg.toggle.click()
    assert conn.port_share is None and dlg.toggle.text() == "Start"
    dlg.close()


def test_dialog_shows_why_it_couldnt_start(linked, qapp):
    from ui.controllers.tcp_share_controller import TcpShareDialog
    win, _board = linked
    conn = win._active_conn
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        dlg = TcpShareDialog(win._tcp_share, conn, win)
        dlg.port.setValue(taken.getsockname()[1])
        dlg.toggle.click()
    assert conn.port_share is None and dlg.status.text()
    dlg.close()


def test_closing_a_tab_and_the_window_stop_everything(linked, qapp):
    win, _board = linked
    first = win._active_conn
    win._custom_cmd.setPlainText("T")
    win._repeat_ms.setValue(20)
    win._serial.toggle_repeat()
    assert win._tcp_share.start(first, "127.0.0.1", 0) is None
    share = first.port_share
    port = share.port
    win.close()
    assert first.repeat is None and first.port_share is None and not share.running
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=0.5)


# ── Trigger files ─────────────────────────────────────────────────────────────

def _load_trigger_file(monkeypatch, win, path):
    from PyQt6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *_a, **_k: (str(path), "")))
    win._triggers.load_triggers()


def test_load_triggers_updates_the_list(qapp, window, tmp_path, monkeypatch):
    win = window
    path = tmp_path / "rules.json"
    path.write_text(json.dumps([
        {"name": "From file", "pattern": "boom", "snapshot": True, "snapshot_before": 3},
    ]))
    _load_trigger_file(monkeypatch, win, path)
    panel = win._trigger_panel
    assert [r.trigger.name for r in panel._rows] == ["From file"]
    assert win._engine.get_triggers()[0].snapshot_before == 3


def test_loading_python_triggers_disabled_says_so(qapp, window, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    win = window
    path = tmp_path / "rules.json"
    path.write_text(json.dumps([{"name": "Py", "type": "python", "pattern": "lambda l: True"}]))

    def choose_disabled(box):
        box._picked = next(b for b in box.buttons() if b.text() == "Load disabled")

    monkeypatch.setattr(QMessageBox, "exec", choose_disabled)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda box: box._picked)
    _load_trigger_file(monkeypatch, win, path)
    trigger = win._engine.get_triggers()[0]
    assert not trigger.enabled and trigger._blocked
    assert [r.trigger.name for r in win._trigger_panel._rows] == ["Py"]
    assert "DISABLED" in terminal(win)


def test_a_loaded_file_cant_switch_on_sending_snapshots(qapp, window, tmp_path, monkeypatch):
    win = window
    path = tmp_path / "rules.json"
    path.write_text(json.dumps([{"name": "x", "pattern": "y", "snapshot": True,
                                 "snapshot_notify": True, "notify_url": "http://example.invalid/"}]))
    _load_trigger_file(monkeypatch, win, path)
    trigger = win._engine.get_triggers()[0]
    assert trigger.snapshot and not trigger.snapshot_notify
    assert "switched off" in terminal(win)
