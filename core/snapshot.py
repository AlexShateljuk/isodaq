"""
core/snapshot.py — save the lines around a trigger match to a file (CMP21).

Runs on the GUI thread, in the receive path: :meth:`SnapshotRecorder.feed` is
called for each line just before the trigger check, and the engine calls
:meth:`on_match` straight from that check, so a snapshot starts at exactly the
matching line.

A fault often ends the output, so a snapshot doesn't wait for ever for its lines
after the match: :meth:`poll` writes what it has once the device goes quiet. A
trigger that keeps firing can't fill the disk: while its snapshot is open further
matches are only marked in it, new ones are at least ``MIN_INTERVAL`` apart, and
a session writes at most ``MAX_FILES``.
"""
from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from itertools import islice
from pathlib import Path
from typing import Callable

MAX_LINES = 1000                     # before, and after, the match


def clamp_lines(value: object, default: int) -> int:
    """A line count from a settings or trigger file, which may hold anything."""
    try:
        return max(0, min(MAX_LINES, int(value)))     # type: ignore[call-overload]
    except (TypeError, ValueError):
        return default


@dataclass
class _Capture:
    trigger: object
    when: datetime
    lines: list = field(default_factory=list)        # (ts, line)
    marks: set = field(default_factory=set)          # indexes of matching lines
    remaining: int = 0
    skipped_before: int = 0
    opened: float = 0.0
    last_line: float = 0.0


class SnapshotRecorder:
    MIN_INTERVAL = 5.0     # seconds between snapshots of one trigger
    IDLE_TIMEOUT = 10.0    # write once no line has come for this long
    MAX_AGE = 60.0         # … and in any case this long after the match
    MAX_FILES = 500        # per session

    def __init__(self, folder: Callable[[], Path], device: Callable[[], str]):
        self._folder = folder
        self._device = device
        self._ring: deque = deque(maxlen=MAX_LINES + 1)
        self._open: dict[str, _Capture] = {}
        self._last_start: dict[str, float] = {}
        self._skipped: dict[str, int] = {}
        self._written = 0
        #: Set by the UI: on_saved(trigger, path, text), on_error(message).
        self.on_saved: Callable | None = None
        self.on_error: Callable | None = None

    def feed(self, line: str, ts: str) -> None:
        self._ring.append((ts, line))
        if not self._open:
            return
        now = time.monotonic()
        for name, cap in list(self._open.items()):
            cap.lines.append((ts, line))
            cap.last_line = now
            cap.remaining -= 1
            if cap.remaining <= 0:
                self._finish(name, cap)

    def on_match(self, trigger, _line: str, _ts: str) -> None:
        if not trigger.snapshot:
            return
        name = trigger.name
        cap = self._open.get(name)
        if cap is not None:
            cap.marks.add(len(cap.lines) - 1)
            return
        now = time.monotonic()
        if now - self._last_start.get(name, -self.MIN_INTERVAL) < self.MIN_INTERVAL:
            self._skipped[name] = self._skipped.get(name, 0) + 1
            return
        if self._written >= self.MAX_FILES:
            if self._written == self.MAX_FILES:
                self._written += 1
                self._report_error(f"{self.MAX_FILES} snapshots this session; "
                                   "no more will be saved")
            return
        self._last_start[name] = now
        before = clamp_lines(trigger.snapshot_before, 100)
        lines = list(islice(reversed(self._ring), before + 1))
        lines.reverse()
        cap = _Capture(trigger, datetime.now(), lines, {len(lines) - 1},
                       clamp_lines(trigger.snapshot_after, 50),
                       self._skipped.pop(name, 0), now, now)
        if cap.remaining == 0:
            self._write(cap)
        else:
            self._open[name] = cap

    def poll(self) -> None:
        """Write any snapshot whose device has gone quiet."""
        now = time.monotonic()
        for name, cap in list(self._open.items()):
            if now - cap.last_line >= self.IDLE_TIMEOUT or now - cap.opened >= self.MAX_AGE:
                self._finish(name, cap)

    def flush(self) -> None:
        for name, cap in list(self._open.items()):
            self._finish(name, cap)

    def _finish(self, name: str, cap: _Capture) -> None:
        del self._open[name]
        self._write(cap)

    def _write(self, cap: _Capture) -> None:
        text = self._render(cap)
        try:
            path = self._save(cap, text)
        except OSError as e:
            self._report_error(str(e))
            return
        self._written += 1
        if self.on_saved is not None:
            self.on_saved(cap.trigger, path, text)

    def _render(self, cap: _Capture) -> str:
        head = [f"# IsoDAQ snapshot: {cap.trigger.name}",
                f"# Device: {self._device()}",
                f"# Time: {cap.when:%Y-%m-%d %H:%M:%S}",
                f"# Pattern: [{cap.trigger.type}] {cap.trigger.pattern}"]
        if cap.remaining > 0:
            head.append(f"# Ended {cap.remaining} line(s) short: the device went quiet")
        if len(cap.marks) > 1:
            head.append(f"# Matched {len(cap.marks)} times in this snapshot")
        if cap.skipped_before:
            head.append(f"# {cap.skipped_before} earlier match(es) had no snapshot "
                        f"(less than {self.MIN_INTERVAL:g} s apart)")
        body = [f"{ts}  {'>>' if i in cap.marks else '  '}  {line}"
                for i, (ts, line) in enumerate(cap.lines)]
        return "\n".join(head + [""] + body) + "\n"

    def _save(self, cap: _Capture, text: str) -> Path:
        folder = self._folder()
        folder.mkdir(parents=True, exist_ok=True)
        # The name comes from a trigger, possibly from someone else's file.
        safe = re.sub(r"[^\w-]+", "_", cap.trigger.name).strip("_")[:40] or "trigger"
        stem = f"{safe}_{cap.when:%Y%m%d_%H%M%S}"
        for n in range(1, 1000):
            path = folder / (f"{stem}.txt" if n == 1 else f"{stem}_{n}.txt")
            try:
                with open(path, "x", encoding="utf-8", newline="\n") as f:
                    f.write(text)
                return path
            except FileExistsError:
                continue
        raise OSError(f"Too many snapshots named {stem}")

    def _report_error(self, message: str) -> None:
        if self.on_error is not None:
            self.on_error(message)
