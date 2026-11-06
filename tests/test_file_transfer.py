"""XMODEM / YMODEM sending, against an independent receiver in this file, written
from the protocol descriptions and sharing no code with core.file_transfer."""
from __future__ import annotations

import os
import queue
import sys
import threading
import time

import pytest

pytest.importorskip("PyQt6")

from core.file_transfer import (TransferCancelled, TransferError, crc16_xmodem, make_block,
                                send_file)

SOH, STX, EOT, ACK, NAK, CAN = 1, 2, 4, 6, 0x15, 0x18


def test_crc_matches_published_check_value():
    assert crc16_xmodem(b"123456789") == 0x31C3      # CRC-16/XMODEM catalogue check value


def test_block_layout():
    b = make_block(1, b"A" * 128, True)
    assert b[:3] == bytes([SOH, 1, 0xFE]) and len(b) == 133
    assert make_block(255, b"A" * 1024, True)[:3] == bytes([STX, 255, 0])
    assert make_block(256, b"A" * 128, True)[:3] == bytes([SOH, 0, 255])    # wraps
    assert make_block(1, b"\x01" * 128, False)[-1] == 128                   # checksum
    assert len(make_block(1, b"A" * 128, False)) == 132


class Wire:
    """Two byte queues joined up, with a short read timeout like a serial port."""

    def __init__(self):
        self.to_sender: queue.Queue = queue.Queue()
        self.to_receiver: queue.Queue = queue.Queue()

    def sender_read(self, n=1) -> bytes:
        try:
            return bytes([self.to_sender.get(timeout=0.01)])
        except queue.Empty:
            return b""

    def sender_write(self, data: bytes) -> None:
        for b in data:
            self.to_receiver.put(b)

    def rx(self, n, timeout=2.0) -> bytes:
        out = bytearray()
        end = time.monotonic() + timeout
        while len(out) < n and time.monotonic() < end:
            try:
                out.append(self.to_receiver.get(timeout=0.02))
            except queue.Empty:
                pass
        return bytes(out)

    def tx(self, data: bytes) -> None:
        for b in data:
            self.to_sender.put(b)


def crc_ref(data: bytes) -> int:
    """Table-free reference, separate from the implementation."""
    crc = 0
    for byte in data:
        for bit in range(7, -1, -1):
            carry = (crc >> 15) & 1
            crc = (crc << 1) & 0xFFFF
            if carry ^ ((byte >> bit) & 1):
                crc ^= 0x1021
    return crc


class Receiver(threading.Thread):
    """Receives one file, verifying every field of every block."""

    def __init__(self, wire, start=b"C", ymodem=False, faults=(), banner=b""):
        super().__init__(daemon=True)
        self.wire, self.start_byte, self.ymodem = wire, start, ymodem
        self.faults = dict(faults)        # block sequence index → "nak" | "silent"
        self.banner = banner
        self.data, self.header = bytearray(), None
        self.sizes, self.errors = [], []
        self.done = False

    def read_block(self):
        head = self.wire.rx(1)
        if not head:
            return None
        if head[0] == EOT:
            return "eot"
        if head[0] not in (SOH, STX):
            self.errors.append(f"bad header {head[0]}")
            return None
        size = 1024 if head[0] == STX else 128
        use_crc = self.start_byte == b"C"
        rest = self.wire.rx(2 + size + (2 if use_crc else 1))
        number, comp = rest[0], rest[1]
        payload = rest[2:2 + size]
        if (number + comp) & 0xFF != 0xFF:
            self.errors.append("bad complement")
        if use_crc:
            if int.from_bytes(rest[2 + size:], "big") != crc_ref(payload):
                self.errors.append("bad crc")
        elif rest[-1] != sum(payload) & 0xFF:
            self.errors.append("bad checksum")
        return number, payload

    def run(self):
        self.wire.tx(self.banner + self.start_byte)
        expected = 0 if self.ymodem else 1
        seq = 0
        eots = 0
        while True:
            b = self.read_block()
            if b is None:
                return
            if b == "eot":
                eots += 1
                if self.ymodem and eots == 1:
                    self.wire.tx(bytes([NAK]))
                    continue
                self.wire.tx(bytes([ACK]))
                if not self.ymodem:
                    self.done = True
                    return
                self.wire.tx(b"C")
                end = self.read_block()
                if end[0] != 0 or any(end[1]):
                    self.errors.append("bad end-of-batch block")
                self.wire.tx(bytes([ACK]))
                self.done = True
                return
            number, payload = b
            fault = self.faults.pop(seq, None)
            seq += 1
            if fault == "nak":
                self.wire.tx(bytes([NAK]))
                continue
            if fault == "silent":
                continue
            if number != expected & 0xFF:
                self.errors.append(f"block {number}, expected {expected & 0xFF}")
            if self.ymodem and expected == 0:
                self.header = payload
                self.wire.tx(bytes([ACK]) + b"C")
            else:
                self.data += payload
                self.sizes.append(len(payload))
                self.wire.tx(bytes([ACK]))
            expected += 1


def run(data, mode, **kw):
    wire = Wire()
    send_kw = {k: kw.pop(k) for k in ("should_cancel", "start_timeout", "progress") if k in kw}
    rx = Receiver(wire, **kw)
    rx.start()
    send_file(wire.sender_read, wire.sender_write, data, "fw.bin", mode, **send_kw)
    rx.join(3)
    assert rx.done and not rx.errors, rx.errors
    return rx


PAYLOAD = bytes(range(256)) * 12 + b"tail"      # 3076 bytes


def test_xmodem_crc_pads_with_ctrl_z():
    rx = run(PAYLOAD, "XMODEM")
    assert set(rx.sizes) == {128}
    assert bytes(rx.data).rstrip(b"\x1a") == PAYLOAD
    assert len(rx.data) % 128 == 0


def test_xmodem_checksum_when_the_receiver_nak_starts():
    rx = run(PAYLOAD, "XMODEM", start=bytes([NAK]))
    assert bytes(rx.data).rstrip(b"\x1a") == PAYLOAD


def test_xmodem_1k_blocks_and_short_tail():
    rx = run(PAYLOAD, "XMODEM-1K")
    assert rx.sizes[:3] == [1024, 1024, 1024] and rx.sizes[-1] == 128
    assert bytes(rx.data).rstrip(b"\x1a") == PAYLOAD


def test_checksum_receiver_never_gets_1k_blocks():
    rx = run(PAYLOAD, "XMODEM-1K", start=bytes([NAK]))
    assert set(rx.sizes) == {128}


def test_ymodem_header_and_batch_end():
    rx = run(PAYLOAD, "YMODEM", ymodem=True)
    assert rx.header.split(b"\0")[0] == b"fw.bin"
    assert rx.header.split(b"\0")[1] == str(len(PAYLOAD)).encode()
    assert bytes(rx.data)[:len(PAYLOAD)] == PAYLOAD


def test_exact_multiple_has_no_extra_block():
    rx = run(b"x" * 256, "XMODEM")
    assert bytes(rx.data) == b"x" * 256


def test_block_number_wraps_past_255():
    data = os.urandom(128 * 300)
    rx = run(data, "XMODEM")
    assert bytes(rx.data) == data


def test_nak_causes_a_resend():
    rx = run(PAYLOAD, "XMODEM", faults={2: "nak", 5: "nak"})
    assert bytes(rx.data).rstrip(b"\x1a") == PAYLOAD


def test_a_banner_with_c_letters_does_not_start_it():
    rx = run(PAYLOAD, "XMODEM", banner=b"Connected. CAUTION: Ready\r\nC")
    assert bytes(rx.data).rstrip(b"\x1a") == PAYLOAD


def test_progress_reaches_total():
    seen = []
    run(PAYLOAD, "XMODEM", progress=lambda s, t: seen.append((s, t)))
    assert seen[-1] == (len(PAYLOAD), len(PAYLOAD))
    assert [s for s, _ in seen] == sorted(s for s, _ in seen)


def test_no_receiver_times_out_with_advice():
    wire = Wire()
    with pytest.raises(TransferError, match="never started"):
        send_file(wire.sender_read, wire.sender_write, b"x", "f", "XMODEM", start_timeout=0.1)


def test_receiver_cancel_is_reported():
    wire = Wire()
    wire.tx(bytes([CAN, CAN]))
    with pytest.raises(TransferError, match="cancelled"):
        send_file(wire.sender_read, wire.sender_write, b"x", "f", "XMODEM", start_timeout=1)


def test_cancel_tells_the_receiver():
    wire = Wire()
    flag = []
    threading.Timer(0.1, lambda: flag.append(1)).start()
    with pytest.raises(TransferCancelled):
        send_file(wire.sender_read, wire.sender_write, b"x", "f", "XMODEM",
                  should_cancel=lambda: bool(flag), start_timeout=5)
    assert wire.rx(2) == bytes([CAN, CAN])


# ── On a real connection, through the window ────────────────────────────────────

class PtyWire:
    """The receiver's side of a pty, with the Wire interface."""

    def __init__(self):
        import select
        self._select = select.select
        self.master, self.slave = os.openpty()
        self.port = os.ttyname(self.slave)

    def rx(self, n, timeout=2.0):
        out = bytearray()
        end = time.monotonic() + timeout
        while len(out) < n and time.monotonic() < end:
            if self._select([self.master], [], [], 0.02)[0]:
                out += os.read(self.master, n - len(out))
        return bytes(out)

    def tx(self, data):
        os.write(self.master, data)

    def close(self):
        os.close(self.master)
        os.close(self.slave)


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


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


def pump(qapp, cond, sec=10.0):
    end = time.monotonic() + sec
    while time.monotonic() < end and not cond():
        qapp.processEvents()
        time.sleep(0.005)
    assert cond()


def term(win):
    win._flush_terminals()
    return win._terminal.toPlainText()


@pytest.mark.skipif(sys.platform == "win32", reason="needs POSIX ptys")
@pytest.mark.parametrize("mode, ymodem", [("XMODEM", False), ("XMODEM-1K", False), ("YMODEM", True)])
def test_window_sends_over_the_open_port_and_keeps_receiving(qapp, win, tmp_path, mode, ymodem):
    import tty
    wire = PtyWire()
    tty.setraw(wire.slave)
    conn = win._active_conn
    win._port_combo.insertItem(0, wire.port)
    win._port_combo.setCurrentIndex(0)
    win._serial.toggle_connection()
    pump(qapp, conn.reader.isRunning)
    wire.tx(b"before\r\n")
    pump(qapp, lambda: "before" in term(win))

    path = tmp_path / "fw.bin"
    path.write_bytes(PAYLOAD)
    panel = conn.flash_panel
    panel.set_transfer_config(mode, str(path))
    win._transfer.start(conn)
    assert conn.transfer is not None
    pump(qapp, lambda: "Waiting" in panel.transfer_status())
    assert "file transfer" in conn.reader.send(b"typed")    # others are kept out
    assert not win._conn_btn.isEnabled()

    rx = Receiver(wire, ymodem=ymodem)
    rx.start()
    pump(qapp, lambda: conn.transfer is None, sec=20)
    rx.join(3)
    assert rx.done and not rx.errors, rx.errors
    assert bytes(rx.data)[:len(PAYLOAD)] == PAYLOAD
    assert "Sent" in term(win)
    assert panel.transfer_progress() == 100
    assert win._conn_btn.isEnabled()

    wire.tx(b"after\r\n")                    # the terminal has the port back
    pump(qapp, lambda: "after" in term(win))
    assert conn.reader.send(b"ok") is None
    win._serial.toggle_connection()
    pump(qapp, lambda: not conn.reader.isRunning())
    wire.close()


@pytest.mark.skipif(sys.platform == "win32", reason="needs POSIX ptys")
def test_window_cancel_and_refusals(qapp, win, tmp_path):
    import tty
    conn = win._active_conn
    panel = conn.flash_panel
    path = tmp_path / "fw.bin"
    path.write_bytes(b"x" * 10)

    panel.set_transfer_config("XMODEM", str(path))
    win._transfer.start(conn)                # not connected
    assert conn.transfer is None and "Connect first" in panel.transfer_status()

    wire = PtyWire()
    tty.setraw(wire.slave)
    win._port_combo.insertItem(0, wire.port)
    win._port_combo.setCurrentIndex(0)
    win._serial.toggle_connection()
    pump(qapp, conn.reader.isRunning)

    panel.set_transfer_config("XMODEM", str(tmp_path / "missing.bin"))
    win._transfer.start(conn)
    assert conn.transfer is None and "not found" in panel.transfer_status()

    panel.set_transfer_config("XMODEM", str(path))
    win._transfer.start(conn)
    pump(qapp, lambda: "Waiting" in panel.transfer_status())
    win._transfer.cancel(conn)
    pump(qapp, lambda: conn.transfer is None)
    assert "Cancelled" in panel.transfer_status()
    assert wire.rx(2) == bytes([CAN, CAN])
    win._serial.toggle_connection()
    pump(qapp, lambda: not conn.reader.isRunning())
    wire.close()


def test_transfer_needs_a_byte_stream_transport(qapp, win, tmp_path):
    from fake_broker import FakeBroker
    b = FakeBroker()
    conn = win._active_conn
    win._transport_combo.setCurrentText("MQTT")
    win._port_combo.setEditText(f"mqtt://127.0.0.1:{b.port}/x")
    win._serial.toggle_connection()
    pump(qapp, lambda: conn.reader.isRunning() and "Connected" in term(win))
    path = tmp_path / "f"
    path.write_bytes(b"x")
    conn.flash_panel.set_transfer_config("XMODEM", str(path))
    win._transfer.start(conn)
    assert conn.transfer is None and "Serial or Network" in conn.flash_panel.transfer_status()
    win._serial.toggle_connection()
    pump(qapp, lambda: not conn.reader.isRunning())
    b.stop()
