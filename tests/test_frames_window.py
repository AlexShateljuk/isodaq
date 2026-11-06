"""Binary frames in the real window (CMP15): a device on a pty sends real bytes."""
import os
import socket
import struct
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")
pytest.importorskip("pyqtgraph")
pytest.importorskip("serial")

from test_device_control import Board, _select, pump  # noqa: E402

from core.binary_frames import Field, FrameSpec  # noqa: E402

TAIL = bytes([0x00, 0x00, 0x80, 0x7F])


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
    board = Board(tmp_path / "ttyFRAMES")
    _select(window, board.link)
    window._serial.toggle_connection()
    pump(qapp, 0.2)
    assert window._active_conn.reader.isRunning()
    yield window, board
    if window._active_conn.reader.isRunning():
        window._serial.toggle_connection()
        pump(qapp, 0.1)
    board.unplug()


def terminal(win):
    win._flush_terminals()
    return win._terminal.toPlainText()


def decode(win, spec):
    panel = win._active_conn.frames
    panel.set_spec(spec)
    panel.enabled.setChecked(True)
    assert panel.decoder is not None, panel.status.text()
    return panel


SPEC = FrameSpec(header="AA 55", fields=[Field("temp", "i16", 0.1), Field("rpm", "u16")],
                 checksum="xor8")


def frame(temp, rpm):
    body = b"\xAA\x55" + struct.pack("<hH", temp, rpm)
    check = 0
    for b in body:
        check ^= b
    return body + bytes([check])


def test_frames_reach_the_terminal_panel_and_plot(linked, qapp):
    win, board = linked
    panel = decode(win, SPEC)
    board.send(frame(215, 1500) + frame(-30, 1600))
    pump(qapp, 0.8)
    text = terminal(win)
    assert "temp:21.5 rpm:1500" in text and "temp:-3 rpm:1600" in text
    assert panel.decoder.frames == 2
    assert "2 frames" in panel.status.text()
    assert panel.table.item(1, 3).text() == "1600"
    conn = win._active_conn
    assert conn.rx_bytes == 14               # what came over the wire, not the text
    assert list(conn.chart._channels["rpm"]["buf_v"]) == [1500, 1600]   # Quick Plot


def test_boot_log_between_frames_stays_readable(linked, qapp):
    win, board = linked
    decode(win, SPEC)
    board.send(b"I (120) boot: ready\r\n" + frame(215, 1500) + b"E (200) brownout\r\n")
    pump(qapp, 0.5)
    text = terminal(win)
    assert "I (120) boot: ready" in text and "temp:21.5" in text and "E (200) brownout" in text


def test_a_bad_checksum_is_dropped_and_counted(linked, qapp):
    win, board = linked
    panel = decode(win, SPEC)
    bad = bytearray(frame(999, 999))
    bad[-1] ^= 0xFF
    board.send(bytes(bad) + frame(215, 1500))
    pump(qapp, 0.5)
    text = terminal(win)
    assert "temp:21.5" in text and "99.9" not in text
    assert panel.decoder.bad_checksum == 1
    pump(qapp, 0.6)
    assert "1 bad checksum" in panel.status.text()


def test_triggers_and_parsing_see_decoded_values(linked, qapp, tmp_path):
    from core.triggers import Trigger
    win, board = linked
    conn = win._active_conn
    decode(win, SPEC)
    conn.engine.add_trigger(Trigger("hot", r"temp:[5-9]\d", type="regex", action_flash=False))
    board.send(frame(215, 1) + frame(650, 2))
    pump(qapp, 0.5)
    assert conn.engine.get_triggers()[-1].hit_count == 1


def test_hex_view_shows_the_real_frame_bytes(linked, qapp):
    win, board = linked
    decode(win, SPEC)
    win._chk_hex.setChecked(True)
    board.send(frame(215, 1500))
    pump(qapp, 0.4)
    assert "AA 55" in terminal(win).upper()


def test_switching_off_goes_back_to_lines(linked, qapp):
    win, board = linked
    panel = decode(win, SPEC)
    panel.enabled.setChecked(False)
    assert win._active_conn.reader.frame_decoder is None
    board.send(b"plain text again\r\n")
    pump(qapp, 0.3)
    assert "plain text again" in terminal(win)


def test_justfloat_preset_from_the_combo(linked, qapp):
    win, board = linked
    panel = win._active_conn.frames
    panel.preset.setCurrentIndex(panel.preset.findData("VOFA+ JustFloat"))
    panel.preset.activated.emit(panel.preset.currentIndex())
    assert panel.any_floats.isChecked() and panel.tail.text() == "00 00 80 7F"
    assert panel.preset.currentData() == "VOFA+ JustFloat"
    panel.enabled.setChecked(True)
    board.send(TAIL + struct.pack("<3f", 1.5, 2.5, 3.5) + TAIL)
    pump(qapp, 0.4)
    assert "ch1:1.5 ch2:2.5 ch3:3.5" in terminal(win)


def test_tcp_share_still_gets_the_raw_bytes(linked, qapp):
    win, board = linked
    conn = win._active_conn
    decode(win, SPEC)
    assert win._tcp_share.start(conn, "127.0.0.1", 0) is None
    c = socket.create_connection(("127.0.0.1", conn.port_share.port), timeout=3)
    c.settimeout(3)
    pump(qapp, 0.1)
    data = frame(215, 1500)
    board.send(data)
    got = b""
    while len(got) < len(data):
        got += c.recv(100)
    assert got == data
    c.close()


def test_invalid_layout_says_why_and_decodes_nothing(linked, qapp):
    win, board = linked
    panel = win._active_conn.frames
    panel.set_spec(FrameSpec(header="ZZ", fields=[Field("a")]))
    panel.enabled.setChecked(True)
    assert panel.decoder is None and "Not hex" in panel.status.text()
    assert win._active_conn.reader.frame_decoder is None
    board.send(b"still lines\r\n")
    pump(qapp, 0.3)
    assert "still lines" in terminal(win)


def test_each_device_has_its_own_layout(linked, qapp):
    win, board = linked
    first = win._active_conn
    decode(win, SPEC)
    win._add_connection()
    second = win._active_conn
    assert second.frames is not first.frames and second.frames.decoder is None
    assert win._frames_stack.currentWidget() is second.frames
    board.send(frame(215, 1500))
    pump(qapp, 0.4)
    assert first.frames.decoder.frames == 1
    win._set_active_connection(first)
    assert win._frames_stack.currentWidget() is first.frames


def test_layout_survives_a_restart(qapp, window, monkeypatch):
    from ui.controllers.update_manager import UpdateManager
    from ui.main_window import MainWindow
    decode(window, SPEC)
    window._active_conn.frames.show_other.setChecked(False)
    window._settings.save()
    monkeypatch.setattr(UpdateManager, "start", lambda self: None)
    again = MainWindow()
    try:
        panel = again._active_conn.frames
        assert panel.spec() == SPEC and panel.enabled.isChecked()
        assert panel.decoder is not None and not panel.decoder.show_other
        assert again._active_conn.reader.frame_decoder is panel.decoder
    finally:
        again.close()


def test_a_reader_made_later_gets_the_decoder(qapp, window):
    decode(window, SPEC)
    reader = window._serial._reader_for("Network")
    assert reader.frame_decoder is window._active_conn.frames.decoder


def test_transports_that_cant_decode_say_so(qapp, window):
    conn = window._active_conn
    decode(window, SPEC)
    conn.reader = window._serial._reader_for("MQTT")
    window._serial.on_connected(conn, "mqtt://x", 0)
    assert "only decoded on" in terminal(window)


@pytest.mark.parametrize("sample, expected", [
    ("AA 55 D7 00 DC 05 F1", "temp=21.5  rpm=1500"),
    ("AA 55 D7 00 DC 05 00", "checksum"),
    ("AA 55 D7", "Not a whole frame: 3 bytes, and this layout has 7"),
    ("not hex", "Not hex"),
])
def test_trying_a_pasted_frame(qapp, window, sample, expected):
    panel = window._active_conn.frames
    panel.set_spec(SPEC)
    assert frame(215, 1500).hex(" ").upper() == "AA 55 D7 00 DC 05 F1"
    panel.sample.setText(sample)
    panel.try_sample()
    assert expected in panel.sample_result.text()


def test_rows_and_validation_in_the_table(qapp, window):
    panel = window._active_conn.frames
    panel.header.setText("7E")
    panel.add_field()
    panel.add_field()
    assert [f.name for f in panel.spec().fields] == ["ch1", "ch2"]
    panel.enabled.setChecked(True)
    assert panel.decoder is not None
    panel.table.item(1, 2).setText("oops")
    assert panel.decoder is None and "Scale" in panel.status.text()
    panel.table.item(1, 2).setText("2")
    assert panel.decoder is not None
    panel._delete_row_of(panel.table.cellWidget(0, 4))
    assert [f.name for f in panel.spec().fields] == ["ch2"]


def test_sustained_high_rate_loses_nothing(linked, qapp):
    win, board = linked
    panel = decode(win, FrameSpec(header="AA 55", fields=[Field("n", "u32"), Field("x", "f32")]))
    count = 5000
    sent = 0
    end = time.monotonic() + 30
    while sent < count and time.monotonic() < end:
        batch = b"".join(b"\xAA\x55" + struct.pack("<If", sent + i, 1.5) for i in range(50))
        board.send(batch)
        sent += 50
        pump(qapp, 0.005)
    pump(qapp, 0.5)
    assert panel.decoder.frames == count and panel.decoder.bad_length == 0
    assert panel.decoder.last["n"] == count - 1
