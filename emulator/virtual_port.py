"""
emulator/virtual_port.py — Windows-compatible virtual serial port

Uses a local TCP server so that pyserial's socket:// URL handler can connect
to it as if it were a real serial port.  No drivers or external tools needed.

Data flow:
    Emulator  →  VirtualPort.write()  →  TCP server socket
                                              ↓
    SerialReader  ←  serial_for_url("socket://127.0.0.1:PORT")  ←  TCP client
"""
from __future__ import annotations

import socket
import threading


class VirtualPort:
    """
    Wraps a loopback TCP server as a virtual serial port.

    Usage:
        vp = VirtualPort()
        url = vp.open()                   # start server, get pyserial URL
        # pass url to SerialReader.configure(port=url, ...)
        vp.wait_for_client()              # block until SerialReader connects
        vp.write(b"data\\n")              # emulator feeds data
        vp.close()

    Also works as a context manager:
        with VirtualPort() as (vp, url):
            ...
    """

    def __init__(self) -> None:
        self._server: socket.socket | None = None
        self._conn: socket.socket | None = None
        self._port: int = 0
        self._accept_thread: threading.Thread | None = None
        self._client_connected = threading.Event()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def open(self) -> str:
        """
        Bind a TCP server on a random free port and start waiting for a client.
        Returns the pyserial URL string ("socket://127.0.0.1:PORT") that
        SerialReader should use as its port name.
        """
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))   # OS picks a free port
        self._server.listen(1)
        self._port = self._server.getsockname()[1]

        self._accept_thread = threading.Thread(
            target=self._accept_loop, daemon=True, name="VirtualPort-accept"
        )
        self._accept_thread.start()

        return f"socket://127.0.0.1:{self._port}"

    def wait_for_client(self, timeout: float = 5.0) -> bool:
        """
        Block until SerialReader (the TCP client) connects.
        Returns True if connected within timeout, False otherwise.
        """
        return self._client_connected.wait(timeout)

    def close(self) -> None:
        """Close both sides of the connection."""
        if self._conn:
            try:
                self._conn.close()
            except OSError:
                pass
            self._conn = None

        if self._server:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None

        self._client_connected.clear()

    # ── Data path ─────────────────────────────────────────────────────────────

    def write(self, data: bytes) -> None:
        """
        Send raw bytes to the connected client (SerialReader).
        Silently drops data if no client is connected yet.
        """
        if self._conn is None:
            return
        try:
            self._conn.sendall(data)
        except OSError:
            pass   # client disconnected — caller decides what to do

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def is_open(self) -> bool:
        return self._server is not None

    @property
    def tcp_port(self) -> int:
        return self._port

    # ── Context manager ───────────────────────────────────────────────────────

    def __enter__(self) -> tuple[VirtualPort, str]:
        url = self.open()
        return self, url

    def __exit__(self, *_) -> None:
        self.close()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _accept_loop(self) -> None:
        """Wait for exactly one client connection, then signal readiness."""
        try:
            self._conn, addr = self._server.accept()
            self._client_connected.set()
        except OSError:
            pass   # server was closed before any client arrived
