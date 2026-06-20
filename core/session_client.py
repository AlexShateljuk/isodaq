"""
core/session_client.py — TCP session client with latency monitoring,
and HTTP relay client (poll-based, works through NAT).

TCP protocol (line-delimited JSON, UTF-8):
  Server → Client:  {"t": float, "d": str, "k": "rx"|"tx"|"sys"}
                    {"pong": float}          — echo of our ping timestamp
  Client → Server:  {"ping": float}         — every PING_INTERVAL seconds

Relay protocol:
  Client → Relay:  GET /tunnel/{code}/poll?timeout=20  →  {"messages":[...]}
"""
from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request

from PyQt6.QtCore import QThread, pyqtSignal

PING_INTERVAL = 2.0   # seconds between TCP pings
PING_TIMEOUT  = 6.0   # seconds before TCP connection considered lost


class SessionClient(QThread):
    line_received   = pyqtSignal(str, float, str)  # (line, timestamp, kind)
    connected       = pyqtSignal(str)              # "host:port" or "relay/host"
    disconnected    = pyqtSignal()
    error           = pyqtSignal(str)
    latency_updated = pyqtSignal(int)              # round-trip ms; -1 = timeout

    def __init__(self, host: str = "", port: int = 0,
                 relay_url: str = "", parent=None):
        super().__init__(parent)
        self._host      = host
        self._port      = port
        self._relay_url = relay_url   # full poll URL, e.g. https://…/tunnel/481203/poll
        self._running   = False
        self._sock:    socket.socket | None = None

    # ── Dispatch ───────────────────────────────────────────────────────────────

    def run(self) -> None:
        if self._relay_url:
            self._run_relay()
        else:
            self._run_tcp()

    def stop(self) -> None:
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
        self.quit()

    # ── TCP mode ───────────────────────────────────────────────────────────────

    def _run_tcp(self) -> None:
        self._running = True
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(6)
            self._sock.connect((self._host, self._port))
            self._sock.settimeout(None)
            self.connected.emit(f"{self._host}:{self._port}")
        except ConnectionRefusedError as e:
            self.error.emit(
                f"Cannot connect to {self._host}:{self._port} — {e}\n"
                "  The host may be behind a router/firewall.\n"
                "  Fix: forward port 9876 on the host's router, use a VPN,\n"
                "  or switch to relay sharing (Join → By code)."
            )
            self._running = False
            return
        except Exception as e:
            self.error.emit(f"Cannot connect to {self._host}:{self._port} — {e}")
            self._running = False
            return

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

        if self._running:
            self.latency_updated.emit(-1)

    # ── Relay mode (HTTP long-poll, works through NAT) ─────────────────────────

    def _run_relay(self) -> None:
        self._running = True
        display = self._relay_url.split("://")[-1].split("/")[0]
        self.connected.emit(f"relay/{display}")

        while self._running:
            try:
                url = f"{self._relay_url}?timeout=20"
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=25) as r:
                    resp = json.loads(r.read())

                for msg in resp.get("messages", []):
                    if not self._running:
                        break
                    if "d" in msg:
                        self.line_received.emit(
                            str(msg["d"]),
                            float(msg.get("t", 0)),
                            str(msg.get("k", "rx")),
                        )

            except urllib.error.HTTPError as e:
                if e.code == 404:
                    self.error.emit("Relay: session not found or expired")
                    break
                # Other HTTP errors — brief backoff then retry
                if self._running:
                    time.sleep(2)
            except Exception:
                if self._running:
                    time.sleep(2)

        self._running = False
        self.disconnected.emit()
