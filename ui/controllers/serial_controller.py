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

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer

import ui.main_window as _win   # colour globals (runtime access only)
from core.i18n import tr
from core.net_reader import NetworkReader
from core.serial_reader import SerialReader
from core.ssh_reader import SSHReader


#: POSIX termios rates; anything else needs the driver's custom-baud ioctl.
STANDARD_BAUDS = {50, 75, 110, 134, 150, 200, 300, 600, 1200, 1800, 2400, 4800,
                  9600, 19200, 38400, 57600, 115200, 230400, 460800, 500000,
                  576000, 921600, 1000000, 1152000, 1500000, 2000000, 2500000,
                  3000000, 3500000, 4000000}


class SerialController(QObject):
    """Connect/disconnect, send, port rescan and command history."""

    def __init__(self, mw):
        super().__init__(mw)
        self._mw = mw
        self._cmd_history: list[str] = []
        self._hist_idx: int = 0
        self._ssh_pw_cache: dict[str, str] = {}  # session name → password (this run only)
        # Auto-reconnect (CMP1): one 1 s tick serves every waiting connection.
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setInterval(1000)
        self._reconnect_timer.timeout.connect(self._reconnect_tick)

    def wire_reader(self, conn, reader) -> None:
        """Wire a reader's signals, capturing its owning *conn* so RX lines and
        connection-state updates route to that connection (not whichever is
        active)."""
        mw = self._mw
        # Batched: one queued signal per read, not per line (see stream_reader.deliver).
        reader.lines_received.connect(
            lambda batch, c=conn: mw._on_conn_lines(c, batch))
        reader.error_occurred.connect(lambda msg, c=conn: self.on_serial_error(c, msg))
        reader.connected.connect(lambda label, baud, c=conn: self.on_connected(c, label, baud))
        reader.disconnected.connect(lambda c=conn: self.on_disconnected(c))

    def wire(self) -> None:
        """Wire the active connection's default (serial) reader. Network/SSH
        readers are created and wired lazily in :meth:`_reader_for`; the per-kind
        reader cache lives on the Connection."""
        conn = self._mw._active_conn
        self.wire_reader(conn, conn.reader)

    # ── Connection ──────────────────────────────────────────────────────────────

    def _reader_for(self, kind: str):
        """Get (or lazily create + wire) the reader for a transport kind, cached
        on the active Connection so switching transports within a connection
        doesn't leak threads or re-wire signals. The chosen one becomes the
        connection's active reader (``mw._reader``), so send/isRunning/disconnect
        and the macro runner all follow the swap."""
        conn = self._mw._active_conn
        r = conn.readers.get(kind)
        if r is None:
            r = {"Network": NetworkReader, "SSH": SSHReader}.get(kind, SerialReader)(conn)
            self.wire_reader(conn, r)
            conn.readers[kind] = r
        return r

    def toggle_connection(self) -> None:
        """Connect or disconnect using the selected transport (or stop waiting
        for a lost link to come back)."""
        mw = self._mw
        if mw._active_conn.reconnecting:
            self.stop_reconnect(mw._active_conn)
            return
        if mw._reader.isRunning():
            mw._reader.disconnect_port()
            return

        kind = mw._transport_combo.currentText()
        reader = self._reader_for(kind)

        baud = self.baud()
        if baud is None:
            return
        data_str = mw._data_combo.currentText()   # e.g. "8N1"
        bytesize, parity, stopbits = int(data_str[0]), data_str[1], int(data_str[2])

        if kind == "Network":
            url = mw._port_combo.currentText().strip()
            if not url:
                mw._log("ERR", "Enter a network target, e.g. socket://192.168.1.50:23",
                        _win.C_ERR)
                return
            mw._net_url = url
            reader.configure(url=url, baud=baud,
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
                             baud=baud,
                             bytesize=bytesize, parity=parity, stopbits=stopbits,
                             flow=mw._flow_combo.currentText())

        mw._active_conn.reader = reader
        reader.dtr = mw._chk_dtr.isChecked()
        reader.rts = mw._chk_rts.isChecked()
        err = reader.connect_port()
        if err:
            if kind == "Serial" and baud not in STANDARD_BAUDS and "ioctl" in err.lower():
                err += f" — this port/driver doesn't accept the custom rate {baud}"
            mw._log("ERR", f"Connection failed: {err}", _win.C_ERR)

    def baud(self) -> int | None:
        """The baud box as a number (any rate the driver accepts — 74880,
        250000, 1000000 … — CMP3), or ``None`` after logging why not."""
        mw = self._mw
        text = mw._baud_combo.currentText().strip().replace(" ", "").replace("_", "")
        try:
            baud = int(text)
        except ValueError:
            baud = 0
        if baud <= 0:
            mw._log("ERR", f"Invalid baud rate: {mw._baud_combo.currentText()!r}", _win.C_ERR)
            return None
        return baud

    def on_connected(self, conn, port: str, baud: int) -> None:
        mw = self._mw
        label = f"{port} · {baud}" if baud else port
        if conn.reconnecting:
            tries = conn.reconnect_tries
            conn.reconnecting = False
            mw._log("SYS", f"Reconnected: {label}  (after {tries} s)", _win.C_OK, conn=conn)
        else:
            mw._log("SYS", f"Connected: {label}", _win.C_OK, conn=conn)
        if isinstance(conn.reader, SerialReader):
            conn.usb_id = SerialReader.usb_id(port) or conn.usb_id
        conn.failed = False
        conn.session_sec = 0
        conn.name = port                       # tab reflects what it's attached to
        mw._update_conn_tab(conn)              # tab state dot always
        if conn is mw._active_conn:            # button/statusbar only for active
            mw._conn_btn.setText(tr("Disconnect"))
            mw._conn_btn.setObjectName("disconnectBtn")
            mw._repolish(mw._conn_btn)
            mw._sb_conn.setText(f"● {label}")
            mw._sb_conn.setStyleSheet(f"color:{_win.C_OK.name()}")

    def on_disconnected(self, conn) -> None:
        mw = self._mw
        if conn.reconnecting:
            # A failed network/SSH retry — stay quiet, the tick tries again.
            mw._update_conn_tab(conn)
            return
        if conn.reader.lost and mw._chk_reconnect.isChecked():
            self.start_reconnect(conn)
            return
        mw._log("SYS", "Disconnected.", _win.C_SYS, conn=conn)
        mw._update_conn_tab(conn)
        if conn is mw._active_conn:
            mw._conn_btn.setText(tr("Connect"))
            mw._conn_btn.setObjectName("connectBtn")
            mw._repolish(mw._conn_btn)
            mw._refresh_conn_state()

    def on_serial_error(self, conn, msg: str) -> None:
        # Any transport error marks the connection failed (refused / link lost)
        # until it next connects; a clean user disconnect leaves it idle.
        if conn.reconnecting:
            return       # retries are expected to fail until the device is back
        conn.errors += 1
        conn.failed = True
        self._mw._log("ERR", msg, _win.C_ERR, conn=conn)
        self._mw._update_conn_tab(conn)
        if conn is self._mw._active_conn:
            self._mw._refresh_conn_state()

    # ── Auto-reconnect (CMP1) ──────────────────────────────────────────────────

    def _target(self, conn) -> str:
        r = conn.reader
        return getattr(r, "_port", "") if isinstance(r, SerialReader) else conn.name

    def start_reconnect(self, conn) -> None:
        """The link dropped: keep the connection's settings and retry every
        second until the device is back (re-plugged, rebooted, host up)."""
        mw = self._mw
        conn.reconnecting = True
        conn.reconnect_tries = 0
        mw._log("SYS", f"Link lost — waiting for {self._target(conn)} to come back "
                       f"(auto-reconnect; click Stop to give up)…", _win.C_SYS, conn=conn)
        self._reconnect_timer.start()
        mw._update_conn_tab(conn)
        if conn is mw._active_conn:
            mw._refresh_conn_state()

    def stop_reconnect(self, conn, quiet: bool = False) -> None:
        mw = self._mw
        if not conn.reconnecting:
            return
        conn.reconnecting = False
        if conn.reader.isRunning():          # a network/SSH attempt in flight
            conn.reader.disconnect_port()
        if not quiet:
            mw._log("SYS", "Auto-reconnect stopped.", _win.C_SYS, conn=conn)
        mw._update_conn_tab(conn)
        if conn is mw._active_conn:
            mw._refresh_conn_state()

    def _reconnect_tick(self) -> None:
        waiting = [c for c in self._mw._connections if c.reconnecting]
        if not waiting:
            self._reconnect_timer.stop()
            return
        for conn in waiting:
            conn.reconnect_tries += 1
            r = conn.reader
            if r.isRunning():                # attempt still in progress
                continue
            if isinstance(r, SerialReader):
                port = SerialReader.find_port(r._port, conn.usb_id)
                if port is None:
                    continue                 # not plugged in yet
                if port != r._port:
                    self._mw._log("SYS", f"{r._port} came back as {port}",
                                  _win.C_SYS, conn=conn)
                    r._port = port
                # A freshly enumerated port can refuse the first open (busy /
                # permission settling) — just try again next tick.
                r.connect_port()
            elif conn.reconnect_tries % 3 == 0:  # network / SSH: every 3 s
                r.connect_port()
        if self._mw._active_conn.reconnecting:
            self._mw._refresh_conn_state()      # status-bar "waiting … N s"

    # ── DTR / RTS / BREAK and reset sequences (CMP2) ───────────────────────────

    #: (DTR, RTS, hold ms) steps; None leaves a line as it is. The ESP sequences
    #: are esptool's classic ones for the usual two-transistor auto-reset
    #: circuit (RTS → EN, DTR → GPIO0).
    RESET_SEQUENCES = {
        "dtr":     [(False, None, 100), (True, None, 0)],             # Arduino-style
        "esp":     [(False, True, 100), (False, False, 0)],           # EN pulse
        "esp_boot": [(False, True, 100), (True, False, 50), (False, False, 0)],
    }

    def set_line(self, name: str, on: bool) -> None:
        """DTR / RTS checkbox toggled — apply now if connected."""
        mw = self._mw
        err = mw._reader.set_lines(**{name: on})
        if err:
            mw._log("ERR", err, _win.C_ERR)

    def send_break(self, ms: int = 250) -> None:
        mw = self._mw
        reader = mw._reader
        err = reader.set_break(True)
        if err:
            mw._log("ERR", err, _win.C_ERR)
            return
        QTimer.singleShot(ms, lambda: reader.set_break(False))
        mw._log("SYS", f"BREAK sent ({ms} ms)", _win.C_SYS)

    def reset_board(self, kind: str = "esp") -> None:
        """Pulse DTR/RTS to reset the board (or enter the ESP ROM bootloader),
        then restore the lines to the DTR/RTS checkboxes."""
        mw = self._mw
        reader = mw._reader
        if not reader.isRunning():
            mw._log("ERR", "Not connected", _win.C_ERR)
            return
        steps = list(self.RESET_SEQUENCES[kind])
        restore = (mw._chk_dtr.isChecked(), mw._chk_rts.isChecked())
        label = {"dtr": "Reset (DTR pulse)", "esp": "Reset (EN via RTS)",
                 "esp_boot": "Enter bootloader"}[kind]

        def step():
            if not steps:
                reader.set_lines(dtr=restore[0], rts=restore[1])
                return
            dtr, rts, hold = steps.pop(0)
            err = reader.set_lines(dtr=dtr, rts=rts)
            if err:
                mw._log("ERR", err, _win.C_ERR)
                reader.dtr, reader.rts = restore
                return
            QTimer.singleShot(hold, step)

        mw._log("SYS", f"{label} …", _win.C_SYS)
        step()

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

        data = text.encode() + self.eol()

        err = mw._reader.send(data)
        if err:
            mw._log("ERR", err, _win.C_ERR)
        else:
            mw._tx_bytes += len(data)
            mw._sb_tx.setText(f"TX: {mw._fmt_bytes(mw._tx_bytes)}")
            mw._log("TX", text, _win.C_TX)
            mw._session.feed_line(text, "tx")

        mw._cmd_edit.clear()

    def eol(self) -> bytes:
        """The line ending selected in the command bar."""
        return {"\\r\\n": b"\r\n", "\\n": b"\n", "\\r": b"\r", "None": b""}.get(
            self._mw._eol_combo.currentText(), b"\r\n")

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
        """Rescan available COM/tty ports and repopulate the port combo-box
        (each entry labelled with the OS's description of the port — CMP4)."""
        from ui.port_list import fill
        mw = self._mw
        infos = SerialReader.port_info()
        ports = [p["device"] for p in infos]
        current = mw._port_combo.currentText()
        fill(mw._port_combo, infos, "No ports found")
        view = mw._port_combo.view()
        view.setMinimumWidth(min(view.sizeHintForColumn(0) + 24, 640))
        # Priority: currently selected → restored from config → first available
        restore = getattr(mw, "_restore_port", "")
        prefer = restore if restore in ports else (current if current in ports else "")
        if prefer:
            mw._port_combo.setCurrentText(prefer)
        named = [f"{p['device']} ({p['description']})" if p["description"] else p["device"]
                 for p in infos]
        mw._log("SYS", f"Ports: {', '.join(named) if named else 'none found'}", _win.C_DIM)

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
