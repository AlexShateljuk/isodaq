"""
core/macros.py — Macro command sequencer

A Macro is a named sequence of MacroSteps.
Each step sends a command, waits a configurable delay,
and optionally waits for a pattern in the RX stream before proceeding.

MacroRunner (QObject) drives execution with QTimers — fully non-blocking,
safe to use from the GUI thread.

Execution flow per step:
  1. send command
  2. if wait_for set  → wait for RX pattern (or timeout)
  3. apply delay_ms
  4. advance to next step
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

EOL_MAP: dict[str, bytes] = {
    "\\r\\n": b"\r\n",
    "\\n":    b"\n",
    "\\r":    b"\r",
    "None":   b"",
}


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class MacroStep:
    command:         str = ""
    delay_ms:        int = 200       # pause AFTER this step (before next)
    wait_for:        str = ""        # optional: wait for this substring in RX
    wait_timeout_ms: int = 3000      # timeout if wait_for is set


@dataclass
class Macro:
    name:  str             = "New macro"
    eol:   str             = "\\r\\n"
    steps: list[MacroStep] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name":  self.name,
            "eol":   self.eol,
            "steps": [
                {
                    "command":         s.command,
                    "delay_ms":        s.delay_ms,
                    "wait_for":        s.wait_for,
                    "wait_timeout_ms": s.wait_timeout_ms,
                }
                for s in self.steps
            ],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Macro":
        return cls(
            name  = d.get("name", "Macro"),
            eol   = d.get("eol", "\\r\\n"),
            steps = [
                MacroStep(
                    command         = s.get("command", ""),
                    delay_ms        = int(s.get("delay_ms", 200)),
                    wait_for        = s.get("wait_for", ""),
                    wait_timeout_ms = int(s.get("wait_timeout_ms", 3000)),
                )
                for s in d.get("steps", [])
            ],
        )


# ── Runner ────────────────────────────────────────────────────────────────────

class MacroRunner(QObject):
    """
    Executes a Macro step-by-step using QTimers.
    Never blocks the GUI thread.

    Signals:
      step_started(step_index, command)   — command was sent
      step_waiting(step_index, pattern)   — waiting for RX pattern
      finished()                          — all steps completed
      aborted(error_message)              — stopped or send error
    """

    step_started = pyqtSignal(int, str)
    step_waiting = pyqtSignal(int, str)
    finished     = pyqtSignal()
    aborted      = pyqtSignal(str)

    def __init__(self, send_fn: Callable[[bytes], str | None], parent=None):
        super().__init__(parent)
        self._send     = send_fn
        self._macro: Macro | None = None
        self._idx      = 0
        self._running  = False
        self._wait_pat = ""

        # delay timer: fires after each step's delay_ms
        self._delay_t = QTimer(self)
        self._delay_t.setSingleShot(True)
        self._delay_t.timeout.connect(self._on_delay)

        # wait timer: fires when wait_for times out
        self._wait_t = QTimer(self)
        self._wait_t.setSingleShot(True)
        self._wait_t.timeout.connect(self._on_wait_timeout)

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._running

    def start(self, macro: Macro) -> None:
        """Begin executing *macro* from step 0."""
        self._delay_t.stop()
        self._wait_t.stop()
        self._macro   = macro
        self._idx     = 0
        self._running = True
        self._wait_pat = ""
        self._execute()

    def send_file(self, path: str) -> str | None:
        """
        Send the raw contents of *path* immediately (outside a macro sequence).
        Returns an error string on failure, or None on success.
        """
        try:
            data = Path(path).read_bytes()
        except Exception as e:
            return f"Cannot read file: {e}"
        return self._send(data)

    def stop(self) -> None:
        """Abort the current macro run."""
        self._delay_t.stop()
        self._wait_t.stop()
        self._running  = False
        self._wait_pat = ""
        self.aborted.emit("Stopped by user")

    def feed_rx_line(self, line: str) -> None:
        """
        Feed every incoming RX line while the runner is active.
        A substring match on the current wait_for pattern advances
        execution to the next step immediately.
        """
        if self._running and self._wait_pat:
            if self._wait_pat.lower() in line.lower():
                self._wait_t.stop()
                self._wait_pat = ""
                self._after_wait()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _execute(self) -> None:
        """Send the command (or file) for step[self._idx]."""
        if not self._running or self._macro is None:
            return
        if self._idx >= len(self._macro.steps):
            self._running = False
            self.finished.emit()
            return

        step = self._macro.steps[self._idx]

        # ── Build payload ─────────────────────────────────────────────────────
        if step.command.startswith("@file:"):
            # Send raw file bytes — no EOL appended
            file_path = step.command[6:].strip()
            try:
                payload = Path(file_path).read_bytes()
            except Exception as exc:
                self._running = False
                self.aborted.emit(f"Step {self._idx + 1}: cannot read file: {exc}")
                return
            display = f"[file] {Path(file_path).name}  ({len(payload):,} B)"
        else:
            eol     = EOL_MAP.get(self._macro.eol, b"\r\n")
            payload = step.command.encode("utf-8", errors="replace") + eol
            display = step.command

        err = self._send(payload)
        if err:
            self._running = False
            self.aborted.emit(f"Step {self._idx + 1}: {err}")
            return

        self.step_started.emit(self._idx, display)

        if step.wait_for:
            self._wait_pat = step.wait_for
            self.step_waiting.emit(self._idx, step.wait_for)
            self._wait_t.start(max(100, step.wait_timeout_ms))
        else:
            self._delay_t.start(max(0, step.delay_ms))

    def _after_wait(self) -> None:
        """Pattern matched (or timed out) — apply delay, then next step."""
        if self._macro is None:
            return
        delay = self._macro.steps[self._idx].delay_ms
        self._idx += 1
        self._delay_t.start(max(0, delay))

    def _on_delay(self) -> None:
        self._execute()

    def _on_wait_timeout(self) -> None:
        self._wait_pat = ""
        self._after_wait()
