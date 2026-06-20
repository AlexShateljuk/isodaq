#!/usr/bin/env python3
"""
IsoDAQ Signaling + Relay Server

Endpoints:
  POST /register               {"ip":"1.2.3.4","port":9876}  →  {"code":"481203"}
  GET  /lookup/481203          →  {"ip":"1.2.3.4","port":9876}  or 404
  POST /tunnel/481203/push     body: {"messages":[...]}  →  {"ok":true}
  GET  /tunnel/481203/poll     →  {"messages":[...]}  (long-poll, up to ?timeout=25 s)
  GET  /health                 →  {"status":"ok","sessions":N}

Sessions and tunnels expire after 1 hour.
"""
from __future__ import annotations

import json
import os
import queue
import random
import string
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_sessions: dict[str, dict] = {}
_lock     = threading.Lock()
_TTL      = 3600   # seconds


# ── Tunnel session ─────────────────────────────────────────────────────────────

class _TunnelSession:
    """Per-session message queue shared by one sharer (push) and any viewers (poll)."""

    def __init__(self) -> None:
        self._q:     queue.Queue = queue.Queue(maxsize=10_000)
        self._event: threading.Event = threading.Event()
        self.expires: float = time.time() + _TTL

    def push(self, messages: list[dict]) -> None:
        for msg in messages:
            try:
                self._q.put_nowait(msg)
            except queue.Full:
                pass
        self._event.set()

    def poll(self, timeout: float = 20.0) -> list[dict]:
        """Block until data arrives or timeout; return list of message dicts."""
        self._event.wait(timeout=timeout)
        self._event.clear()
        out: list[dict] = []
        while not self._q.empty():
            try:
                out.append(self._q.get_nowait())
            except queue.Empty:
                break
        return out


_tunnels: dict[str, _TunnelSession] = {}


def _clean() -> None:
    now = time.time()
    with _lock:
        expired = [k for k, v in _sessions.items() if v["expires"] < now]
        for k in expired:
            del _sessions[k]
            _tunnels.pop(k, None)


def _gen_code() -> str:
    while True:
        code = "".join(random.choices(string.digits, k=6))
        if code not in _sessions:
            return code


# ── HTTP handler ───────────────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt: str, *args) -> None:   # silence default access log
        pass

    def _send_json(self, status: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    # ── POST ──────────────────────────────────────────────────────────────────

    def do_POST(self) -> None:
        parts = self.path.split("?")[0].strip("/").split("/")

        # POST /register
        if self.path == "/register":
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length))
                ip   = str(body["ip"])
                port = int(body["port"])
            except Exception:
                self._send_json(400, {"error": "Bad request — expected {ip, port}"})
                return

            _clean()
            with _lock:
                code = _gen_code()
                _sessions[code] = {"ip": ip, "port": port, "expires": time.time() + _TTL}
                _tunnels[code]   = _TunnelSession()

            print(f"[register] code={code}  {ip}:{port}", flush=True)
            self._send_json(200, {"code": code})

        # POST /tunnel/{code}/push
        elif len(parts) == 3 and parts[0] == "tunnel" and parts[2] == "push":
            code = parts[1]
            with _lock:
                tunnel = _tunnels.get(code)
            if not tunnel or tunnel.expires < time.time():
                self._send_json(404, {"error": "No tunnel for this code"})
                return
            length = int(self.headers.get("Content-Length", 0))
            try:
                body     = json.loads(self.rfile.read(length))
                messages = body.get("messages", [body] if "d" in body else [])
            except Exception:
                self._send_json(400, {"error": "Bad JSON"})
                return
            tunnel.push(messages)
            self._send_json(200, {"ok": True})

        else:
            self._send_json(404, {"error": "Not found"})

    # ── GET ───────────────────────────────────────────────────────────────────

    def do_GET(self) -> None:
        path  = self.path.split("?")[0]
        parts = path.strip("/").split("/")

        # GET /lookup/{code}
        if len(parts) == 2 and parts[0] == "lookup":
            code = parts[1]
            with _lock:
                sess = _sessions.get(code)
            if sess and sess["expires"] > time.time():
                self._send_json(200, {"ip": sess["ip"], "port": sess["port"]})
            else:
                self._send_json(404, {"error": "Session not found or expired"})

        # GET /tunnel/{code}/poll[?timeout=N]
        elif len(parts) == 3 and parts[0] == "tunnel" and parts[2] == "poll":
            code = parts[1]
            with _lock:
                tunnel = _tunnels.get(code)
            if not tunnel or tunnel.expires < time.time():
                self._send_json(404, {"error": "No tunnel for this code"})
                return
            try:
                qs     = self.path.split("?", 1)[1] if "?" in self.path else ""
                params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
                tval   = min(float(params.get("timeout", "20")), 28.0)
            except Exception:
                tval = 20.0
            messages = tunnel.poll(tval)
            self._send_json(200, {"messages": messages})

        # GET / or /health
        elif self.path.split("?")[0] in ("/", "/health"):
            self._send_json(200, {"status": "ok", "sessions": len(_sessions)})

        else:
            self._send_json(404, {"error": "Not found"})


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port   = int(os.environ.get("PORT", 9877))
    server = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    print(f"IsoDAQ signaling+relay server on port {port}", flush=True)
    server.serve_forever()
