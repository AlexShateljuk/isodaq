"""
core/signaling.py — IsoDAQ signaling server client.

After deploying relay/server.py (e.g. on Railway), paste the URL below:

    _DEFAULT_URL = "https://your-app.railway.app"

Both the sharer and viewer must use the same URL.
The URL can also be overridden per-call if the user configures their own relay.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Optional

# ── Set this after deploying relay/server.py ──────────────────────────────────
_DEFAULT_URL = "https://isodaq-production.up.railway.app"
# ─────────────────────────────────────────────────────────────────────────────


def is_configured(url: str = "") -> bool:
    """Return True if a signaling server URL is available."""
    return bool(url or _DEFAULT_URL)


def _base(url: str) -> str:
    return (url or _DEFAULT_URL).rstrip("/")


def register(ip: str, port: int, url: str = "") -> Optional[str]:
    """
    Register this session with the signaling server.
    Returns a 6-digit code string, or None on failure.
    """
    base = _base(url)
    if not base:
        return None
    try:
        body = json.dumps({"ip": ip, "port": port}).encode()
        req  = urllib.request.Request(
            f"{base}/register",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            resp = json.loads(r.read())
        code = str(resp.get("code", ""))
        return code if len(code) == 6 and code.isdigit() else None
    except Exception:
        return None


def lookup(code: str, url: str = "") -> Optional[tuple[str, int]]:
    """
    Look up a session code.
    Returns (ip, port) or None if not found / server unreachable.
    """
    base = _base(url)
    if not base:
        return None
    clean = code.strip().replace("-", "").replace(" ", "")
    if not clean.isdigit() or len(clean) != 6:
        return None
    try:
        endpoint = f"{base}/lookup/{clean}"
        with urllib.request.urlopen(endpoint, timeout=5) as r:
            resp = json.loads(r.read())
        return str(resp["ip"]), int(resp["port"])
    except Exception:
        return None
