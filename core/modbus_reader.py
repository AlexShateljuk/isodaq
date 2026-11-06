"""
core/modbus_reader.py — Modbus as a transport: poll a register map (CMP18).

Target: a serial port for RTU (using the toolbar's baud and framing), or
``tcp://host[:port]`` for Modbus TCP. Each poll becomes ordinary terminal lines,
``temp:21.5 hum:40``, so the chart, triggers, logging and publishing work on them
unchanged. Problems arrive as ``modbus: …`` lines, which triggers can match too.

To write, send ``write NAME VALUE`` from the command line, a macro or a control.

A device that doesn't answer is reported but the connection stays up: only a lost
serial port or TCP connection counts as a lost link.
"""
from __future__ import annotations

import queue
import socket
import time

from PyQt6.QtCore import QThread, pyqtSignal

from core.modbus import (LinkLost, ModbusDeviceError, ModbusFrameError, ModbusTimeout, Point,
                         RtuLink, TcpLink, check_write, decode_block, plan_reads, read_request,
                         write_request)
from core.stream_reader import deliver

DEFAULT_TCP_PORT = 502
CONNECT_TIMEOUT_S = 5.0
DEFAULT_POLL_MS = 1000
MIN_POLL_MS = 20
#: Pairs per terminal line. Quick Plot treats a much longer line as a table, not data.
VALUES_PER_LINE = 8


def parse_tcp_target(target: str) -> tuple[str, int] | None:
    """``(host, port)`` for a ``tcp://`` target; ``None`` if it's a serial port.
    Raises ``ValueError`` for a malformed one."""
    text = target.strip()
    if not text.startswith("tcp://"):
        return None
    host, colon, port = text[len("tcp://"):].rstrip("/").partition(":")
    if not host:
        raise ValueError("Enter a device, e.g. tcp://192.168.1.10:502")
    if colon and not (port.isdigit() and 0 < int(port) < 65536):
        raise ValueError(f"Invalid port: {port}")
    return host, int(port) if colon else DEFAULT_TCP_PORT


def values_to_lines(values: dict[str, float]) -> list[str]:
    def number(v: float) -> str:
        return f"{v:.10g}"
    items = [f"{k}:{number(v)}" for k, v in values.items()]
    return [" ".join(items[i:i + VALUES_PER_LINE]) for i in range(0, len(items), VALUES_PER_LINE)]


class ModbusReader(QThread):
    line_received  = pyqtSignal(str, str)
    lines_received = pyqtSignal(object)
    error_occurred = pyqtSignal(str)
    connected      = pyqtSignal(str, int)
    disconnected   = pyqtSignal()
    #: For the register table: fresh ``{name: value}``, and a one-line status.
    values_read    = pyqtSignal(object)
    status         = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._disconnected_emitted = False
        self.lost = False
        self._target = ""
        self._baud, self._bytesize, self._parity, self._stopbits = 9600, 8, "E", 1
        self._points: list[Point] = []
        self._blocks = []
        self.poll_ms = DEFAULT_POLL_MS
        self.show_frames = False
        self._writes: queue.Queue = queue.Queue()
        self._link = None
        self._polls = 0
        self._failed = 0
        self._last_problem = ""

    def configure(self, target: str, baud: int = 9600, bytesize: int = 8, parity: str = "E",
                  stopbits: int = 1) -> None:
        parse_tcp_target(target)         # raises for a malformed target
        self._target = target.strip()
        self._baud, self._bytesize, self._parity, self._stopbits = baud, bytesize, parity, stopbits

    def set_points(self, points: list[Point]) -> None:
        """Replace the register map, also while polling (one atomic swap)."""
        self._points = list(points)
        self._blocks = plan_reads(self._points)

    # ── Connect / disconnect ──────────────────────────────────────────────────

    def connect_port(self) -> str | None:
        if not self._target:
            return "No Modbus device set"
        self._disconnected_emitted = False
        self.lost = False
        self._running = True
        self.start()
        return None

    def disconnect_port(self) -> None:
        self._running = False
        self.wait(int(CONNECT_TIMEOUT_S * 1000) + 2000)
        if not self._disconnected_emitted:
            self.disconnected.emit()
        self._disconnected_emitted = False

    def send(self, data: bytes) -> str | None:
        """``write NAME VALUE``. Mistakes are answered here; the write itself runs
        between polls on this thread, and failures come back as ``modbus:`` lines."""
        if self._link is None:
            return "Not connected"
        parts = data.decode("utf-8", errors="replace").split()
        if len(parts) != 3 or parts[0].lower() != "write":
            return "Modbus command: write NAME VALUE"
        point = next((p for p in self._points if p.name == parts[1]), None)
        if point is None:
            return f"No register named {parts[1]}"
        try:
            value = float(parts[2])
            request = write_request(point, value)
        except ValueError as e:
            return str(e)
        self._writes.put((point, request))
        return None

    @staticmethod
    def set_lines(dtr=None, rts=None) -> str | None:   # pylint: disable=unused-argument
        return "DTR/RTS aren't controllable on a Modbus connection"

    @staticmethod
    def set_break(on: bool) -> str | None:   # pylint: disable=unused-argument
        return "BREAK isn't available on a Modbus connection"

    # ── Worker thread ─────────────────────────────────────────────────────────

    def _open(self):
        tcp = parse_tcp_target(self._target)
        if tcp:
            return TcpLink(socket.create_connection(tcp, timeout=CONNECT_TIMEOUT_S))
        import serial
        ser = serial.Serial(self._target, self._baud, bytesize=self._bytesize,
                            parity=self._parity, stopbits=self._stopbits, timeout=0.02,
                            write_timeout=1.0)
        return RtuLink(ser, baud=self._baud)

    def run(self) -> None:
        try:
            link = self._open()
        except Exception as e:
            self.error_occurred.emit(f"Could not open {self._target}: {e}")
            self._finish(lost=False)
            return
        self._link = link
        self._polls = self._failed = 0
        self._last_problem = ""
        tcp = parse_tcp_target(self._target)
        self.connected.emit(f"modbus://{tcp[0]}:{tcp[1]}" if tcp else self._target,
                            0 if tcp else self._baud)
        try:
            while self._running:
                started = time.monotonic()
                self._poll_once(link)
                while self._running and (time.monotonic() - started) * 1000 < self.poll_ms:
                    self._do_writes(link)
                    time.sleep(0.005)
        except LinkLost as e:
            self.error_occurred.emit(f"Modbus link lost: {e}")
            self._close(link)
            self._finish(lost=True)
            return
        self._close(link)

    def _close(self, link) -> None:
        self._link = None
        try:
            link.close()
        except Exception:
            pass

    def _finish(self, lost: bool) -> None:
        self._running = False
        self.lost = lost
        self._disconnected_emitted = True
        self.disconnected.emit()

    def _poll_once(self, link) -> None:
        values: dict[str, float] = {}
        for block in self._blocks:
            if not self._running:
                return
            self._do_writes(link)
            request = read_request(block.table, block.start, block.count)
            what = f"unit {block.unit} {block.table} {block.start}"
            self._polls += 1
            try:
                got = decode_block(block, self._transact(link, block.unit, request))
            except (ModbusTimeout, ModbusFrameError, ModbusDeviceError) as e:
                self._failed += 1
                self._problem(what, e)
                continue
            self._recovered()
            values.update(got)
        if values:
            deliver(self, [line.encode() + b"\n" for line in values_to_lines(values)])
            self.values_read.emit(values)
        self._report()

    def _do_writes(self, link) -> None:
        while True:
            try:
                point, request = self._writes.get_nowait()
            except queue.Empty:
                return
            try:
                check_write(request, self._transact(link, point.unit, request))
            except (ModbusTimeout, ModbusFrameError, ModbusDeviceError) as e:
                self._emit_line(f"modbus: write {point.name} failed: {self._describe(e)}")

    def _transact(self, link, unit: int, pdu: bytes) -> bytes:
        """One exchange; with **Show frames** on, the raw bytes go to the terminal."""
        try:
            answer = link.transact(unit, pdu)
        finally:
            if self.show_frames:
                self._emit_line(f"modbus> {link.last_tx.hex(' ').upper()}")
                self._emit_line(f"modbus< {link.last_rx.hex(' ').upper() or '(nothing)'}")
        return answer

    def _emit_line(self, text: str) -> None:
        deliver(self, [text.encode() + b"\n"])

    @staticmethod
    def _describe(e: Exception) -> str:
        return "no response" if isinstance(e, ModbusTimeout) else str(e)

    def _problem(self, what: str, e: Exception) -> None:
        """Said once, not at every poll: a silent device mustn't flood the log."""
        message = f"{what}: {self._describe(e)}"
        if message != self._last_problem:
            self._last_problem = message
            self._emit_line(f"modbus: {message}")

    def _recovered(self) -> None:
        if self._last_problem:
            self._last_problem = ""
            self._emit_line("modbus: responding again")

    def _report(self) -> None:
        text = f"{self._polls:,} requests, {self._failed:,} failed"
        if self._last_problem:
            text += f"  ·  {self._last_problem}"
        self.status.emit(text)
