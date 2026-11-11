"""
core/ssh_reader.py — SSH transport (optional, needs ``paramiko``).

Opens an SSH session to a remote host and runs a command (default
``cat /dev/ttyUSB0``), streaming its stdout/stderr into the terminal and
forwarding what you type to the command's stdin. Typical uses:

  * stream a remote device's own serial console — ``cat /dev/ttyACM0`` on a
    Raspberry Pi / BeagleBone / Linux SBC,
  * follow logs remotely — ``tail -f /var/log/syslog``, ``dmesg -w``,
  * bridge a device attached to a remote lab machine.

``paramiko`` is a declared runtime dependency (bundled in the release binaries),
but it pulls in ``cryptography``, so it is still imported lazily inside
:meth:`run` — the app never pays that import cost unless SSH is actually used,
and a from-source checkout without it gets a friendly ``error_occurred`` asking
for ``pip install paramiko`` instead of crashing.

It emits the same signals as :class:`core.stream_reader.StreamReader` and offers
the same ``configure`` / ``connect_port`` / ``disconnect_port`` / ``send`` /
``isRunning`` surface, so :class:`SerialController` can drive it identically —
but it is a plain :class:`QThread` (not a serial-like object), so it carries its
own read loop rather than inheriting one.
"""
from __future__ import annotations

import time

from PyQt6.QtCore import QThread, pyqtSignal

from core.stream_reader import LineSplitter, StreamReader, deliver

DEFAULT_COMMAND = "cat /dev/ttyUSB0"
_FLUSH_TIMEOUT = 0.25


class SSHReader(QThread):
    """Viewer of a remote command's output over SSH."""

    line_received  = pyqtSignal(str, str)   # (line, timestamp)
    lines_received = pyqtSignal(object)     # [(line, ts, arrival, raw bytes), …] per read
    error_occurred = pyqtSignal(str)
    connected      = pyqtSignal(str, int)   # (label, 0)
    disconnected   = pyqtSignal()

    shareable = True

    def __init__(self, parent=None):
        super().__init__(parent)
        self.taps: tuple = ()      # see StreamReader.taps
        self._running = False
        self._disconnected_emitted = False
        self.lost = False          # link dropped after connecting (see StreamReader)
        self._client = None
        self._chan = None
        self._host = ""
        self._port = 22
        self._user = ""
        self._password = ""
        self._key_path = ""
        self._command = DEFAULT_COMMAND

    # ── Configuration (call before connect) ───────────────────────────────────

    def configure(
        self,
        host: str,
        port: int = 22,
        username: str = "",
        password: str = "",
        key_path: str = "",
        command: str = DEFAULT_COMMAND,
    ):
        self._host = host
        self._port = int(port or 22)
        self._user = username
        self._password = password
        self._key_path = key_path
        self._command = command or DEFAULT_COMMAND

    # ── Connect / disconnect (async — the SSH handshake blocks) ────────────────

    def connect_port(self) -> str | None:
        if not self._host:
            return "No SSH host set"
        self._disconnected_emitted = False
        self.lost = False
        self._running = True
        self.start()   # handshake + read loop run in run()
        return None

    def disconnect_port(self):
        self._running = False
        self.wait(3000)
        self._cleanup()
        if not self._disconnected_emitted:
            self.disconnected.emit()
        self._disconnected_emitted = False

    def send(self, data: bytes) -> str | None:
        if self._chan is None:
            return "Not connected"
        try:
            self._chan.send(data)
            return None
        except Exception as e:
            return str(e)

    # ── Thread body ────────────────────────────────────────────────────────────

    def run(self):
        try:
            import paramiko
        except ImportError:
            self.error_occurred.emit(
                "SSH transport needs the 'paramiko' package.\n"
                "  Install it:  pip install paramiko"
            )
            self._finish_abnormal()
            return

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            kwargs = dict(
                hostname=self._host,
                port=self._port,
                username=self._user or None,
                timeout=10,
                allow_agent=True,
                look_for_keys=not self._password,   # fall back to keys/agent
            )
            if self._password:
                kwargs["password"] = self._password
            if self._key_path:
                kwargs["key_filename"] = self._key_path
            client.connect(**kwargs)
        except Exception as e:
            self.error_occurred.emit(f"SSH connect failed: {e}")
            try:
                client.close()
            except Exception:
                pass
            self._finish_abnormal()
            return

        self._client = client
        try:
            chan = client.get_transport().open_session()
            chan.get_pty()
            chan.exec_command(self._command)
        except Exception as e:
            self.error_occurred.emit(f"SSH channel failed: {e}")
            self._cleanup()
            self._finish_abnormal()
            return

        self._chan = chan
        self.connected.emit(f"ssh://{self._user}@{self._host}", 0)
        self._read_loop(chan)

        # Loop returned: remote EOF/close (abnormal) or user stop.
        self._cleanup()
        if self._running:
            self._running = False
            self.lost = True
            self._disconnected_emitted = True
            self.disconnected.emit()

    def _read_loop(self, chan):
        chan.settimeout(0.0)   # non-blocking recv
        lines = LineSplitter(StreamReader.MAX_LINE_BYTES)
        while self._running:
            got = False
            try:
                if chan.recv_ready():
                    data = chan.recv(65536)
                    if data == b"":
                        break            # remote closed stdout
                    for tap in self.taps:
                        tap(data)
                    deliver(self, lines.feed(data))
                    got = True
                if chan.recv_stderr_ready():
                    err = chan.recv_stderr(65536)
                    if err:
                        for tap in self.taps:
                            tap(err)
                        deliver(self, lines.feed(err))
                        got = True
                if chan.exit_status_ready() and not chan.recv_ready():
                    break                # command exited, drained
            except Exception:
                break

            deliver(self, lines.flush_if_idle(_FLUSH_TIMEOUT))

            if not got:
                time.sleep(0.02)

    @staticmethod
    def set_lines(dtr=None, rts=None) -> str | None:   # pylint: disable=unused-argument
        return "DTR/RTS are not available over SSH"

    @staticmethod
    def set_break(on: bool) -> str | None:   # pylint: disable=unused-argument
        return "BREAK is not available over SSH"

    # ── Helpers ─────────────────────────────────────────────────────────────────

    def _finish_abnormal(self):
        self._running = False
        self._disconnected_emitted = True
        self.disconnected.emit()

    def _cleanup(self):
        try:
            if self._chan is not None:
                self._chan.close()
        except Exception:
            pass
        try:
            if self._client is not None:
                self._client.close()
        except Exception:
            pass
        self._chan = None
        self._client = None
