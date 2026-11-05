"""
core/flash_runner.py — runs a build / flash command for a connection (CMP12).

``idf.py -p {port} flash``, ``pio run -t upload`` and the like run in the user's
own shell on a worker thread; their output is handed over in batches. Whoever
starts this (``ui/controllers/flash_controller.py``) releases the serial port
first, because the flashing tool needs it, and reconnects afterwards.

The command is exactly what the user typed into the Flash tool window and is
only ever started by a local action — see SECURITY.md.
"""
from __future__ import annotations

import os
import queue
import re
import shlex
import signal
import subprocess
import sys
import threading
import time

from PyQt6.QtCore import QThread, pyqtSignal

IS_WINDOWS = sys.platform == "win32"

#: Colour and cursor sequences; tools usually drop them when piped, not always.
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
#: Shells known to accept ``-lc`` (csh and friends reject that combination).
_LOGIN_SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish"}
#: A trailing partial line (a prompt, a row of dots) is shown after this long.
_PARTIAL_FLUSH_S = 0.5
#: Keep waiting this long for output after the command itself has exited; a
#: daemon it left behind may hold the pipe open for ever.
_EXIT_GRACE_S = 0.5
_KILL_AFTER_S = 2.0


def expand_command(template: str, port: str = "", baud: int | None = None) -> str:
    """Fill in ``{port}`` / ``{baud}``, quoted for the shell. Plain replacement,
    not ``str.format``: commands legitimately contain other braces."""
    def quote(value: str) -> str:
        if IS_WINDOWS:
            return f'"{value}"' if " " in value else value
        return shlex.quote(value)
    return (template.replace("{port}", quote(port))
                    .replace("{baud}", "" if baud is None else str(baud)))


def login_shell_argv(command: str) -> list[str]:
    """POSIX: the user's login shell, so ``pio`` / ``idf.py`` are on ``PATH``
    even when the app was started from Finder or a desktop launcher, whose
    environment has only the system default ``PATH``."""
    shell = os.environ.get("SHELL", "")
    if not shell:
        try:
            import pwd
            shell = pwd.getpwuid(os.getuid()).pw_shell
        except (ImportError, KeyError):
            shell = ""
    if os.path.basename(shell) in _LOGIN_SHELLS and os.path.exists(shell):
        return [shell, "-lc", command]
    return ["/bin/sh", "-c", command]


def child_env() -> dict[str, str]:
    """Environment for the command. A PyInstaller build points the library path
    at its own bundle, which would break any tool run from it — put back what
    the app was started with. Python tools (esptool, idf.py, pio) buffer stdout
    when piped; unbuffered shows their progress live."""
    env = os.environ.copy()
    if getattr(sys, "frozen", False):
        for var in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
            original = env.pop(f"{var}_ORIG", None)
            if original is not None:
                env[var] = original
            else:
                env.pop(var, None)
    env["PYTHONUNBUFFERED"] = "1"
    return env


class FlashRunner(QThread):
    """Runs one command to completion. :attr:`stopped` tells a Stop apart from a
    tool that failed by itself."""

    output = pyqtSignal(object)        # [line, …]
    finished_code = pyqtSignal(int, float)   # (exit code, seconds)

    def __init__(self, command: str, cwd: str = "", parent=None):
        super().__init__(parent)
        self.command = command
        self.cwd = cwd
        self.stopped = False
        self._proc: subprocess.Popen | None = None

    # ── Stop (GUI thread) ─────────────────────────────────────────────────────

    def stop(self) -> None:
        """Stop the command and everything it started: ``idf.py`` runs ``ninja``
        runs ``esptool``, and killing only the shell would leave the last of
        those holding the serial port."""
        self.stopped = True
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        self._kill_tree(proc, force=False)
        timer = threading.Timer(_KILL_AFTER_S, self._kill_tree, args=(proc, True))
        timer.daemon = True
        timer.start()

    @staticmethod
    def _kill_tree(proc: subprocess.Popen, force: bool) -> None:
        if IS_WINDOWS and proc.poll() is not None:
            return                       # the pid may belong to someone else by now
        try:
            if IS_WINDOWS:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True, check=False,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                # start_new_session made the shell a group leader: pgid == pid.
                os.killpg(proc.pid, signal.SIGKILL if force else signal.SIGTERM)
        except (OSError, subprocess.SubprocessError):
            pass                         # already gone

    # ── Worker thread ─────────────────────────────────────────────────────────

    def _start_process(self) -> subprocess.Popen:
        kwargs = dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                      stderr=subprocess.STDOUT, cwd=self.cwd or None, env=child_env())
        if IS_WINDOWS:
            return subprocess.Popen(
                self.command, shell=True,
                creationflags=subprocess.CREATE_NO_WINDOW
                | subprocess.CREATE_NEW_PROCESS_GROUP, **kwargs)
        return subprocess.Popen(login_shell_argv(self.command),
                                start_new_session=True, **kwargs)

    def run(self) -> None:
        t0 = time.monotonic()
        try:
            proc = self._start_process()
        except (OSError, ValueError) as e:
            self.output.emit([f"Could not start the command: {e}"])
            self.finished_code.emit(-1, 0.0)
            return
        self._proc = proc
        if self.stopped:                 # Stop landed before the process existed
            self._kill_tree(proc, force=True)

        # The read blocks for as long as anything holds the pipe, so it gets its
        # own daemon thread and this loop can notice the command has exited.
        chunks: queue.Queue = queue.Queue()

        def pump(fd: int) -> None:
            try:
                while True:
                    chunk = os.read(fd, 65536)
                    if not chunk:
                        break
                    chunks.put(chunk)
            except OSError:
                pass
            chunks.put(None)

        threading.Thread(target=pump, args=(proc.stdout.fileno(),), daemon=True).start()

        buf = bytearray()
        last_data = time.monotonic()
        exited_at = None
        eof = False
        while not eof:
            try:
                chunk = chunks.get(timeout=0.1)
            except queue.Empty:
                chunk = b""
            if chunk is None:
                eof = True
            elif chunk:
                buf.extend(chunk)
                last_data = time.monotonic()
            now = time.monotonic()
            self._emit_lines(buf, partial=bool(buf) and now - last_data >= _PARTIAL_FLUSH_S)
            if proc.poll() is not None:
                exited_at = exited_at or now
                if now - exited_at >= _EXIT_GRACE_S and chunks.empty():
                    break
        self._emit_lines(buf, partial=True)
        code = proc.wait()
        self.finished_code.emit(code, time.monotonic() - t0)

    def _emit_lines(self, buf: bytearray, partial: bool) -> None:
        """Hand over complete lines; ``\\r`` ends a line too (progress bars)."""
        cut = max(buf.rfind(b"\n"), buf.rfind(b"\r"))
        if partial:
            cut = len(buf) - 1
        if cut < 0:
            return
        text = bytes(buf[:cut + 1]).decode("utf-8", errors="replace")
        del buf[:cut + 1]
        lines = [_ANSI.sub("", ln).rstrip() for ln in re.split(r"[\r\n]+", text)]
        lines = [ln for ln in lines if ln]
        if lines:
            self.output.emit(lines)
