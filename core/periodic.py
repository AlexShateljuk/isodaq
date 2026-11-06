"""
core/periodic.py — repeat a command on a timer (CMP8).

Runs on the GUI thread with a QTimer, once per connection. It sends once straight
away, then every ``interval_ms``, and stops at ``count`` sends (0 = until stopped)
or at the first failed send, so a dead link isn't retried for ever.
"""
from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal


class PeriodicSender(QObject):
    progress = pyqtSignal(int)       # sends so far
    finished = pyqtSignal(str)       # why it stopped: "" = count reached, or the error

    MIN_INTERVAL_MS = 10

    def __init__(self, send: Callable[[], "str | None"], interval_ms: int, count: int = 0,
                 parent=None):
        super().__init__(parent)
        self._send = send
        self.interval_ms = max(self.MIN_INTERVAL_MS, int(interval_ms))
        self.count = max(0, int(count))
        self.sent = 0
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(self.interval_ms)
        self._timer.timeout.connect(self._tick)

    @property
    def running(self) -> bool:
        return self._timer.isActive()

    def start(self) -> str | None:
        """Send the first one now. Returns the error if even that fails."""
        err = self._send()
        if err:
            return err
        self.sent = 1
        self.progress.emit(self.sent)
        if self.count != 1:
            self._timer.start()
        else:
            self.finished.emit("")
        return None

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        err = self._send()
        if err:
            self._timer.stop()
            self.finished.emit(err)
            return
        self.sent += 1
        self.progress.emit(self.sent)
        if self.count and self.sent >= self.count:
            self._timer.stop()
            self.finished.emit("")
