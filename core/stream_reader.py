"""
core/stream_reader.py — shared base for byte-stream transports.

Holds everything that is identical across transports that expose a
``serial.Serial``-like object (``in_waiting`` / ``read`` / ``write`` / ``close``):
the Qt signals, the high-speed line-splitting reader loop, the partial-line
flush, ``send()`` and ``disconnect_port()``.

Subclasses implement only how the connection is opened:

  * :meth:`_open`        — return an open serial-like object (raises on failure)
  * :meth:`_conn_label`  — ``(label, baud)`` shown on the ``connected`` signal

Two open strategies are supported:

  * **synchronous** (local serial) — the default :meth:`connect_port` opens the
    port on the calling thread and returns an error string on failure, then
    starts the reader thread. This preserves the original ``SerialReader`` API.
  * **asynchronous** (network) — subclasses whose open can block on I/O (a TCP
    connect to a dead host) override :meth:`connect_port` to start the thread
    with ``self._serial = None``; :meth:`run` then opens in-thread and reports
    success/failure through the ``connected`` / ``error_occurred`` signals.
"""
from __future__ import annotations

import time
from datetime import datetime

from PyQt6.QtCore import QThread, pyqtSignal


class StreamReader(QThread):
    """Base reader thread for serial-like byte streams. Not used directly —
    see :class:`~core.serial_reader.SerialReader` and
    :class:`~core.net_reader.NetworkReader`."""

    # Signals emitted to the GUI thread
    line_received  = pyqtSignal(str, str)   # (line, timestamp)
    error_occurred = pyqtSignal(str)
    connected      = pyqtSignal(str, int)   # (label, baud) — baud 0 for network
    disconnected   = pyqtSignal()

    # How long (seconds) a partial line with no new data waits before being
    # flushed as its own line. Catches crash/reset lines that lack a trailing \n.
    PARTIAL_FLUSH_TIMEOUT = 0.25

    def __init__(self, parent=None):
        super().__init__(parent)
        self._serial = None
        self._running = False
        self._disconnected_emitted = False

    # ── To implement in subclasses ────────────────────────────────────────────

    def _open(self):
        """Open and return a serial-like object, or raise on failure."""
        raise NotImplementedError

    def _conn_label(self) -> tuple[str, int]:
        """Return ``(label, baud)`` for the ``connected`` signal."""
        raise NotImplementedError

    # ── Connect / disconnect ──────────────────────────────────────────────────

    def connect_port(self) -> str | None:
        """Open synchronously and start the reader thread.
        Returns an error string on failure, or ``None`` on success."""
        try:
            self._serial = self._open()
        except Exception as e:
            return str(e)
        self._disconnected_emitted = False   # reset per-connection (see B1)
        self._running = True
        self.start()
        label, baud = self._conn_label()
        self.connected.emit(label, baud)
        return None

    def disconnect_port(self):
        """Signal the thread to stop and close the connection."""
        self._running = False
        self.wait(2000)
        try:
            if self._serial is not None and getattr(self._serial, "is_open", False):
                self._serial.close()
        except Exception:
            pass
        if not self._disconnected_emitted:
            self.disconnected.emit()
        self._disconnected_emitted = False

    def send(self, data: bytes) -> str | None:
        """Send raw bytes. Returns an error string, or ``None`` on success."""
        if self._serial is None or not getattr(self._serial, "is_open", False):
            return "Not connected"
        try:
            self._serial.write(data)
            return None
        except Exception as e:
            return str(e)

    # ── Reader loop ───────────────────────────────────────────────────────────

    def run(self):
        # Asynchronous-open transports start the thread with no open connection;
        # open here so a blocking connect never freezes the GUI thread.
        if self._serial is None:
            try:
                self._serial = self._open()
            except Exception as e:
                self.error_occurred.emit(str(e))
                self._running = False
                self._disconnected_emitted = True
                self.disconnected.emit()
                return
            label, baud = self._conn_label()
            self.connected.emit(label, baud)

        self._read_loop()

    def _read_loop(self):
        """High-speed reader loop.

        Read whatever is in the OS buffer (``in_waiting``), accumulate into a
        bytearray, split on ``\\n``, emit complete lines. Faster than
        ``readline()`` at high baud. A partial line with no trailing ``\\n`` is
        flushed after ``PARTIAL_FLUSH_TIMEOUT`` so crash/reset output is never
        merged with the next reboot line.
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

        # ── Abnormal exit (I/O exception or remote close) ─────────────────────
        # Emit disconnected so the GUI resets, unless disconnect_port() already
        # handled it (user-initiated stop sets _running=False before joining).
        if self._running:
            self._running = False
            self._disconnected_emitted = True
            try:
                if self._serial is not None and getattr(self._serial, "is_open", False):
                    self._serial.close()
            except Exception:
                pass
            self.disconnected.emit()
