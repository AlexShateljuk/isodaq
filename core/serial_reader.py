"""
core/serial_reader.py — Non-blocking serial reader

Runs in its own QThread.
Emits Qt signals for each received line so the GUI can update safely.

Performance notes:
  - Reads in chunks (serial.read(serial.in_waiting or 1)) for max throughput
  - Splits on newline in Python — much faster than serial.readline() which
    blocks until timeout for every line
  - Timestamps each line immediately in the reader thread
  - Puts raw bytes → logger queue without waiting for GUI
"""
from __future__ import annotations

import time
from datetime import datetime

from PyQt6.QtCore import QThread, pyqtSignal


class SerialReader(QThread):
    """Background serial reader. Configure with :meth:`configure`, then start the
    thread; it emits ``line_received(line, timestamp)`` per line and the
    connection-state signals below. All I/O stays off the GUI thread."""

    # Signals emitted to GUI thread
    line_received = pyqtSignal(str, str)   # (line, timestamp)
    error_occurred = pyqtSignal(str)
    connected = pyqtSignal(str, int)       # (port, baud)
    disconnected = pyqtSignal()

    # How long (seconds) a partial line with no new data waits before being
    # flushed as its own line.  Catches crash/reset lines that lack a trailing \n.
    PARTIAL_FLUSH_TIMEOUT = 0.25

    def __init__(self, parent=None):
        super().__init__(parent)
        self._serial = None
        self._running = False
        self._disconnected_emitted = False
        self._port = ""
        self._baud = 115200
        self._bytesize = 8
        self._parity = "N"
        self._stopbits = 1
        self._flow_rtscts = False
        self._flow_xonxoff = False

    # ── Configuration (call before connect) ───────────────────────────────────

    def configure(
        self,
        port: str,
        baud: int,
        bytesize: int = 8,
        parity: str = "N",
        stopbits: int = 1,
        flow: str = "None",
    ):
        self._port = port
        self._baud = baud
        self._bytesize = bytesize
        self._parity = parity
        self._stopbits = stopbits
        self._flow_rtscts = flow == "RTS/CTS"
        self._flow_xonxoff = flow == "XON/XOFF"

    # ── Connect / disconnect ──────────────────────────────────────────────────

    def connect_port(self) -> str | None:
        """
        Open the serial port and start the reader thread.
        Returns error string on failure, None on success.
        """
        try:
            import serial  # pyserial
            self._serial = serial.Serial(
                port=self._port,
                baudrate=self._baud,
                bytesize=self._bytesize,
                parity=self._parity,
                stopbits=self._stopbits,
                rtscts=self._flow_rtscts,
                xonxoff=self._flow_xonxoff,
                timeout=0.05,        # non-blocking read timeout
                write_timeout=1.0,
            )
            self._disconnected_emitted = False   # reset per-connection (see B1)
            self._running = True
            self.start()
            self.connected.emit(self._port, self._baud)
            return None
        except Exception as e:
            return str(e)

    def disconnect_port(self):
        """Signal the thread to stop and close the port."""
        self._running = False
        self.wait(2000)
        if self._serial and self._serial.is_open:
            self._serial.close()
        if not self._disconnected_emitted:
            self.disconnected.emit()
        self._disconnected_emitted = False

    def send(self, data: bytes) -> str | None:
        """Send raw bytes. Returns error string or None."""
        if not self._serial or not self._serial.is_open:
            return "Not connected"
        try:
            self._serial.write(data)
            return None
        except Exception as e:
            return str(e)

    @staticmethod
    def list_ports() -> list[str]:
        """Return available serial port names."""
        try:
            from serial.tools import list_ports
            return [p.device for p in list_ports.comports()]
        except Exception:
            return []

    # ── Reader loop ───────────────────────────────────────────────────────────

    def run(self):
        """
        High-speed reader loop.

        Strategy: read whatever is in the OS buffer (serial.in_waiting),
        accumulate into a bytearray, split on \\n, emit complete lines.
        This is significantly faster than readline() at high baud rates.

        Partial-line flush: if data sits in the buffer without a trailing \\n
        for longer than PARTIAL_FLUSH_TIMEOUT seconds (e.g. crash/reset output),
        it is emitted as-is so it is never merged with the next reboot line.
        """
        buf = bytearray()
        buf_ts: float = 0.0   # monotonic time when last byte was appended

        while self._running:
            try:
                waiting = self._serial.in_waiting
                chunk = self._serial.read(waiting if waiting > 0 else 1)
            except Exception as e:
                self.error_occurred.emit(str(e))
                break

            if chunk:
                buf.extend(chunk)
                buf_ts = time.monotonic()

            # Emit all complete lines
            while b"\n" in buf:
                idx = buf.index(b"\n")
                raw = buf[:idx].replace(b"\r", b"").decode("utf-8", errors="replace")
                buf = buf[idx + 1:]
                if raw:
                    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    self.line_received.emit(raw, ts)

            # Flush partial line after silence (handles crash/brownout output)
            if buf and buf_ts > 0 and (time.monotonic() - buf_ts) >= self.PARTIAL_FLUSH_TIMEOUT:
                raw = buf.replace(b"\r", b"").decode("utf-8", errors="replace").strip()
                buf = bytearray()
                buf_ts = 0.0
                if raw:
                    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    self.line_received.emit(raw, ts)

        # ── Abnormal exit (serial exception) ──────────────────────────────────
        # Emit disconnected so the GUI resets, unless disconnect_port() already
        # handled it (user-initiated stop sets _running=False before joining).
        if self._running:
            self._running = False
            self._disconnected_emitted = True
            try:
                if self._serial and self._serial.is_open:
                    self._serial.close()
            except Exception:
                pass
            self.disconnected.emit()
