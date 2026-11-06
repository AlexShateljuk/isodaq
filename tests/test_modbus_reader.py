"""Modbus polling through the real reader and window, against a real-frame slave."""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

from fake_modbus import RtuSlave, TcpSlave

from core.modbus import Point, crc16
from core.modbus_reader import ModbusReader, parse_tcp_target, values_to_lines
from core.quick_plot import quick_parse


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


def wait(qapp, cond, sec=5.0):
    end = time.monotonic() + sec
    while time.monotonic() < end and not cond():
        qapp.processEvents()
        time.sleep(0.005)
    assert cond()


def test_parse_tcp_target():
    assert parse_tcp_target("tcp://10.0.0.5") == ("10.0.0.5", 502)
    assert parse_tcp_target("tcp://plc.local:1502/") == ("plc.local", 1502)
    assert parse_tcp_target("/dev/ttyUSB0") is None
    assert parse_tcp_target("COM7") is None
    for bad in ("tcp://", "tcp://h:99999", "tcp://h:abc"):
        with pytest.raises(ValueError):
            parse_tcp_target(bad)


def test_values_become_lines_quick_plot_understands():
    values = {f"r{i}": float(i) for i in range(10)}
    values["t"] = 21.5
    lines = values_to_lines(values)
    assert len(lines) == 2
    merged = {}
    for line in lines:
        merged.update(quick_parse(line))
    assert merged == values
    assert values_to_lines({"big": 123456789.0}) == ["big:123456789"]   # no 1.23e+08


class Session:
    def __init__(self, target, points, poll_ms=30, **kw):
        self.reader = ModbusReader()
        self.lines, self.values, self.errors, self.up, self.down, self.status = [], [], [], [], [], []
        self.reader.lines_received.connect(lambda b: self.lines.extend(x[0] for x in b))
        self.reader.values_read.connect(self.values.append)
        self.reader.error_occurred.connect(self.errors.append)
        self.reader.connected.connect(lambda label, baud: self.up.append((label, baud)))
        self.reader.disconnected.connect(lambda: self.down.append(1))
        self.reader.status.connect(self.status.append)
        self.reader.configure(target, **kw)
        self.reader.set_points(points)
        self.reader.poll_ms = poll_ms
        assert self.reader.connect_port() is None


@pytest.fixture
def slave():
    s = TcpSlave()
    s.holding.update({0: 215, 1: 400, 2: 0x42F6, 3: 0xE979})
    s.input[10] = 7
    s.coils[3] = True
    yield s
    s.stop()


POINTS = [Point("temp", address=0, scale=0.1), Point("hum", address=1),
          Point("f", address=2, fmt="f32"), Point("rpm", table="input", address=10),
          Point("relay", table="coil", address=3)]


def test_tcp_poll_write_and_stop(qapp, slave):
    s = Session(f"tcp://127.0.0.1:{slave.port}", POINTS)
    wait(qapp, lambda: s.values)
    assert s.up == [(f"modbus://127.0.0.1:{slave.port}", 0)]
    v = s.values[0]
    assert v["temp"] == pytest.approx(21.5) and v["hum"] == 400 and v["rpm"] == 7
    assert v["f"] == pytest.approx(123.456, abs=1e-3) and v["relay"] == 1
    assert quick_parse(s.lines[0])["hum"] == 400
    assert len([r for r in slave.requests if r[0] == 3]) >= 1

    assert s.reader.send(b"write temp 30.5\r\n") is None
    wait(qapp, lambda: slave.holding[0] == 305)
    assert s.reader.send(b"write relay 0\r\n") is None
    wait(qapp, lambda: slave.coils[3] is False)
    assert "write NAME VALUE" in s.reader.send(b"hello")
    assert "No register" in s.reader.send(b"write nope 1")
    assert "read-only" in s.reader.send(b"write rpm 1")
    assert "doesn't fit" in s.reader.send(b"write hum 99999")
    assert s.reader.send(b"write hum abc") is not None

    s.reader.disconnect_port()
    qapp.processEvents()
    assert s.down == [1] and not s.reader.lost and s.reader.send(b"write hum 1") == "Not connected"


def test_silent_device_is_reported_once_and_recovers(qapp, slave):
    s = Session(f"tcp://127.0.0.1:{slave.port}", [Point("hum", address=1)], poll_ms=10)
    wait(qapp, lambda: s.values)
    slave.silent = True
    wait(qapp, lambda: any("no response" in x for x in s.lines), sec=6)
    time.sleep(1.2)                          # another timeout passes: not said again
    qapp.processEvents()
    assert sum("no response" in x for x in s.lines) == 1
    assert s.reader.isRunning() and not s.errors           # the link is fine
    slave.silent = False
    wait(qapp, lambda: any("responding again" in x for x in s.lines), sec=6)
    wait(qapp, lambda: "failed" in s.status[-1] and "no response" not in s.status[-1], sec=6)
    s.reader.disconnect_port()


def test_device_exception_doesnt_stop_other_registers(qapp, slave):
    pts = [Point("hum", address=1), Point("missing", table="input", address=500)]
    s = Session(f"tcp://127.0.0.1:{slave.port}", pts)
    wait(qapp, lambda: s.values and any("illegal data address" in x for x in s.lines))
    assert s.values[-1] == {"hum": 400}
    s.reader.disconnect_port()


def test_bad_write_is_reported(qapp, slave, monkeypatch):
    s = Session(f"tcp://127.0.0.1:{slave.port}", [Point("hum", address=1)])
    wait(qapp, lambda: s.values)
    monkeypatch.setattr(slave, "answer", lambda pdu: bytes([pdu[0] | 0x80, 4]))
    assert s.reader.send(b"write hum 5") is None
    wait(qapp, lambda: any("write hum failed: device failure" in x for x in s.lines))
    s.reader.disconnect_port()


def test_lost_tcp_link_is_flagged(qapp, slave):
    s = Session(f"tcp://127.0.0.1:{slave.port}", [Point("hum", address=1)])
    wait(qapp, lambda: s.values)
    slave.kick()
    wait(qapp, lambda: s.down)
    assert s.reader.lost and "lost" in s.errors[-1]
    wait(qapp, lambda: not s.reader.isRunning())


def test_unreachable_device_is_not_a_lost_link(qapp):
    t = TcpSlave()
    port = t.port
    t.stop()
    s = Session(f"tcp://127.0.0.1:{port}", [Point("x")])
    wait(qapp, lambda: s.down)
    assert not s.reader.lost and "Could not open" in s.errors[0]


def test_show_frames_goes_to_the_terminal(qapp, slave):
    s = Session(f"tcp://127.0.0.1:{slave.port}", [Point("hum", address=1)])
    s.reader.show_frames = True
    wait(qapp, lambda: any(x.startswith("modbus<") for x in s.lines))
    tx = next(x for x in s.lines if x.startswith("modbus>"))
    assert tx.endswith("00 00 00 06 01 03 00 01 00 01")   # MBAP + PDU, as hex
    s.reader.disconnect_port()


def test_map_can_change_while_polling(qapp, slave):
    s = Session(f"tcp://127.0.0.1:{slave.port}", [Point("hum", address=1)])
    wait(qapp, lambda: s.values)
    s.reader.set_points([Point("rpm", table="input", address=10)])
    wait(qapp, lambda: "rpm" in s.values[-1])
    s.reader.disconnect_port()


@pytest.mark.skipif(sys.platform == "win32", reason="needs POSIX ptys")
def test_rtu_end_to_end_and_framing(qapp):
    pytest.importorskip("serial")
    rtu = RtuSlave(crc16)
    rtu.holding.update({0: 215, 1: 400})
    s = Session(rtu.port, [Point("temp", address=0, scale=0.1), Point("hum", address=1)],
                baud=115200, parity="N")
    try:
        wait(qapp, lambda: s.values)
        assert s.up == [(rtu.port, 115200)]
        assert s.values[0]["hum"] == 400
        assert s.reader.send(b"write hum 12") is None
        wait(qapp, lambda: rtu.holding[1] == 12)
    finally:
        s.reader.disconnect_port()
        rtu.stop()


# ── Through the window ──────────────────────────────────────────────────────────

@pytest.fixture
def win(qapp, tmp_path, monkeypatch):
    pytest.importorskip("pyqtgraph")
    from ui.controllers.update_manager import UpdateManager
    from ui.main_window import MainWindow
    monkeypatch.setattr(MainWindow, "_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(UpdateManager, "start", lambda self: None)
    w = MainWindow()
    yield w
    w.close()


def _term(win):
    win._flush_terminals()
    return win._terminal.toPlainText()


def test_window_end_to_end(qapp, win, slave):
    conn = win._active_conn
    panel = conn.modbus
    panel.poll.setValue(30)
    panel.add_point(Point("temp", address=0, scale=0.1))
    panel.add_point(Point("hum", address=1))
    win._transport_combo.setCurrentText("Modbus")
    win._port_combo.setEditText(f"tcp://127.0.0.1:{slave.port}")
    win._serial.toggle_connection()
    wait(qapp, lambda: "temp:21.5 hum:400" in _term(win))

    assert conn.short_name == f"127.0.0.1:{slave.port}"
    assert list(conn.chart._channels["hum"]["buf_v"])[0] == 400       # Quick Plot
    wait(qapp, lambda: panel.table.item(1, 6).text() == "400")        # live value
    wait(qapp, lambda: "0 failed" in panel.status.text())

    panel.add_point(Point("rpm", table="input", address=10))          # while connected
    wait(qapp, lambda: "rpm:7" in _term(win))

    win._cmd_edit.setText("write hum 123")
    win._serial.send_command()
    wait(qapp, lambda: slave.holding[1] == 123)
    assert "TX   write hum 123" in _term(win)

    win._cmd_edit.setText("nonsense")
    win._serial.send_command()
    assert "Modbus command: write NAME VALUE" in _term(win)

    win._serial.toggle_connection()
    wait(qapp, lambda: not conn.reader.isRunning())


def test_auto_reconnect_after_device_restart(qapp, win):
    t = TcpSlave()
    t.holding[0] = 1
    port = t.port
    conn = win._active_conn
    conn.modbus.poll.setValue(30)
    conn.modbus.add_point(Point("a"))
    win._transport_combo.setCurrentText("Modbus")
    win._port_combo.setEditText(f"tcp://127.0.0.1:{port}")
    win._serial.toggle_connection()
    wait(qapp, lambda: "a:1" in _term(win))
    t.stop()
    wait(qapp, lambda: conn.reconnecting)
    t = TcpSlave(port=port)
    t.holding[0] = 2
    try:
        wait(qapp, lambda: "a:2" in _term(win), sec=10)
        assert not conn.reconnecting
    finally:
        win._serial.toggle_connection()
        wait(qapp, lambda: not conn.reader.isRunning())
        t.stop()


def test_connecting_with_no_registers_says_what_to_do(qapp, win, slave):
    conn = win._active_conn
    win._transport_combo.setCurrentText("Modbus")
    win._port_combo.setEditText(f"tcp://127.0.0.1:{slave.port}")
    win._serial.toggle_connection()
    wait(qapp, lambda: conn.reader.isRunning())
    assert "add them in the Modbus tool window" in _term(win)
    win._serial.toggle_connection()
    wait(qapp, lambda: not conn.reader.isRunning())


def test_panel_rows_validation_and_persistence(qapp, win):
    panel = win._active_conn.modbus
    panel.add_point(Point("a", unit=2, address=9, fmt="f32", scale=0.5))
    panel.add_point()                       # next address, same unit and format
    assert [(p.name, p.unit, p.address, p.fmt) for p in panel.points()] == \
        [("a", 2, 9, "f32"), ("reg2", 2, 11, "f32")]

    panel.table.item(1, 3).setText("x")     # a broken row isn't polled, and says so
    assert [p.name for p in panel.points()] == ["a"]
    assert "numbers" in panel.status.text()
    assert panel.table.item(1, 6).text() == "invalid"
    panel.table.item(1, 3).setText("11")
    assert panel.table.item(1, 6).text() == "—"

    panel.table.cellWidget(1, 2).setCurrentText("coil")
    assert not panel.table.cellWidget(1, 4).isEnabled()

    data = panel.to_dict()
    win._settings.save()
    from ui.modbus_panel import ModbusPanel
    fresh = ModbusPanel()
    fresh.from_dict(data)
    assert [p.to_dict() for p in fresh.points()] == [p.to_dict() for p in panel.points()]
    panel.table.cellWidget(0, 7).click()    # delete
    assert [p.name for p in panel.points()] == ["reg2"]
