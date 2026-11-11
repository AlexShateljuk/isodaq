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

import threading
import time
from datetime import datetime

from PyQt6.QtCore import QThread, pyqtSignal


class LineSplitter:
    """Turns a byte stream into lines: one split per chunk, partial-line flush
    after silence, and a hard cap on line length (shared by every reader).

    Lines are returned *with* their ``\n`` terminator, so every received byte
    is accounted for (the HEX view shows the stream exactly)."""

    def __init__(self, max_line: int):
        self.buf = bytearray()
        self.buf_ts = 0.0          # monotonic time the last byte was appended
        self.max_line = max_line

    def feed(self, chunk: bytes) -> list[bytes]:
        out: list[bytes] = []
        if not chunk:
            return out
        self.buf.extend(chunk)
        self.buf_ts = time.monotonic()
        if b"\n" in chunk:
            *out, rest = self.buf.split(b"\n")
            out = [line + b"\n" for line in out]
            self.buf = bytearray(rest)
        # Binary / flash dumps can run megabytes without a newline: emit an
        # over-long run in pieces instead of growing one giant "line".
        while len(self.buf) >= self.max_line:
            out.append(bytes(self.buf[:self.max_line]))
            del self.buf[:self.max_line]
        return out

    def flush_if_idle(self, timeout: float) -> list[bytes]:
        """Return a trailing partial line once no byte arrived for *timeout*
        (crash/reset output often lacks a final newline)."""
        if self.buf and self.buf_ts > 0 and time.monotonic() - self.buf_ts >= timeout:
            raw, self.buf, self.buf_ts = bytes(self.buf), bytearray(), 0.0
            return [raw]
        return []


#: C0 control characters (except TAB) and DEL → "·". Binary / flash dumps are
#: full of them: NUL made the CSV log unreadable ("line contains NUL"), and the
#: invisible glyphs roughly doubled the terminal's text-layout cost.
_CONTROL_TO_DOT = {i: "·" for i in (*range(0, 9), *range(11, 32), 0x7F)}


def deliver(reader, raw_lines: list[bytes]) -> None:
    """Decode + timestamp *raw_lines* and hand them to the GUI as one batch of
    ``(text, timestamp, arrival, raw_bytes)``.

    One cross-thread signal per read instead of per line: at ~2k lines/s per
    port the per-line queued signal was itself a large part of the GUI cost.
    ``line_received`` (per line) is still emitted for anything connected to it.

    Lines with no text (blank / bare CR-LF) are not passed on as lines, but
    their bytes are carried into the next line's ``raw_bytes`` so the HEX view
    never loses a byte.
    """
    if not raw_lines:
        return
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    mono = time.monotonic()   # arrival time — lets the GUI measure its lag
    carry = getattr(reader, "_raw_carry", b"")
    batch = []
    for raw in raw_lines:
        text = raw.replace(b"\r", b"").rstrip(b"\n").decode("utf-8", errors="replace")
        if not text:
            carry += raw
            continue
        if not text.isprintable():
            text = text.translate(_CONTROL_TO_DOT)
        batch.append((text, ts, mono, carry + raw if carry else raw))
        carry = b""
    reader._raw_carry = carry
    if not batch:
        return
    reader.lines_received.emit(batch)
    if reader.receivers(reader.line_received) > 0:
        for text, t, *_ in batch:
            reader.line_received.emit(text, t)


def add_tap(reader, tap) -> None:
    """Have *tap* called with the raw bytes. A new tuple each time, because the reader
    thread is going through the old one."""
    reader.taps = (*reader.taps, tap)


def remove_tap(reader, tap) -> None:
    if hasattr(reader, "taps"):
        reader.taps = tuple(t for t in reader.taps if t != tap)


def receive(reader, splitter: LineSplitter, chunk: bytes) -> None:
    """Pass on bytes from the device: as lines, or through the connection's binary
    frame decoder if it has one (see core.binary_frames). Whatever isn't a frame
    still goes out as lines, so a boot log stays readable."""
    decoder = reader.frame_decoder
    if decoder is None:
        deliver(reader, splitter.feed(chunk))
        return
    batch: list = []

    def send_batch() -> None:
        if batch:
            reader.lines_received.emit(list(batch))
            if reader.receivers(reader.line_received) > 0:
                for text, t, *_ in batch:
                    reader.line_received.emit(text, t)
            batch.clear()

    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    mono = time.monotonic()
    for item in decoder.feed(chunk):
        if isinstance(item, bytes):
            send_batch()
            deliver(reader, splitter.feed(item))
            continue
        lines, raw = item
        for line in lines:
            batch.append((line, ts, mono, raw))
            raw = b""
    send_batch()


class StreamReader(QThread):
    """Base reader thread for serial-like byte streams. Not used directly —
    see :class:`~core.serial_reader.SerialReader` and
    :class:`~core.net_reader.NetworkReader`."""

    # Signals emitted to the GUI thread
    line_received  = pyqtSignal(str, str)   # (line, timestamp)
    lines_received = pyqtSignal(object)     # [(line, ts, arrival, raw bytes), …] per read
    error_occurred = pyqtSignal(str)
    connected      = pyqtSignal(str, int)   # (label, baud) — baud 0 for network
    disconnected   = pyqtSignal()

    # How long (seconds) a partial line with no new data waits before being
    # flushed as its own line. Catches crash/reset lines that lack a trailing \n.
    PARTIAL_FLUSH_TIMEOUT = 0.25
    #: A "line" longer than this is emitted in pieces. Binary / flash dumps can
    #: stream megabytes with no newline; unbounded, that one line grew without
    #: limit and was pushed into the terminal in one go.
    MAX_LINE_BYTES = 4096
    #: Bytes from the device can be passed on as they are (see core.port_share).
    shareable = True

    def __init__(self, parent=None):
        super().__init__(parent)
        self._serial = None
        self._running = False
        self._disconnected_emitted = False
        #: True when the last session ended because the link dropped (I/O error
        #: or remote close) rather than a user disconnect — drives auto-reconnect.
        self.lost = False
        #: Modem-control lines applied on open and kept while connected (CMP2).
        #: pyserial asserts both by default; ``None`` leaves the driver default.
        self.dtr: bool | None = None
        self.rts: bool | None = None
        # File transfers need the port to themselves (see acquire_port).
        self._hold = threading.Event()
        self._held = threading.Event()
        #: Called with each chunk read from the device, on the reader thread.
        self.taps: tuple = ()
        #: Turns the bytes into channels instead of lines (see core.binary_frames).
        self.frame_decoder = None

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
        self.lost = False
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
        if self._hold.is_set():
            return "A file transfer is using this connection"
        if self._serial is None or not getattr(self._serial, "is_open", False):
            return "Not connected"
        try:
            self._serial.write(data)
            return None
        except Exception as e:
            return str(e)

    # ── Exclusive use of the port (file transfers) ────────────────────────────

    def acquire_port(self, timeout: float = 2.0) -> bool:
        """Park the read loop so another thread can talk to the port directly.
        Whatever arrives until :meth:`release_port` isn't shown or logged."""
        self._hold.set()
        if self.isRunning() and self._held.wait(timeout):
            return True
        self.release_port()
        return False

    def release_port(self) -> None:
        self._held.clear()
        self._hold.clear()

    def port_read(self, n: int) -> bytes:
        return self._serial.read(n)

    def port_write(self, data: bytes) -> None:
        self._serial.write(data)

    # ── Modem-control lines (CMP2) ───────────────────────────────────────────

    def set_lines(self, dtr: bool | None = None, rts: bool | None = None) -> str | None:
        """Set DTR and/or RTS (remembered for the next open too). Returns an
        error string if the open transport can't do it, else ``None``."""
        if dtr is not None:
            self.dtr = dtr
        if rts is not None:
            self.rts = rts
        ser = self._serial
        if ser is None or not getattr(ser, "is_open", False):
            return None
        try:
            if dtr is not None:
                ser.dtr = dtr
            if rts is not None:
                ser.rts = rts
                # Windows usbser drivers only apply RTS together with DTR
                # (same workaround as esptool).
                if self.dtr is not None:
                    ser.dtr = self.dtr
        except Exception as e:
            return str(e) or "DTR/RTS not supported by this transport"
        return None

    def set_break(self, on: bool) -> str | None:
        """Hold (``True``) or release the BREAK condition. The caller times the
        pulse (a QTimer), so the GUI thread never blocks in ``send_break``."""
        ser = self._serial
        if ser is None or not getattr(ser, "is_open", False):
            return "Not connected"
        try:
            ser.break_condition = on
        except Exception as e:
            return str(e) or "BREAK not supported by this transport"
        return None

    def _apply_lines(self, ser) -> None:
        """Apply the remembered DTR/RTS to a freshly opened port."""
        try:
            if self.dtr is not None:
                ser.dtr = self.dtr
            if self.rts is not None:
                ser.rts = self.rts
        except Exception:
            pass

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
        lines = LineSplitter(self.MAX_LINE_BYTES)

        while self._running:
            if self._hold.is_set():
                self._held.set()
                time.sleep(0.005)
                continue
            try:
                waiting = self._serial.in_waiting
                chunk = self._serial.read(waiting if waiting > 0 else 1)
            except Exception as e:
                self.error_occurred.emit(str(e))
                break

            if chunk:
                for tap in self.taps:
                    tap(chunk)
            receive(self, lines, chunk)
            deliver(self, lines.flush_if_idle(self.PARTIAL_FLUSH_TIMEOUT))

        # ── Abnormal exit (I/O exception or remote close) ─────────────────────
        # Emit disconnected so the GUI resets, unless disconnect_port() already
        # handled it (user-initiated stop sets _running=False before joining).
        if self._running:
            self._running = False
            self.lost = True
            self._disconnected_emitted = True
            try:
                if self._serial is not None and getattr(self._serial, "is_open", False):
                    self._serial.close()
            except Exception:
                pass
            self.disconnected.emit()
