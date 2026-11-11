"""
core/agent_reader.py — the "Agent" transport (AGT2): a serial port on another
machine running ``isodaq-agent`` (a Raspberry Pi by the boards).

The read loop, ``send()``, DTR/RTS/BREAK, file transfers and auto-reconnect come
from :class:`core.stream_reader.StreamReader`; :class:`core.agent_client.AgentSerial`
stands in for the pyserial object. Like the Network transport, the connection is
opened on the reader thread, because reaching a host can take seconds.
"""
from __future__ import annotations

from core.agent_client import AgentError, AgentSerial, TrustStore, parse_url
from core.stream_reader import StreamReader

_FLOWS = {"RTS/CTS": "rtscts", "XON/XOFF": "xonxoff"}


class AgentReader(StreamReader):
    """Reader for ``agent://host[:port]/device`` targets."""

    def __init__(self, parent=None, store: TrustStore | None = None):
        super().__init__(parent)
        self.store = store or TrustStore()
        self._url = ""
        self._target: tuple[str, int, str] | None = None
        self._settings: dict = {}
        #: Why the last open failed, when retrying won't help (not paired, the
        #: agent's certificate changed, no such port): auto-reconnect gives up.
        self.fatal = ""

    def configure(self, url: str, baud: int = 115200, bytesize: int = 8,
                  parity: str = "N", stopbits: int = 1, flow: str = "None") -> None:
        """Raises ``ValueError`` for a target that isn't ``agent://host/device``."""
        self._target = parse_url(url)
        self._url = url.strip()
        self._settings = {"baud": baud, "bytesize": bytesize, "parity": parity,
                          "stopbits": stopbits, "flow": _FLOWS.get(flow, "none")}

    def connect_port(self) -> str | None:
        if self._target is None:
            return "No agent target set"
        self.fatal = ""
        self._serial = None            # run() opens in-thread
        self._disconnected_emitted = False
        self.lost = False
        self._running = True
        self.start()
        return None

    def _open(self):
        host, port, device = self._target
        settings = dict(self._settings)
        if self.dtr is not None:
            settings["dtr"] = self.dtr
        if self.rts is not None:
            settings["rts"] = self.rts
        try:
            return AgentSerial(host, port, device, self.store, settings)
        except AgentError as e:
            if e.fatal:
                self.fatal = str(e)
            raise

    def _conn_label(self) -> tuple[str, int]:
        ser = self._serial
        host, _, device = self._target
        name = getattr(ser, "agent", "") or host
        return (f"{name}: {device}", self._settings.get("baud", 0))
