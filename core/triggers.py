"""
core/triggers.py — Trigger engine

Supported trigger types (set via prefix in the text field):

  Brownout Detector               ← [contains] default, case-insensitive
  [regex]  hard fault|HardFault
  [python] lambda line: 'err' in line.lower() and int(line.split('=')[1]) > 100

Each trigger can fire any combination of actions:
  action_flash  — highlight line in terminal
  action_log    — write TRIGGER marker to log file
  action_sound  — system beep
  action_pause  — pause the active log session
  action_resume — resume the log session
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Trigger:
    name:    str
    pattern: str
    type:    str  = "contains"   # contains | regex | python
    enabled: bool = True
    color:   str  = "#ef4444"

    # Actions
    action_flash:  bool = True
    action_log:    bool = True
    action_sound:  bool = False
    action_pause:  bool = False
    action_resume: bool = False

    # Notifications
    notify_url:     str = ""   # webhook URL or Telegram sendMessage URL
    notify_tg_chat: str = ""   # Telegram chat_id (ignored for generic webhooks)

    hit_count: int = 0

    # Internal compiled state
    _compiled: object     = field(default=None, repr=False)
    _fn:       Callable | None = field(default=None, repr=False)

    def compile(self) -> str | None:
        """Returns error string on failure, None on success."""
        self._compiled = self._fn = None
        try:
            if self.type == "regex":
                self._compiled = re.compile(self.pattern, re.IGNORECASE)
            elif self.type == "python":
                self._fn = eval(self.pattern)          # noqa: S307
                if not callable(self._fn):
                    return "Expression must be callable (lambda)"
        except Exception as e:
            return str(e)
        return None

    def matches(self, line: str) -> bool:
        if not self.enabled:
            return False
        try:
            if self.type == "contains":
                return self.pattern.lower() in line.lower()
            elif self.type == "regex":
                if self._compiled is None:
                    self._compiled = re.compile(self.pattern, re.IGNORECASE)
                return bool(self._compiled.search(line))
            elif self.type == "python":
                if self._fn is None:
                    self._fn = eval(self.pattern)      # noqa: S307
                return bool(self._fn(line))
        except Exception:
            return False
        return False


class TriggerEngine:
    """
    Thread-safe trigger list + hot-path checker.
    Callbacks are registered with on_match() and called for every match.
    """

    def __init__(self):
        self._triggers: list[Trigger] = []
        self._lock = threading.RLock()
        self._callbacks: list[Callable] = []

    # ── Management ────────────────────────────────────────────────────────────

    def add_trigger(self, t: Trigger) -> str | None:
        err = t.compile()
        if err: return err
        with self._lock: self._triggers.append(t)
        return None

    def remove_trigger(self, idx: int):
        with self._lock:
            if 0 <= idx < len(self._triggers):
                self._triggers.pop(idx)

    def update_trigger(self, idx: int, t: Trigger) -> str | None:
        err = t.compile()
        if err: return err
        with self._lock:
            if 0 <= idx < len(self._triggers):
                self._triggers[idx] = t
        return None

    def get_triggers(self) -> list[Trigger]:
        with self._lock: return list(self._triggers)

    def clear_hit_counts(self):
        with self._lock:
            for t in self._triggers: t.hit_count = 0

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def on_match(self, cb: Callable):
        """Register callback(trigger, line, timestamp)."""
        self._callbacks.append(cb)

    # ── Hot path ──────────────────────────────────────────────────────────────

    def check(self, line: str, ts: str):
        with self._lock:
            triggers = list(self._triggers)
        for t in triggers:
            if t.matches(line):
                t.hit_count += 1
                for cb in self._callbacks:
                    try: cb(t, line, ts)
                    except Exception: pass
                if t.notify_url:
                    from core.notifier import send_notification
                    send_notification(t.notify_url, t.notify_tg_chat,
                                      t.name, line, ts)

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict_list(self) -> list[dict]:
        with self._lock:
            return [{
                "name": t.name, "pattern": t.pattern, "type": t.type,
                "enabled": t.enabled, "color": t.color,
                "action_flash":  t.action_flash,
                "action_log":    t.action_log,
                "action_sound":  t.action_sound,
                "action_pause":  t.action_pause,
                "action_resume": t.action_resume,
                "notify_url":     t.notify_url,
                "notify_tg_chat": t.notify_tg_chat,
            } for t in self._triggers]

    def from_dict_list(self, data: list[dict]):
        with self._lock: self._triggers.clear()
        for d in data:
            t = Trigger(
                name=d.get("name","trigger"), pattern=d.get("pattern",""),
                type=d.get("type","contains"), enabled=d.get("enabled",True),
                color=d.get("color","#ef4444"),
                action_flash=d.get("action_flash",True),
                action_log=d.get("action_log",True),
                action_sound=d.get("action_sound",False),
                action_pause=d.get("action_pause",False),
                action_resume=d.get("action_resume",False),
                notify_url=d.get("notify_url",""),
                notify_tg_chat=d.get("notify_tg_chat",""),
            )
            self.add_trigger(t)


def parse_trigger_line(text: str) -> tuple[str, str]:
    """
    '[regex] pattern'   → ('regex',   'pattern')
    '[python] lambda …' → ('python',  'lambda …')
    'plain text'        → ('contains','plain text')
    """
    for t in ("contains", "regex", "python"):
        if text.strip().lower().startswith(f"[{t}]"):
            return t, text.strip()[len(t)+2:].strip()
    return "contains", text.strip()
