#!/usr/bin/env python3
"""
IsoDAQ Signaling Server

Two endpoints:
  POST /register   body: {"ip": "1.2.3.4", "port": 9876}
                   resp: {"code": "481203"}

  GET  /lookup/481203
                   resp: {"ip": "1.2.3.4", "port": 9876}
                   or 404 {"error": "..."}

Sessions expire after 1 hour.  No database — in-memory only.

Deploy on Railway (free tier):
  1. Push this folder to a GitHub repo.
  2. New project on railway.app → Deploy from GitHub repo.
  3. Done. Copy the generated URL into IsoDAQ core/signaling.py _DEFAULT_URL.
"""
from __future__ import annotations

import json
import os
import random
import string
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

_sessions: dict[str, dict] = {}   # code → {"ip", "port", "expires"}
_lock = threading.Lock()
_TTL  = 3600   # seconds


def _clean() -> None:
    now = time.time()
    with _lock:
        expired = [k for k, v in _sessions.items() if v["expires"] < now]
        for k in expired:
            del _sessions[k]


def _gen_code() -> str:
    """Generate a unique 6-digit numeric code."""
    while True:
        code = "".join(random.choices(string.digits, k=6))
        if code not in _sessions:
            return code


class _Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt: str, *args) -> None:  # silence default access log
        pass

    def _send_json(self, status: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path != "/register":
            self._send_json(404, {"error": "Not found"})
            return

        length = int(self.headers.get("Content-Length", 0))
        try:
            body   = json.loads(self.rfile.read(length))
            ip     = str(body["ip"])
            port   = int(body["port"])
        except Exception:
            self._send_json(400, {"error": "Bad request — expected {ip, port}"})
            return

        _clean()
        with _lock:
            code = _gen_code()
            _sessions[code] = {
                "ip":      ip,
                "port":    port,
                "expires": time.time() + _TTL,
            }

        print(f"[register] code={code}  {ip}:{port}")
        self._send_json(200, {"code": code})

    def do_GET(self) -> None:
        parts = self.path.strip("/").split("/")
        if len(parts) == 2 and parts[0] == "lookup":
            code = parts[1]
            with _lock:
                sess = _sessions.get(code)
            if sess and sess["expires"] > time.time():
                self._send_json(200, {"ip": sess["ip"], "port": sess["port"]})
            else:
                self._send_json(404, {"error": "Session not found or expired"})
        elif self.path in ("/", "/health"):
            self._send_json(200, {"status": "ok", "sessions": len(_sessions)})
        else:
            self._send_json(404, {"error": "Not found"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 9877))
    server = HTTPServer(("0.0.0.0", port), _Handler)
    print(f"IsoDAQ signaling server on port {port}")
    server.serve_forever()
