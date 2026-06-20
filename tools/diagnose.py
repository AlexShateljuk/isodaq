#!/usr/bin/env python3
"""
tools/diagnose.py — IsoDAQ sharing diagnostic (run on the affected machine).

Self-contained: copy this single file anywhere and run `python diagnose.py`.
It reads the LOCAL config, shows the exact signaling URL the app would use,
normalizes it the same way the app does, and tests the real endpoints.

This reproduces precisely what the app does when you click Share, so a 404
here means the same 404 in the app — and the output shows why.
"""
from __future__ import annotations

import ipaddress
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

_DEFAULT_URL = "https://isodaq-production.up.railway.app"
_CONFIG = Path.home() / ".isodaq_studio" / "config.json"


def _is_local(host: str) -> bool:
    if host in ("", "localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_loopback or ip.is_private
    except ValueError:
        return False


def normalize(url: str) -> str:
    """Same logic as core/signaling.normalize()."""
    raw = (url or _DEFAULT_URL).strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    p = urlparse(raw)
    if not p.netloc:
        return ""
    scheme = p.scheme
    if scheme == "http" and not _is_local(p.hostname or ""):
        scheme = "https"
    return f"{scheme}://{p.netloc}"


def main() -> int:
    print("\nIsoDAQ sharing diagnostic")
    print("─" * 50)

    # 1. Read config
    print(f"\nConfig file: {_CONFIG}")
    raw_url = ""
    if _CONFIG.exists():
        try:
            data = json.loads(_CONFIG.read_text())
            raw_url = str(data.get("signaling_url", ""))
            print(f"  signaling_url (saved): {raw_url!r}")
        except Exception as e:
            print(f"  ! could not parse config: {e}")
    else:
        print("  ! config does not exist yet (app never saved settings)")

    # 2. Show what the OLD app (no normalize) vs NEW app would hit
    old_base = (raw_url or _DEFAULT_URL).rstrip("/")
    new_base = normalize(raw_url)
    print(f"\n  default URL          : {_DEFAULT_URL}")
    print(f"  base WITHOUT fix     : {old_base}")
    print(f"  base WITH normalize  : {new_base}")
    if old_base != new_base:
        print(f"  ⚠  saved URL has a stray path — this is the 404 cause.")
        print(f"     old register URL: {old_base}/register   ← 404")
        print(f"     fixed register  : {new_base}/register   ← ok")

    # 3. Actually hit /register on the normalized base (what the fixed app does)
    print(f"\nTesting POST {new_base}/register ...")
    try:
        body = json.dumps({"ip": "1.2.3.4", "port": 9876}).encode()
        req  = urllib.request.Request(
            f"{new_base}/register", data=body,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as r:
            resp = json.loads(r.read())
        code = str(resp.get("code", ""))
        print(f"  ✓ register OK → code {code}")
    except urllib.error.HTTPError as e:
        print(f"  ✗ HTTP {e.code}: {e.reason}  (server reached, wrong path)")
        return 1
    except Exception as e:
        print(f"  ✗ cannot reach server: {e}  (network/firewall, NOT a 404)")
        return 1

    # 4. Tunnel round-trip (the NAT-bypass path)
    print(f"\nTesting relay tunnel push → poll ...")
    try:
        msg = {"messages": [{"t": 1, "d": "diag", "k": "rx"}]}
        req = urllib.request.Request(
            f"{new_base}/tunnel/{code}/push", data=json.dumps(msg).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=8)
        with urllib.request.urlopen(f"{new_base}/tunnel/{code}/poll?timeout=3", timeout=8) as r:
            got = json.loads(r.read()).get("messages", [])
        if got and got[0].get("d") == "diag":
            print(f"  ✓ relay round-trip OK")
        else:
            print(f"  ✗ relay returned unexpected: {got}")
            return 1
    except urllib.error.HTTPError as e:
        print(f"  ✗ tunnel HTTP {e.code}: {e.reason}  (server has OLD code — needs redeploy)")
        return 1
    except Exception as e:
        print(f"  ✗ tunnel error: {e}")
        return 1

    print("\n✓ All checks passed — sharing works from this machine.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
