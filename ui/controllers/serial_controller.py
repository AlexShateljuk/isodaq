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
from core.net_reader import NetworkReader
from core.serial_reader import SerialReader
from core.ssh_reader import SSHReader


class SerialController(QObject):
    """Connect/disconnect, send, port rescan and command history."""

    def __init__(self, mw):
        super().__init__(mw)
        self._mw = mw
        self._cmd_history: list[str] = []
        self._hist_idx: int = 0
        self._readers: dict[str, object] = {}   # transport kind → reader instance
        self._ssh_pw_cache: dict[str, str] = {}  # session name → password (this run only)

    def wire(self) -> None:
        """Connect the initial (serial) reader's connection-state signals.
        (line_received stays wired to MainWindow's RX pipeline.) Network/SSH
        readers are created and wired lazily in :meth:`_reader_for`."""
        self._readers["Serial"] = self._mw._reader
        r = self._mw._reader
        r.error_occurred.connect(self.on_serial_error)
        r.connected.connect(self.on_connected)
        r.disconnected.connect(self.on_disconnected)

    # ── Connection ──────────────────────────────────────────────────────────────

    def _reader_for(self, kind: str):
        """Get (or lazily create + wire) the reader for a transport kind.

        Readers are cached per kind so switching transports doesn't leak threads
        and doesn't re-wire signals. The active one is mirrored to ``mw._reader``
        so send/isRunning/disconnect and the macro runner all follow the swap."""
        r = self._readers.get(kind)
        if r is None:
            mw = self._mw
            r = {"Network": NetworkReader, "SSH": SSHReader}.get(kind, SerialReader)(mw)
            r.line_received.connect(mw._on_line_received)
            r.error_occurred.connect(self.on_serial_error)
            r.connected.connect(self.on_connected)
            r.disconnected.connect(self.on_disconnected)
            self._readers[kind] = r
        return r

    def toggle_connection(self) -> None:
        """Connect or disconnect using the selected transport."""
        mw = self._mw
        if mw._reader.isRunning():
            mw._reader.disconnect_port()
            return

        kind = mw._transport_combo.currentText()
        reader = self._reader_for(kind)

        data_str = mw._data_combo.currentText()   # e.g. "8N1"
        bytesize, parity, stopbits = int(data_str[0]), data_str[1], int(data_str[2])

        if kind == "Network":
            url = mw._port_combo.currentText().strip()
            if not url:
                mw._log("ERR", "Enter a network target, e.g. socket://192.168.1.50:23",
                        _win.C_ERR)
                return
            mw._net_url = url
            reader.configure(url=url, baud=int(mw._baud_combo.currentText()),
                             bytesize=bytesize, parity=parity, stopbits=stopbits,
                             flow=mw._flow_combo.currentText())
        elif kind == "SSH":
            if not mw._ssh_params.get("host"):
                if not self.edit_ssh_session(new=True):
                    return
            p = mw._ssh_params
            # Prompt once for a password if this session is password-auth and we
            # have none cached (blank falls back to key/agent).
            if not p.get("key_path") and not p.get("password"):
                pw = self._prompt_ssh_password(p)
                if pw is None:
                    return
                p["password"] = pw
                if p.get("name"):
                    self._ssh_pw_cache[p["name"]] = pw
            reader.configure(host=p["host"], port=p.get("port", 22),
                             username=p.get("username", ""), password=p.get("password", ""),
                             key_path=p.get("key_path", ""),
                             command=p.get("command", "cat /dev/ttyUSB0"))
        else:  # Serial
            reader.configure(port=mw._port_combo.currentText(),
                             baud=int(mw._baud_combo.currentText()),
                             bytesize=bytesize, parity=parity, stopbits=stopbits,
                             flow=mw._flow_combo.currentText())

        mw._reader = reader
        err = reader.connect_port()
        if err:
            mw._log("ERR", f"Connection failed: {err}", _win.C_ERR)

    @pyqtSlot(str, int)
    def on_connected(self, port: str, baud: int) -> None:
        mw = self._mw
        label = f"{port} · {baud}" if baud else port
        mw._conn_btn.setText(tr("Disconnect"))
        mw._conn_btn.setObjectName("disconnectBtn")
        mw._repolish(mw._conn_btn)
        mw._sb_conn.setText(f"● {label}")
        mw._sb_conn.setStyleSheet(f"color:{_win.C_OK.name()}")
        mw._log("SYS", f"Connected: {label}", _win.C_OK)

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

    # ── SSH saved sessions ─────────────────────────────────────────────────────

    @staticmethod
    def _ssh_label(s: dict) -> str:
        """Human label for a saved session (name, else user@host)."""
        who = f"{s.get('username', '')}@{s.get('host', '')}".strip("@")
        name = (s.get("name") or "").strip()
        return f"{name} — {who}" if name else (who or "ssh session")

    def populate_ssh_combo(self) -> None:
        """Fill the target combo with saved sessions + a trailing '＋ Add' entry,
        and select the active one. Programmatic — uses blockSignals so it doesn't
        re-trigger selection handling."""
        mw = self._mw
        combo = mw._port_combo
        combo.blockSignals(True)
        combo.clear()
        for s in mw._ssh_sessions:
            combo.addItem(self._ssh_label(s))
        combo.addItem(tr("＋ Add SSH session…"))

        active = mw._ssh_params.get("name", "")
        idx = next((i for i, s in enumerate(mw._ssh_sessions)
                    if s.get("name") == active), -1)
        if idx < 0 and mw._ssh_sessions:
            idx = 0
            self._activate_ssh(0)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        combo.blockSignals(False)

    def handle_ssh_activation(self, index: int) -> None:
        """User picked a combo entry: a saved session, or the ＋ Add entry."""
        mw = self._mw
        if index >= len(mw._ssh_sessions):        # the ＋ Add entry
            self.edit_ssh_session(new=True)
        else:
            self._activate_ssh(index)

    def _activate_ssh(self, index: int) -> None:
        """Make saved session *index* the active one (password from run cache)."""
        mw = self._mw
        s = dict(mw._ssh_sessions[index])
        s["password"] = self._ssh_pw_cache.get(s.get("name", ""), "")
        mw._ssh_params = s

    def edit_ssh_session(self, new: bool = False, index: int | None = None) -> bool:
        """Add or edit a saved SSH session. Passwords are cached for the current
        run only, never written to disk. Returns True if a session was saved and
        made active (ready to connect), False on cancel/delete."""
        from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout,
                                     QHBoxLayout, QLineEdit, QPushButton,
                                     QSpinBox, QWidget)

        from ui.themes import tint_titlebar

        mw = self._mw
        if not new:
            if index is None:
                index = mw._port_combo.currentIndex()
            if index is None or index < 0 or index >= len(mw._ssh_sessions):
                new = True
        base = {} if new else dict(mw._ssh_sessions[index])
        cached_pw = self._ssh_pw_cache.get(base.get("name", ""), base.get("password", ""))

        dlg = QDialog(mw)
        tint_titlebar(dlg)
        dlg.setWindowTitle(tr("Add SSH session") if new else tr("Edit SSH session"))
        dlg.setMinimumWidth(440)
        dlg.setModal(True)

        form = QFormLayout(dlg)
        form.setContentsMargins(16, 16, 16, 12)
        form.setSpacing(10)

        name_edit = QLineEdit(base.get("name", ""))
        name_edit.setPlaceholderText(tr("optional — e.g. “Lab Pi”"))
        host_edit = QLineEdit(base.get("host", ""))
        host_edit.setPlaceholderText("192.168.1.20")
        port_spin = QSpinBox()
        port_spin.setRange(1, 65535)
        port_spin.setValue(int(base.get("port", 22) or 22))
        user_edit = QLineEdit(base.get("username", ""))
        user_edit.setPlaceholderText("pi")
        pass_edit = QLineEdit(cached_pw)
        pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        pass_edit.setPlaceholderText(tr("not saved to disk — blank uses a key/agent"))

        key_edit = QLineEdit(base.get("key_path", ""))
        key_edit.setPlaceholderText("~/.ssh/id_ed25519")
        key_row = QWidget()
        key_lay = QHBoxLayout(key_row)
        key_lay.setContentsMargins(0, 0, 0, 0)
        key_lay.setSpacing(6)
        key_lay.addWidget(key_edit)
        browse = QPushButton(tr("Browse…"))
        browse.setFixedWidth(90)
        browse.clicked.connect(lambda: self._browse_key(dlg, key_edit))
        key_lay.addWidget(browse)

        cmd_edit = QLineEdit(base.get("command", "cat /dev/ttyUSB0"))
        cmd_edit.setPlaceholderText("cat /dev/ttyUSB0   ·   tail -f /var/log/syslog")

        form.addRow(tr("Name"), name_edit)
        form.addRow(tr("Host"), host_edit)
        form.addRow(tr("Port"), port_spin)
        form.addRow(tr("Username"), user_edit)
        form.addRow(tr("Password"), pass_edit)
        form.addRow(tr("Private key"), key_row)
        form.addRow(tr("Remote command"), cmd_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        delete_btn = None
        if not new:
            delete_btn = buttons.addButton(tr("Delete"),
                                           QDialogButtonBox.ButtonRole.DestructiveRole)
            delete_btn.clicked.connect(lambda: dlg.done(2))   # custom "delete" code
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)

        result = dlg.exec()
        if result == 2 and not new:                       # Delete
            self._delete_ssh_session(index)
            return False
        if result != QDialog.DialogCode.Accepted:
            return False

        host = host_edit.text().strip()
        if not host:
            mw._log("ERR", "SSH host is required", _win.C_ERR)
            return False

        name = name_edit.text().strip() or f"{user_edit.text().strip()}@{host}".strip("@")
        session = {
            "name":     name,
            "host":     host,
            "port":     port_spin.value(),
            "username": user_edit.text().strip(),
            "key_path": key_edit.text().strip(),
            "command":  cmd_edit.text().strip() or "cat /dev/ttyUSB0",
        }
        if new:
            mw._ssh_sessions.append(session)
        else:
            mw._ssh_sessions[index] = session
        self._ssh_pw_cache[name] = pass_edit.text()        # in-memory only

        # Make it active and reflect in the combo.
        mw._ssh_params = dict(session, password=pass_edit.text())
        if mw._transport_combo.currentText() == "SSH":
            self.populate_ssh_combo()
        return True

    def delete_ssh_session(self, index: int) -> None:
        """Delete a saved session directly from the list, after confirmation."""
        from PyQt6.QtWidgets import QMessageBox
        mw = self._mw
        if index < 0 or index >= len(mw._ssh_sessions):
            return
        label = self._ssh_label(mw._ssh_sessions[index])
        if QMessageBox.question(
                mw, tr("Delete SSH session"),
                tr("Delete “{name}”?").format(name=label)
        ) != QMessageBox.StandardButton.Yes:
            return
        self._delete_ssh_session(index)

    def _delete_ssh_session(self, index: int) -> None:
        mw = self._mw
        removed = mw._ssh_sessions.pop(index)
        self._ssh_pw_cache.pop(removed.get("name", ""), None)
        if mw._ssh_params.get("name") == removed.get("name"):
            mw._ssh_params = {}
        if mw._transport_combo.currentText() == "SSH":
            self.populate_ssh_combo()

    def _prompt_ssh_password(self, p: dict) -> str | None:
        """Ask for a password once (blank = use key/agent). None on cancel."""
        from PyQt6.QtWidgets import QInputDialog, QLineEdit
        who = f"{p.get('username', '')}@{p.get('host', '')}".strip("@")
        pw, ok = QInputDialog.getText(
            self._mw, tr("SSH password"),
            tr("Password for {who} (blank uses a key/agent):").format(who=who),
            QLineEdit.EchoMode.Password)
        return pw if ok else None

    @staticmethod
    def _browse_key(parent, line_edit) -> None:
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(parent, tr("Select private key"))
        if path:
            line_edit.setText(path)

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
