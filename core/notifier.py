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

#: Telegram refuses a message over 4096 characters; leave room for the title.
TELEGRAM_LIMIT = 3900
WEBHOOK_SNAPSHOT_LIMIT = 256 * 1024


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


def send_snapshot(url: str, tg_chat: str, trigger_name: str, file_name: str,
                  text: str) -> None:
    """Send a saved snapshot (CMP21) the same way. A long one is cut from the start,
    since the lines after the match are the ones a notification is wanted for."""
    if not url.strip():
        return
    threading.Thread(target=_dispatch_snapshot,
                     args=(url.strip(), tg_chat.strip(), trigger_name, file_name, text),
                     daemon=True).start()


def _dispatch_snapshot(url: str, tg_chat: str, name: str, file_name: str, text: str) -> None:
    title = f"[IsoDAQ] Snapshot of trigger '{name}': {file_name}"
    try:
        if "api.telegram.org" in url:
            _send_telegram(url, tg_chat, f"{title}\n{text[-TELEGRAM_LIMIT:]}")
        else:
            payload = json.dumps({"trigger": name, "file": file_name, "text": title,
                                  "snapshot": text[-WEBHOOK_SNAPSHOT_LIMIT:]}).encode()
            req = urllib.request.Request(
                url, data=payload, method="POST",
                headers={"Content-Type": "application/json",
                         "User-Agent": "IsoDAQ-Studio/1"},
            )
            with urllib.request.urlopen(req, timeout=10):
                pass
    except Exception:
        pass  # as _dispatch: no UI access from this thread


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
