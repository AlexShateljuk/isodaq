"""SerialController — serial connection control, TX, ports and command history.

Extracted from MainWindow (OSS6). Owns the connect/disconnect lifecycle, sending
commands (with EOL + TX byte accounting), the port rescan, the connection-state
button/statusbar updates, and the command-history (↑/↓) on the command field —
it installs its own event filter on that field.

**Not** here: the RX pipeline. ``MainWindow._on_line_received`` stays in the shell
because it is woven into line-identity (F2) and the trigger fast path; this
controller only handles outgoing traffic and connection state.

Colour globals live in ``ui.main_window`` and are re-bound on theme change, so
they are read through the module at call time (``_win.C_ERR``).
"""
from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, Qt, pyqtSlot

import ui.main_window as _win   # colour globals (runtime access only)
from core.i18n import tr
from core.serial_reader import SerialReader


class SerialController(QObject):
    """Connect/disconnect, send, port rescan and command history."""

    def __init__(self, mw):
        super().__init__(mw)
        self._mw = mw
        self._cmd_history: list[str] = []
        self._hist_idx: int = 0

    def wire(self) -> None:
        """Connect the SerialReader connection-state signals to this controller.
        (line_received stays wired to MainWindow's RX pipeline.)"""
        r = self._mw._reader
        r.error_occurred.connect(self.on_serial_error)
        r.connected.connect(self.on_connected)
        r.disconnected.connect(self.on_disconnected)

    # ── Connection ──────────────────────────────────────────────────────────────

    def toggle_connection(self) -> None:
        """Connect or disconnect the serial port, reading the UI combos first."""
        mw = self._mw
        if mw._reader.isRunning():
            mw._reader.disconnect_port()
        else:
            data_str = mw._data_combo.currentText()  # e.g. "8N1"
            bytesize = int(data_str[0])
            parity   = data_str[1]
            stopbits = int(data_str[2])
            mw._reader.configure(
                port     = mw._port_combo.currentText(),
                baud     = int(mw._baud_combo.currentText()),
                bytesize = bytesize,
                parity   = parity,
                stopbits = stopbits,
                flow     = mw._flow_combo.currentText(),
            )
            err = mw._reader.connect_port()
            if err:
                mw._log("ERR", f"Connection failed: {err}", _win.C_ERR)

    @pyqtSlot(str, int)
    def on_connected(self, port: str, baud: int) -> None:
        mw = self._mw
        mw._conn_btn.setText(tr("Disconnect"))
        mw._conn_btn.setObjectName("disconnectBtn")
        mw._repolish(mw._conn_btn)
        mw._sb_conn.setText(f"● {port} · {baud}")
        mw._sb_conn.setStyleSheet(f"color:{_win.C_OK.name()}")
        mw._log("SYS", f"Connected: {port} @ {baud}", _win.C_OK)

    @pyqtSlot()
    def on_disconnected(self) -> None:
        mw = self._mw
        mw._conn_btn.setText(tr("Connect"))
        mw._conn_btn.setObjectName("connectBtn")
        mw._repolish(mw._conn_btn)
        mw._sb_conn.setText("● " + tr("Disconnected"))
        mw._sb_conn.setStyleSheet(f"color:{_win.C_ERR.name()}")
        mw._log("SYS", "Disconnected.", _win.C_SYS)

    @pyqtSlot(str)
    def on_serial_error(self, msg: str) -> None:
        self._mw._log("ERR", msg, _win.C_ERR)

    # ── TX ──────────────────────────────────────────────────────────────────────

    def send_command(self) -> None:
        """Send the command field over the port with the selected EOL, log it,
        update the TX counter, and push it into history."""
        mw = self._mw
        text = mw._cmd_edit.text().strip()
        if not text:
            return
        self._cmd_history.append(text)
        self._hist_idx = len(self._cmd_history)

        eol_map = {"\\r\\n": b"\r\n", "\\n": b"\n", "\\r": b"\r", "None": b""}
        eol = eol_map.get(mw._eol_combo.currentText(), b"\r\n")
        data = text.encode() + eol

        err = mw._reader.send(data)
        if err:
            mw._log("ERR", err, _win.C_ERR)
        else:
            mw._tx_bytes += len(data)
            mw._sb_tx.setText(f"TX: {mw._fmt_bytes(mw._tx_bytes)}")
            mw._log("TX", text, _win.C_TX)
            mw._session.feed_line(text, "tx")

        mw._cmd_edit.clear()

    def send_custom(self) -> None:
        text = self._mw._custom_cmd.toPlainText().strip()
        if text:
            self._mw._cmd_edit.setText(text)
            self.send_command()

    def set_cmd(self, cmd: str) -> None:
        self._mw._cmd_edit.setText(cmd)
        self._mw._cmd_edit.setFocus()

    # ── Ports ───────────────────────────────────────────────────────────────────

    def refresh_ports(self) -> None:
        """Rescan available COM/tty ports and repopulate the port combo-box."""
        mw = self._mw
        ports = SerialReader.list_ports()
        current = mw._port_combo.currentText()
        mw._port_combo.clear()
        mw._port_combo.addItems(ports or ["No ports found"])
        # Priority: currently selected → restored from config → first available
        restore = getattr(mw, "_restore_port", "")
        prefer = restore if restore in ports else (current if current in ports else "")
        if prefer:
            mw._port_combo.setCurrentText(prefer)
        mw._log("SYS", f"Ports: {', '.join(ports) if ports else 'none found'}", _win.C_DIM)

    # ── Command history (↑/↓ on the command field) ───────────────────────────────

    def eventFilter(self, obj, event):
        mw = self._mw
        if obj is mw._cmd_edit and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Up and self._cmd_history:
                self._hist_idx = max(0, self._hist_idx - 1)
                mw._cmd_edit.setText(self._cmd_history[self._hist_idx])
                return True
            if key == Qt.Key.Key_Down:
                self._hist_idx = min(len(self._cmd_history), self._hist_idx + 1)
                mw._cmd_edit.setText(
                    self._cmd_history[self._hist_idx] if self._hist_idx < len(self._cmd_history) else "")
                return True
        return super().eventFilter(obj, event)
