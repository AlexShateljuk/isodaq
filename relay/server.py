#!/usr/bin/env python3
"""
IsoDAQ Signaling + Relay Server

Endpoints:
  POST /register               {"ip":"1.2.3.4","port":9876}  →  {"code":"481203"}
  GET  /lookup/481203          →  {"ip":"1.2.3.4","port":9876}  or 404
  POST /tunnel/481203/push     {"messages":[...]}  →  {"ok":true,"viewers":N}
  GET  /tunnel/481203/poll?id=X&timeout=20   →  {"messages":[...]}
  GET  /health                 →  {"status":"ok","sessions":N}

Each tunnel fans messages out to every connected viewer (its own queue), so
multiple viewers can watch one session. A short recent-message buffer lets a
viewer that joins mid-stream catch up. Sessions expire after 1 hour.
"""
from __future__ import annotations

import collections
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
_VIEWER_TTL = 30   # seconds a viewer is considered present after its last poll

# ── Abuse limits (OSS3) ─────────────────────────────────────────────────────────
# The public relay is a shared, unauthenticated resource. Without caps a single
# client can exhaust memory (spam /register), brute-force the 6-digit code space
# (/lookup enumeration), or blow up a tunnel's fan-out. Tune via env for self-hosts.
_MAX_SESSIONS        = int(os.environ.get("ISODAQ_MAX_SESSIONS", 500))   # global live-tunnel cap
_MAX_SESSIONS_PER_IP = int(os.environ.get("ISODAQ_MAX_PER_IP", 20))      # per-source tunnel cap
_MAX_VIEWERS         = int(os.environ.get("ISODAQ_MAX_VIEWERS", 50))     # per-tunnel viewer cap
_MAX_BODY            = 1 << 20   # 1 MiB — reject oversized request bodies
_RATE_WINDOW         = 60.0      # sliding-window length, seconds
_RATE_MAX            = 60        # max register+lookup requests per IP per window

_hits: dict[str, collections.deque] = {}   # ip → deque[request timestamps]


def _rate_ok(ip: str) -> bool:
    """Sliding-window limiter for the abuse-prone endpoints (register, lookup)."""
    now = time.time()
    with _lock:
        dq = _hits.setdefault(ip, collections.deque())
        while dq and dq[0] < now - _RATE_WINDOW:
            dq.popleft()
        if len(dq) >= _RATE_MAX:
            return False
        dq.append(now)
        return True


# ── Tunnel session ─────────────────────────────────────────────────────────────

class _Viewer:
    def __init__(self) -> None:
        self.q:     queue.Queue       = queue.Queue(maxsize=10_000)
        self.event: threading.Event   = threading.Event()
        self.last:  float             = time.time()


class _TunnelSession:
    """Fan-out hub: one sharer pushes, N viewers each poll their own queue."""

    def __init__(self) -> None:
        self.viewers: dict[str, _Viewer] = {}
        self.recent:  collections.deque  = collections.deque(maxlen=200)
        self.lock     = threading.Lock()
        self.expires  = time.time() + _TTL

    def push(self, messages: list[dict]) -> None:
        # Buffer only displayable lines (skip control/heartbeat msgs)
        keep = [m for m in messages if not str(m.get("k", "")).startswith("_")]
        with self.lock:
            for m in keep:
                self.recent.append(m)
            viewers = list(self.viewers.values())
        for v in viewers:
            for m in messages:
                try:
                    v.q.put_nowait(m)
                except queue.Full:
                    pass
            v.event.set()

    def poll(self, viewer_id: str, timeout: float) -> list[dict]:
        with self.lock:
            v   = self.viewers.get(viewer_id)
            new = v is None
            if new and len(self.viewers) >= _MAX_VIEWERS:
                return []   # fan-out cap reached — refuse new viewers on this tunnel
            if new:
                v = _Viewer()
                for m in list(self.recent):   # let a mid-stream viewer catch up
                    try:
                        v.q.put_nowait(m)
                    except queue.Full:
                        break
                self.viewers[viewer_id] = v
            v.last = time.time()

        if v.q.empty():
            v.event.wait(timeout=timeout)
            v.event.clear()

        out: list[dict] = []
        while not v.q.empty():
            try:
                out.append(v.q.get_nowait())
            except queue.Empty:
                break
        return out

    def viewer_count(self) -> int:
        now = time.time()
        with self.lock:
            stale = [k for k, v in self.viewers.items() if v.last < now - _VIEWER_TTL]
            for k in stale:
                del self.viewers[k]
            return len(self.viewers)


_tunnels: dict[str, _TunnelSession] = {}


def _clean() -> None:
    now = time.time()
    with _lock:
        expired = [k for k, v in _sessions.items() if v["expires"] < now]
        for k in expired:
            del _sessions[k]
            _tunnels.pop(k, None)
        # Prune rate-limit buckets that have fully drained (bounds _hits growth)
        for ip in [ip for ip, dq in _hits.items()
                   if not dq or dq[-1] < now - _RATE_WINDOW]:
            del _hits[ip]


def _gen_code() -> str:
    while True:
        code = "".join(random.choices(string.digits, k=6))
        if code not in _sessions:
            return code


def _query(path: str) -> dict[str, str]:
    if "?" not in path:
        return {}
    qs = path.split("?", 1)[1]
    return dict(p.split("=", 1) for p in qs.split("&") if "=" in p)


# ── HTTP handler ───────────────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt: str, *args) -> None:   # silence default access log
        pass

    def _client_ip(self) -> str:
        """Real client IP, honouring the proxy header the Railway edge sets."""
        xff = self.headers.get("X-Forwarded-For", "")
        if xff:
            return xff.split(",")[0].strip()
        return self.client_address[0]

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

        if self.path == "/register":
            src = self._client_ip()
            if not _rate_ok(src):
                self._send_json(429, {"error": "Rate limit exceeded — slow down"})
                return
            length = int(self.headers.get("Content-Length", 0))
            if length > _MAX_BODY:
                self._send_json(413, {"error": "Body too large"})
                return
            try:
                body = json.loads(self.rfile.read(length))
                ip   = str(body["ip"])
                port = int(body["port"])
            except Exception:
                self._send_json(400, {"error": "Bad request — expected {ip, port}"})
                return

            _clean()
            with _lock:
                if len(_sessions) >= _MAX_SESSIONS:
                    self._send_json(503, {"error": "Relay at capacity — try again later "
                                                   "or self-host"})
                    return
                if sum(1 for s in _sessions.values() if s.get("src") == src) >= _MAX_SESSIONS_PER_IP:
                    self._send_json(429, {"error": "Too many active sessions from this client"})
                    return
                code = _gen_code()
                _sessions[code] = {"ip": ip, "port": port,
                                   "expires": time.time() + _TTL, "src": src}
                _tunnels[code]   = _TunnelSession()

            print(f"[register] code={code}  {ip}:{port}  src={src}", flush=True)
            self._send_json(200, {"code": code})

        elif len(parts) == 3 and parts[0] == "tunnel" and parts[2] == "push":
            code = parts[1]
            with _lock:
                tunnel = _tunnels.get(code)
            if not tunnel or tunnel.expires < time.time():
                self._send_json(404, {"error": "No tunnel for this code"})
                return
            length = int(self.headers.get("Content-Length", 0))
            if length > _MAX_BODY:
                self._send_json(413, {"error": "Body too large"})
                return
            try:
                body     = json.loads(self.rfile.read(length))
                messages = body.get("messages", [body] if "d" in body else [])
            except Exception:
                self._send_json(400, {"error": "Bad JSON"})
                return
            tunnel.push(messages)
            self._send_json(200, {"ok": True, "viewers": tunnel.viewer_count()})

        else:
            self._send_json(404, {"error": "Not found"})

    # ── GET ───────────────────────────────────────────────────────────────────

    def do_GET(self) -> None:
        path  = self.path.split("?")[0]
        parts = path.strip("/").split("/")

        if len(parts) == 2 and parts[0] == "lookup":
            if not _rate_ok(self._client_ip()):
                self._send_json(429, {"error": "Rate limit exceeded — slow down"})
                return
            code = parts[1]
            with _lock:
                sess = _sessions.get(code)
            if sess and sess["expires"] > time.time():
                self._send_json(200, {"ip": sess["ip"], "port": sess["port"]})
            else:
                self._send_json(404, {"error": "Session not found or expired"})

        elif len(parts) == 3 and parts[0] == "tunnel" and parts[2] == "poll":
            code = parts[1]
            with _lock:
                tunnel = _tunnels.get(code)
            if not tunnel or tunnel.expires < time.time():
                self._send_json(404, {"error": "No tunnel for this code"})
                return
            params = _query(self.path)
            try:
                tval = min(float(params.get("timeout", "20")), 28.0)
            except Exception:
                tval = 20.0
            viewer_id = params.get("id", "_anon")
            messages  = tunnel.poll(viewer_id, tval)
            self._send_json(200, {"messages": messages})

        elif path in ("/", "/health"):
            self._send_json(200, {"status": "ok", "sessions": len(_sessions),
                                  "limit": _MAX_SESSIONS})

        else:
            self._send_json(404, {"error": "Not found"})


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port   = int(os.environ.get("PORT", 9877))
    server = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    print(f"IsoDAQ signaling+relay server on port {port}", flush=True)
    server.serve_forever()
