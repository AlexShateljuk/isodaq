"""
core/net_reader.py — network transport (raw TCP + RFC2217).

Attaches the terminal to a networked byte stream instead of a local port, using
pyserial's URL handlers (no extra dependency):

  * ``socket://host:port``  — raw TCP. Covers telnet-style log streams (ESP32
    over WiFi), ``ser2net``/``socat`` bridges, ``nc``. No IAC negotiation.
  * ``rfc2217://host:port`` — the network-serial standard (baud/line control
    negotiated over the link): Moxa NPort, Digi, Lantronix, ser2net RFC2217.
  * ``loop://``             — in-memory loopback, used by the tests/demo.

The read loop, ``send()`` and state handling are inherited from
:class:`core.stream_reader.StreamReader`. The connection is opened **in-thread**
(via the async :meth:`connect_port`) because a TCP connect to a dead host can
block for seconds — that must never freeze the GUI thread.
"""
from __future__ import annotations

from core.stream_reader import StreamReader


class NetworkReader(StreamReader):
    """Reader for ``socket://`` / ``rfc2217://`` / ``loop://`` targets."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._url = ""
        self._baud = 115200
        self._bytesize = 8
        self._parity = "N"
        self._stopbits = 1
        self._flow_rtscts = False
        self._flow_xonxoff = False

    # ── Configuration (call before connect) ───────────────────────────────────

    def configure(
        self,
        url: str,
        baud: int = 115200,
        bytesize: int = 8,
        parity: str = "N",
        stopbits: int = 1,
        flow: str = "None",
    ):
        url = (url or "").strip()
        # Bare host:port is a convenience for raw TCP.
        if url and "://" not in url:
            url = f"socket://{url}"
        self._url = url
        self._baud = baud
        self._bytesize = bytesize
        self._parity = parity
        self._stopbits = stopbits
        self._flow_rtscts = flow == "RTS/CTS"
        self._flow_xonxoff = flow == "XON/XOFF"

    # ── Async open (connect can block on a dead host) ─────────────────────────

    def connect_port(self) -> str | None:
        if not self._url:
            return "No network target set"
        self._serial = None            # run() opens in-thread
        self._disconnected_emitted = False
        self._running = True
        self.start()
        return None

    # ── Transport hooks ───────────────────────────────────────────────────────

    def _open(self):
        import serial  # pyserial
        return serial.serial_for_url(
            self._url,
            baudrate=self._baud,
            bytesize=self._bytesize,
            parity=self._parity,
            stopbits=self._stopbits,
            rtscts=self._flow_rtscts,
            xonxoff=self._flow_xonxoff,
            timeout=0.05,
            write_timeout=1.0,
        )

    def _conn_label(self) -> tuple[str, int]:
        return (self._url, 0)   # baud is meaningless for a raw socket
