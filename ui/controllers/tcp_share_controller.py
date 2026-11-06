"""TcpShareController — serve a connection as a raw TCP port (CMP22).

Owns the dialog and each connection's :class:`core.port_share.PortShare`: taps the
reader, counts and shows what clients send, and stops the share when the
connection really ends. A share lives through an automatic reconnect and a flash,
since the same device is coming back.
"""
from __future__ import annotations

from PyQt6.QtCore import QObject, Qt, QTimer
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

import ui.main_window as _win   # for theme-updated colour globals (runtime access only)
from core.i18n import tr
from core.port_share import ALL_HOSTS, DEFAULT_PORT, LOCAL_HOST, PortShare
from core.stream_reader import _CONTROL_TO_DOT
from core.stun_helper import get_local_ip
from ui.number_box import NumberBox


class TcpShareController(QObject):
    def __init__(self, mw):
        super().__init__(mw)
        self._mw = mw
        self.port = DEFAULT_PORT          # last used; saved in the settings
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._show_client_input)

    # ── Start / stop ──────────────────────────────────────────────────────────

    def start(self, conn, host: str, port: int) -> str | None:
        """Share *conn* on *host*:*port*. Returns the error, if any."""
        reader = conn.reader
        if not reader.isRunning():
            return tr("Connect to the device first.")
        if not getattr(reader, "shareable", False):
            return tr("MQTT and Modbus connections aren't byte streams, so they can't be "
                      "shared this way.")
        share = PortShare(lambda data, c=conn: c.reader.send(data), self)
        err = share.start(host, port)
        if err:
            share.deleteLater()
            return err
        share.clients_changed.connect(self._on_clients, Qt.ConnectionType.QueuedConnection)
        share.client_dropped.connect(lambda label, c=conn: self._on_dropped(c, label),
                                     Qt.ConnectionType.QueuedConnection)
        reader.tap = share.feed
        conn.port_share = share
        self.port = share.port
        self._timer.start()
        self._mw._log("SYS", tr("Sharing this connection on TCP: {address}").format(
            address=self.address(share)), _win.C_OK, conn=conn)
        self._mw._refresh_activity()
        return None

    def stop(self, conn, why: str = "") -> None:
        share = conn.port_share
        if share is None:
            return
        conn.port_share = None
        for reader in conn.readers.values():
            if getattr(reader, "tap", None) == share.feed:
                reader.tap = None
        share.stop()
        self._flush_input(conn, share)
        share.deleteLater()
        if not any(c.port_share for c in self._mw._connections):
            self._timer.stop()
        self._mw._log("SYS", why or tr("TCP sharing stopped."), _win.C_SYS, conn=conn)
        self._mw._refresh_activity()

    def on_link_closed(self, conn) -> None:
        """The connection ended for good: nothing is left to share."""
        if conn.port_share is not None and not conn.resume_after_flash:
            self.stop(conn, tr("TCP sharing stopped: the connection closed."))

    @staticmethod
    def address(share: PortShare) -> str:
        host = get_local_ip() if share.host == ALL_HOSTS else share.host
        return f"socket://{host}:{share.port}"

    # ── Activity ──────────────────────────────────────────────────────────────

    def _on_clients(self, _count: int) -> None:
        self._mw._refresh_activity()

    def _on_dropped(self, conn, label: str) -> None:
        self._mw._log("ERR", tr("TCP client {client} was too slow and has been "
                                "disconnected.").format(client=label), _win.C_ERR, conn=conn)

    def _show_client_input(self) -> None:
        for conn in self._mw._connections:
            if conn.port_share is not None:
                self._flush_input(conn, conn.port_share)

    def _flush_input(self, conn, share: PortShare) -> None:
        mw = self._mw
        count, preview, err = share.take_sent()
        if err:
            mw._log("ERR", tr("TCP client's data not sent: {err}").format(err=err),
                    _win.C_ERR, conn=conn)
        if not count:
            return
        conn.tx_bytes += count
        if conn is mw._active_conn:
            mw._sb_tx.setText(f"TX: {mw._fmt_bytes(conn.tx_bytes)}")
        shown = preview.decode("utf-8", errors="replace").rstrip("\r\n").translate(_CONTROL_TO_DOT)
        if count > len(preview):
            shown += f"… ({count:,} bytes)"
        mw._log("TX", f"[tcp] {shown}", _win.C_TX, conn=conn)

    # ── Dialog ────────────────────────────────────────────────────────────────

    def open_dialog(self) -> None:
        TcpShareDialog(self, self._mw._active_conn, self._mw).exec()


class TcpShareDialog(QDialog):
    def __init__(self, controller: TcpShareController, conn, parent=None):
        super().__init__(parent)
        self._controller = controller
        self._conn = conn
        self.setWindowTitle(tr("Share port over TCP"))
        self.setMinimumWidth(460)
        root = QVBoxLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.scope = QComboBox()
        self.scope.addItem(tr("This computer only"), LOCAL_HOST)
        self.scope.addItem(tr("Any computer on the network"), ALL_HOSTS)
        self.scope.currentIndexChanged.connect(self._refresh)
        form.addRow(tr("Who can connect"), self.scope)
        self.port = NumberBox()
        self.port.setDecimals(0)
        self.port.setRange(1, 65535)
        self.port.setValue(controller.port)
        self.port.setFixedWidth(90)
        form.addRow(tr("Port"), self.port)
        self.address = QLineEdit()
        self.address.setReadOnly(True)
        form.addRow(tr("Address"), self.address)
        root.addLayout(form)
        self.warning = QLabel(tr(
            "Anyone who can reach this computer can read everything the device sends and "
            "write to it. There is no password and no encryption."))
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet(f"color:{_win.C_ERR.name()}")
        root.addWidget(self.warning)
        self.status = QLabel()
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.toggle = buttons.addButton(tr("Start"), QDialogButtonBox.ButtonRole.ActionRole)
        self.toggle.clicked.connect(self._on_toggle)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._refresh()

    def _on_toggle(self) -> None:
        if self._conn.port_share is not None:
            self._controller.stop(self._conn)
            self.status.setText("")
        else:
            err = self._controller.start(self._conn, self.scope.currentData(),
                                         int(self.port.value()))
            self.status.setText(err or "")
        self._refresh()

    def _refresh(self) -> None:
        share = self._conn.port_share
        if share is not None:
            self.scope.setCurrentIndex(self.scope.findData(share.host))
            self.port.setValue(share.port)
            self.address.setText(self._controller.address(share))
        else:
            self.address.setText(tr("Not sharing"))
        self.scope.setEnabled(share is None)
        self.port.setEnabled(share is None)
        self.warning.setVisible(self.scope.currentData() == ALL_HOSTS)
        self.toggle.setText(tr("Stop") if share is not None else tr("Start"))
