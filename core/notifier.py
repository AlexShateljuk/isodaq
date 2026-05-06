"""
core/notifier.py — Fire-and-forget HTTP notifications on trigger match.

Supports:
  Generic webhook  — POST JSON to any URL
  Telegram         — POST to api.telegram.org/bot<TOKEN>/sendMessage

Both run in a daemon thread so they never block the serial read path.
"""
from __future__ import annotations

import json
import threading
import urllib.request


def send_notification(url: str, tg_chat: str,
                      trigger_name: str, line: str, ts: str) -> None:
    """Schedule a notification in a background daemon thread."""
    if not url.strip():
        return
    t = threading.Thread(
        target=_dispatch,
        args=(url.strip(), tg_chat.strip(), trigger_name, line, ts),
        daemon=True,
    )
    t.start()


def _dispatch(url: str, tg_chat: str,
              name: str, line: str, ts: str) -> None:
    text = f"[IsoDAQ] Trigger '{name}' fired at {ts}\n{line}"
    try:
        if "api.telegram.org" in url:
            _send_telegram(url, tg_chat, text)
        else:
            _send_webhook(url, name, line, ts, text)
    except Exception:
        pass  # network errors silently ignored — no UI access from thread


def _send_telegram(url: str, chat_id: str, text: str) -> None:
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Content-Type": "application/json",
                 "User-Agent": "IsoDAQ-Studio/1"},
    )
    with urllib.request.urlopen(req, timeout=10):
        pass


def _send_webhook(url: str, name: str, line: str, ts: str, text: str) -> None:
    payload = json.dumps(
        {"trigger": name, "line": line, "ts": ts, "text": text}
    ).encode()
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Content-Type": "application/json",
                 "User-Agent": "IsoDAQ-Studio/1"},
    )
    with urllib.request.urlopen(req, timeout=10):
        pass
