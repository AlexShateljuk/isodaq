"""
core/session_server.py — TCP session server.

Accepts connections from remote IsoDAQ instances and streams every incoming
serial line to all connected clients in real time.

Protocol (line-delimited JSON, UTF-8):
  Server → Client:  {"t": <float>, "d": "<line>", "k": "rx"|"sys"}\n
  Client → Server:  {"tx": "<command>"}\n   (future: TX permission)
"""
from __future__ import annotations

import json
import socket
import threading
import time
from typing import Callable, Optional

from PyQt6.QtCore import QObject, pyqtSignal


class SessionServer(QObject):
    """
    Runs a TCP server on a background thread.
    Call feed_line() from the GUI thread whenever a serial line arrives —
    it will be broadcast to all connected clients.
    """

    client_connected    = pyqtSignal(str)   # remote address string
    client_disconnected = pyqtSignal(str)
    error               = pyqtSignal(str)

    DEFAULT_PORT = 9876

    def __init__(self, port: int = DEFAULT_PORT, parent=None):
        super().__init__(parent)
        self.port = port
        self._sock: Optional[socket.socket] = None
        self._clients: list[socket.socket] = []
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    # ── Public API ─────────────────────────────────────────────────────────

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
        with self._lock:
            for c in list(self._clients):
                try:
                    c.close()
                except Exception:
                    pass
            self._clients.clear()

    def feed_line(self, line: str, kind: str = "rx") -> None:
        """Broadcast one serial line to all connected clients."""
        if not self._clients:
            return
        payload = (json.dumps({"t": time.time(), "d": line, "k": kind}) + "\n").encode()
        with self._lock:
            dead = []
            for c in self._clients:
                try:
                    c.sendall(payload)
                except Exception:
                    dead.append(c)
            for c in dead:
                self._clients.remove(c)

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)

    # ── Internal ───────────────────────────────────────────────────────────

    def _serve(self) -> None:
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(("0.0.0.0", self.port))
            self._sock.listen(8)
            self._sock.settimeout(1.0)
        except Exception as e:
            self.error.emit(str(e))
            return

        while self._running:
            try:
                conn, addr = self._sock.accept()
                addr_str = f"{addr[0]}:{addr[1]}"
                with self._lock:
                    self._clients.append(conn)
                self.client_connected.emit(addr_str)
                threading.Thread(
                    target=self._handle_client,
                    args=(conn, addr_str),
                    daemon=True,
                ).start()
            except socket.timeout:
                continue
            except Exception:
                break

    def _handle_client(self, conn: socket.socket, addr: str) -> None:
        """Read loop — handles ping/pong and future TX commands."""
        buf = b""
        try:
            while self._running:
                data = conn.recv(1024)
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        obj = json.loads(raw)
                        if "ping" in obj:
                            conn.sendall(
                                (json.dumps({"pong": obj["ping"]}) + "\n").encode()
                            )
                    except (json.JSONDecodeError, OSError):
                        pass
        except Exception:
            pass
        finally:
            with self._lock:
                if conn in self._clients:
                    self._clients.remove(conn)
            try:
                conn.close()
            except Exception:
                pass
            self.client_disconnected.emit(addr)
