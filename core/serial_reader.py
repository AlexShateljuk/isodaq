"""
core/serial_reader.py — Non-blocking local serial reader.

Runs in its own QThread and emits Qt signals for each received line so the GUI
can update safely. The read loop, ``send()`` and connection-state handling live
in :class:`core.stream_reader.StreamReader`; this class only opens a local COM /
tty port via pyserial.

Performance notes:
  - Reads in chunks (``serial.read(in_waiting or 1)``) for max throughput
  - Splits on newline in Python — much faster than ``serial.readline()`` which
    blocks until timeout for every line
  - Timestamps each line immediately in the reader thread
"""
from __future__ import annotations

from core.stream_reader import StreamReader


class SerialReader(StreamReader):
    """Background local-serial reader. Configure with :meth:`configure`, then
    call :meth:`connect_port`; it emits ``line_received(line, timestamp)`` per
    line plus the connection-state signals from :class:`StreamReader`. All I/O
    stays off the GUI thread."""

    def __init__(self, parent=None):
        super().__init__(parent)
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

    # ── Transport hooks ───────────────────────────────────────────────────────

    def _open(self):
        import serial  # pyserial
        return serial.Serial(
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

    def _conn_label(self) -> tuple[str, int]:
        return (self._port, self._baud)

    @staticmethod
    def list_ports() -> list[str]:
        """Return available serial port names."""
        try:
            from serial.tools import list_ports
            return [p.device for p in list_ports.comports()]
        except Exception:
            return []
