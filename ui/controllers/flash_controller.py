"""FlashController — build & flash, then resume (CMP12).

Per connection: release the port → run the command with its output in that
connection's terminal → reconnect if it was connected. Runs on background
connections too. The process itself is :class:`core.flash_runner.FlashRunner`.
"""
from __future__ import annotations

import os

from PyQt6.QtCore import QObject

import ui.main_window as _win   # colour globals (runtime access only)
from core.flash_runner import FlashRunner, expand_command
from core.i18n import tr
from core.serial_reader import SerialReader
from ui.log_colorizer_dialog import match_log_color


class FlashController(QObject):
    def __init__(self, mw):
        super().__init__(mw)
        self._mw = mw

    def wire_panel(self, conn) -> None:
        conn.flash_panel.run_requested.connect(lambda c=conn: self.run(c))
        conn.flash_panel.stop_requested.connect(lambda c=conn: self.stop(c))

    # ── Placeholders ──────────────────────────────────────────────────────────

    def _serial_target(self, conn) -> tuple[str, int | None]:
        """``(port, baud)`` for this connection; an empty port if it has no
        serial port (a Network/SSH transport, or nothing plugged in)."""
        mw = self._mw
        reader = conn.reader
        if reader.isRunning() or conn.reconnecting:      # what it really is on
            if isinstance(reader, SerialReader):
                return reader._port, reader._baud
            return "", None
        # Not connected: whatever its part of the toolbar is set to.
        if conn is mw._active_conn:
            tb = {"transport": mw._transport_combo.currentText(),
                  "target": mw._port_combo.currentText(),
                  "baud": mw._baud_combo.currentText()}
        else:
            tb = conn.toolbar
        if tb.get("transport", "Serial") != "Serial":
            return "", None
        port = tb.get("target", "")
        if port == "No ports found":
            port = ""
        baud = str(tb.get("baud", ""))
        return port, int(baud) if baud.isdigit() else None

    # ── Run / stop ────────────────────────────────────────────────────────────

    def run(self, conn=None) -> None:
        mw = self._mw
        conn = conn or mw._active_conn
        panel = conn.flash_panel
        if conn.flash_runner is not None:
            mw._log("SYS", "Already flashing on this connection.", _win.C_SYS, conn=conn)
            return
        if conn.transfer is not None:
            self._refuse(conn, "A file is being sent over this connection: cancel it first.")
            return
        template = panel.command()
        if not template:
            if conn is mw._active_conn:
                mw._show_tool("flash")
            panel.focus_command()
            panel.set_status(tr("Enter the command to run first."))
            return
        cwd = panel.cwd()
        if cwd and not os.path.isdir(os.path.expanduser(cwd)):
            self._refuse(conn, f"Project folder not found: {cwd}")
            return
        port, baud = self._serial_target(conn)
        if not port and "{port}" in template:
            self._refuse(conn, "The command uses {port}, but this connection has no serial port.")
            return
        if baud is None and "{baud}" in template:
            self._refuse(conn, "The command uses {baud}, but this connection has no baud rate.")
            return
        command = expand_command(template, port, baud)

        # Release the port: the flashing tool can't open it while we hold it.
        conn.resume_after_flash = conn.reader.isRunning() or conn.reconnecting
        mw._serial.stop_reconnect(conn, quiet=True)
        if conn.reader.isRunning():
            conn.reader.disconnect_port()

        runner = FlashRunner(command, os.path.expanduser(cwd), parent=conn)
        runner.output.connect(lambda lines, c=conn: self._on_output(c, lines))
        runner.finished_code.connect(lambda code, secs, c=conn: self._on_done(c, code, secs))
        conn.flash_runner = runner
        panel.set_running(True)
        panel.set_status(tr("Running…"))
        mw._log("SYS", f"▶ {command}", _win.C_SYS, conn=conn)
        self._refresh(conn)
        runner.start()

    def stop(self, conn=None) -> None:
        conn = conn or self._mw._active_conn
        if conn.flash_runner is not None:
            self._mw._log("SYS", "Stopping…", _win.C_SYS, conn=conn)
            conn.flash_runner.stop()

    def shutdown(self, conn) -> None:
        """The connection or the window is closing: don't leave a tool running."""
        runner = conn.flash_runner
        if runner is None:
            return
        conn.flash_runner = None         # _on_done: nothing left to update
        conn.resume_after_flash = False
        runner.stop()
        runner.wait(5000)

    # ── Results ───────────────────────────────────────────────────────────────

    def _refuse(self, conn, message: str) -> None:
        self._mw._log("ERR", message, _win.C_ERR, conn=conn)
        conn.flash_panel.set_status(message)

    def _on_output(self, conn, lines: list) -> None:
        mw = self._mw
        for line in lines:
            mw._log("BLD", line, _win.C_SYS, conn=conn,
                    text_color=match_log_color(line, mw._log_colorizer_enabled))

    def _on_done(self, conn, code: int, secs: float) -> None:
        mw = self._mw
        runner, conn.flash_runner = conn.flash_runner, None
        if runner is None:               # shut down; its panel may be gone
            return
        runner.deleteLater()
        panel = conn.flash_panel
        panel.set_running(False)
        if runner.stopped:
            text, color = f"Stopped after {secs:.1f} s", _win.C_SYS
        elif code == 0:
            text, color = f"Finished OK in {secs:.1f} s", _win.C_OK
        else:
            text, color = f"Failed with exit code {code} after {secs:.1f} s", _win.C_ERR
        mw._log("SYS", text, color, conn=conn)
        panel.set_status(text)
        if conn.resume_after_flash:
            mw._serial.resume(conn)
        self._refresh(conn)

    def _refresh(self, conn) -> None:
        mw = self._mw
        mw._update_conn_tab(conn)
        if conn is mw._active_conn:
            mw._refresh_conn_state()
