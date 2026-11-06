r"""
core/triggers.py — Trigger engine

Supported trigger types (set via prefix in the text field):

  Brownout Detector               ← [contains] default, case-insensitive
  [regex]  hard fault|HardFault
  [python] lambda line: 'err' in line.lower() and int(line.split('=')[1]) > 100
  [checksum] xor8                 ← a line ending in *HH whose checksum is wrong

Each trigger can fire any combination of actions:
  action_flash  — highlight line in terminal
  action_log    — write TRIGGER marker to log file
  action_sound  — system beep
  action_pause  — pause the active log session
  action_resume — resume the log session
  reply         — send a reply to the device (auto-response, CMP7); regex
                  groups are substituted: \1 … \9, \g<name>; escapes \r \n
                  \t \0 \xHH \\
  snapshot      — save the lines around the match to a file (CMP21)
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from core.checksum import ALGORITHMS, star_mismatch
from core.snapshot import clamp_lines


@dataclass
class Trigger:
    """One match rule + the actions to fire when a serial line matches it.

    ``type`` selects the matcher: ``contains`` (case-insensitive substring),
    ``regex``, ``checksum`` (the pattern names an algorithm; matches a line whose
    ``*HH`` ending is wrong) or ``python`` (a ``lambda line: ...`` predicate). Python triggers
    execute code, so one loaded from an untrusted source is held ``_blocked``
    (never ``eval()``'d) until :meth:`trust` is called. See SECURITY.md.
    """

    name:    str
    pattern: str
    type:    str  = "contains"   # contains | regex | checksum | python
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

    # Auto-response (CMP7): sent to the device on a match, plus the command
    # bar's line ending. Empty = no reply.
    reply: str = ""

    # Snapshot (CMP21): the lines around the match, saved to a file and, if
    # snapshot_notify is on, sent to notify_url as well.
    snapshot:        bool = False
    snapshot_before: int  = 100
    snapshot_after:  int  = 50
    snapshot_notify: bool = False

    hit_count: int = 0

    # Internal compiled state
    _compiled: object     = field(default=None, repr=False)
    _fn:       Callable | None = field(default=None, repr=False)
    # Security: a "python" trigger loaded from an untrusted source is blocked
    # from ever being eval()'d until the user explicitly trusts/edits it.
    _blocked:  bool       = field(default=False, repr=False)
    _last_reply: float    = field(default=0.0, repr=False)

    #: A reply that the device echoes back could re-trigger itself forever;
    #: replies closer together than this are dropped.
    REPLY_MIN_INTERVAL = 0.1

    def compile(self) -> str | None:
        """Returns error string on failure, None on success."""
        self._compiled = self._fn = None
        try:
            if self.type == "regex":
                self._compiled = re.compile(self.pattern, re.IGNORECASE)
            elif self.type == "checksum":
                if self.pattern.strip().lower() not in ALGORITHMS:
                    return "Unknown checksum. Use one of: " + ", ".join(ALGORITHMS)
            elif self.type == "python":
                if self._blocked:
                    return "Blocked: untrusted Python trigger (not enabled)"
                self._fn = eval(self.pattern)          # noqa: S307
                if not callable(self._fn):
                    return "Expression must be callable (lambda)"
        except Exception as e:
            return str(e)
        return None

    def matches(self, line: str) -> bool:
        """True if *line* matches. Never raises: a matcher error returns False."""
        if not self.enabled:
            return False
        try:
            if self.type == "contains":
                return self.pattern.lower() in line.lower()
            elif self.type == "regex":
                if self._compiled is None:
                    self._compiled = re.compile(self.pattern, re.IGNORECASE)
                return bool(self._compiled.search(line))
            elif self.type == "checksum":
                return star_mismatch(self.pattern.strip().lower(), line)
            elif self.type == "python":
                if self._blocked:
                    return False
                if self._fn is None:
                    self._fn = eval(self.pattern)      # noqa: S307
                return bool(self._fn(line))
        except Exception:
            return False
        return False

    def render_reply(self, line: str) -> bytes:
        """The reply for *line*: regex groups substituted, escapes decoded."""
        m = None
        if self.type == "regex":
            if self._compiled is None:
                self._compiled = re.compile(self.pattern, re.IGNORECASE)
            m = self._compiled.search(line)
        return render_reply(self.reply, m)

    def reply_due(self) -> bool:
        """Rate guard for the reply (see ``REPLY_MIN_INTERVAL``)."""
        now = time.monotonic()
        if now - self._last_reply < self.REPLY_MIN_INTERVAL:
            return False
        self._last_reply = now
        return True

    def trust(self) -> str | None:
        """Un-block a previously blocked Python trigger and (re)compile it."""
        self._blocked = False
        return self.compile()


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
        """Hot path: test *line* against every trigger, firing callbacks +
        notifications for each match. Called once per received serial line."""
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
                "reply":          t.reply,
                "snapshot":        t.snapshot,
                "snapshot_before": t.snapshot_before,
                "snapshot_after":  t.snapshot_after,
                "snapshot_notify": t.snapshot_notify,
            } for t in self._triggers]

    @staticmethod
    def count_python(data: list[dict]) -> int:
        """How many entries are executable [python] triggers (for load warnings)."""
        return sum(1 for d in data if d.get("type") == "python")

    def from_dict_list(self, data: list[dict], allow_python: bool = True):
        """
        Load triggers. If allow_python is False, [python] triggers are added but
        left *blocked* — their code is never eval()'d until the user trusts them.
        """
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
                reply=d.get("reply",""),
                snapshot=bool(d.get("snapshot",False)),
                snapshot_before=clamp_lines(d.get("snapshot_before"), 100),
                snapshot_after=clamp_lines(d.get("snapshot_after"), 50),
                snapshot_notify=bool(d.get("snapshot_notify",False)),
            )
            if t.type == "python" and not allow_python:
                t._blocked = True
                t.enabled  = False   # show as off in the UI until the user trusts it
            t.compile()   # blocked python triggers skip eval; errors are non-fatal
            with self._lock:
                self._triggers.append(t)


_REPLY_TOKEN = re.compile(r"\\(?:x([0-9A-Fa-f]{2})|g<(\w+)>|([1-9])|(.))", re.DOTALL)
_REPLY_ESC = {"r": b"\r", "n": b"\n", "t": b"\t", "0": b"\0", "\\": b"\\"}


def render_reply(template: str, match: re.Match | None = None) -> bytes:
    r"""Expand a reply template to bytes.

    ``\1`` … ``\9`` / ``\g<name>`` insert regex groups (empty if absent),
    ``\r \n \t \0 \\`` and ``\xHH`` are byte escapes; anything else is
    literal UTF-8."""
    out = bytearray()
    pos = 0
    for tok in _REPLY_TOKEN.finditer(template):
        out += template[pos:tok.start()].encode()
        hexb, name, num, ch = tok.groups()
        if hexb:
            out.append(int(hexb, 16))
        elif name or num:
            try:
                val = match.group(name or int(num)) if match else None
            except IndexError:
                val = None
            out += (val or "").encode()
        elif ch in _REPLY_ESC:
            out += _REPLY_ESC[ch]
        else:
            out += ("\\" + ch).encode()
        pos = tok.end()
    out += template[pos:].encode()
    return bytes(out)



def parse_trigger_line(text: str) -> tuple[str, str]:
    """
    '[regex] pattern'   → ('regex',   'pattern')
    '[python] lambda …' → ('python',  'lambda …')
    '[checksum] xor8'   → ('checksum','xor8')
    'plain text'        → ('contains','plain text')
    """
    for t in ("contains", "regex", "checksum", "python"):
        if text.strip().lower().startswith(f"[{t}]"):
            return t, text.strip()[len(t)+2:].strip()
    return "contains", text.strip()
