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

import os

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
        ser = serial.Serial(
            port=None,           # open below, after DTR/RTS are set (CMP2)
            baudrate=self._baud,
            bytesize=self._bytesize,
            parity=self._parity,
            stopbits=self._stopbits,
            rtscts=self._flow_rtscts,
            xonxoff=self._flow_xonxoff,
            timeout=0.05,        # non-blocking read timeout
            write_timeout=1.0,
        )
        ser.port = self._port
        # Set before open() so the lines never glitch: an ESP32's auto-reset
        # circuit would otherwise reset (or hold) the board on connect.
        self._apply_lines(ser)
        ser.open()
        return ser

    def _conn_label(self) -> tuple[str, int]:
        return (self._port, self._baud)

    @staticmethod
    def list_ports() -> list[str]:
        """Return available serial port names."""
        return [p["device"] for p in SerialReader.port_info()]

    @staticmethod
    def port_info() -> list[dict]:
        """Available ports with what the OS knows about them (CMP4):
        ``device``, ``description`` (e.g. "CP2102 USB to UART Bridge"; empty if
        nothing useful), ``vid``/``pid``/``serial`` (USB identity, ``None`` for
        non-USB ports) — used to label the port list and to find a re-plugged
        board again under a new name (auto-reconnect)."""
        try:
            from serial.tools import list_ports
            ports = list_ports.comports()
        except Exception:
            return []
        out = []
        for p in ports:
            desc = (p.description or "").strip()
            if desc in ("n/a", p.device, p.name):
                desc = ""
            if not desc and (p.manufacturer or p.product):
                desc = " ".join(x for x in (p.manufacturer, p.product) if x)
            out.append({"device": p.device, "description": desc,
                        "vid": p.vid, "pid": p.pid, "serial": p.serial_number})
        return out

    @staticmethod
    def find_port(port: str, usb_id: tuple | None) -> str | None:
        """Where is the board that was on *port* now? The same name if it is
        present, else a port with the same USB VID:PID + serial number (Linux /
        Windows may re-number a re-plugged board). ``None`` if not plugged in."""
        info = SerialReader.port_info()
        if any(p["device"] == port for p in info):
            return port
        if usb_id and usb_id[0] is not None and usb_id[2]:
            for p in info:
                if (p["vid"], p["pid"], p["serial"]) == usb_id:
                    return p["device"]
        # Paths the scan doesn't list: udev links (/dev/serial/by-id/…), ptys.
        if port.startswith("/") and os.path.exists(port):
            return port
        return None

    @staticmethod
    def usb_id(port: str) -> tuple | None:
        """``(vid, pid, serial)`` of *port*, or ``None`` if it isn't USB."""
        for p in SerialReader.port_info():
            if p["device"] == port and p["vid"] is not None:
                return (p["vid"], p["pid"], p["serial"])
        return None
