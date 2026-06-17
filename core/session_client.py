"""
core/session_client.py — TCP session client with latency monitoring.

Protocol (line-delimited JSON, UTF-8):
  Server → Client:  {"t": float, "d": str, "k": "rx"|"sys"}
                    {"pong": float}          — echo of our ping timestamp
  Client → Server:  {"ping": float}         — every PING_INTERVAL seconds
"""
from __future__ import annotations

import json
import socket
import threading
import time

from PyQt6.QtCore import QThread, pyqtSignal

PING_INTERVAL = 2.0    # seconds between pings
PING_TIMEOUT  = 6.0    # seconds before connection considered lost


class SessionClient(QThread):
    line_received   = pyqtSignal(str, float, str)  # (line, timestamp, kind)
    connected       = pyqtSignal(str)              # "host:port"
    disconnected    = pyqtSignal()
    error           = pyqtSignal(str)
    latency_updated = pyqtSignal(int)              # round-trip ms; -1 = timeout

    def __init__(self, host: str, port: int, parent=None):
        super().__init__(parent)
        self._host    = host
        self._port    = port
        self._sock: socket.socket | None = None
        self._running = False

    def run(self) -> None:
        self._running = True
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(6)
            self._sock.connect((self._host, self._port))
            self._sock.settimeout(None)
            self.connected.emit(f"{self._host}:{self._port}")
        except Exception as e:
            self.error.emit(f"Cannot connect to {self._host}:{self._port} — {e}")
            self._running = False
            return

        # Start background ping thread
        threading.Thread(target=self._ping_loop, daemon=True).start()

        buf = ""
        try:
            while self._running:
                chunk = self._sock.recv(4096)
                if not chunk:
                    break
                buf += chunk.decode(errors="replace")
                while "\n" in buf:
                    raw, buf = buf.split("\n", 1)
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        obj = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    if "pong" in obj:
                        rtt_ms = int((time.time() - float(obj["pong"])) * 1000)
                        self.latency_updated.emit(rtt_ms)
                    elif "d" in obj:
                        self.line_received.emit(
                            obj["d"],
                            float(obj.get("t", 0)),
                            obj.get("k", "rx"),
                        )
        except Exception:
            pass
        finally:
            self._running = False
            try:
                self._sock.close()
            except Exception:
                pass
            self.disconnected.emit()

    def stop(self) -> None:
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
        self.quit()

    # ── Ping loop (background thread) ─────────────────────────────────────────

    def _ping_loop(self) -> None:
        last_ping = 0.0
        while self._running and self._sock:
            now = time.time()
            if now - last_ping >= PING_INTERVAL:
                try:
                    self._sock.sendall(
                        (json.dumps({"ping": now}) + "\n").encode()
                    )
                    last_ping = now
                except Exception:
                    break
            time.sleep(0.2)

        # If we exit without the connection being deliberately stopped, signal timeout
        if self._running:
            self.latency_updated.emit(-1)
