"""TransferController — send a file with XMODEM / YMODEM over an open connection."""
from __future__ import annotations

import os

from PyQt6.QtCore import QObject

import ui.main_window as _win   # colour globals (runtime access only)
from core.file_transfer import TransferThread
from core.i18n import tr
from core.stream_reader import StreamReader


class TransferController(QObject):
    def __init__(self, mw):
        super().__init__(mw)
        self._mw = mw

    def wire_panel(self, conn) -> None:
        conn.flash_panel.transfer_requested.connect(lambda c=conn: self.start(c))
        conn.flash_panel.transfer_cancel_requested.connect(lambda c=conn: self.cancel(c))

    def start(self, conn=None) -> None:
        mw = self._mw
        conn = conn or mw._active_conn
        panel = conn.flash_panel
        if conn.transfer is not None:
            return
        reader = conn.reader
        if conn.flash_runner is not None:
            panel.set_transfer_status(tr("Flashing: the port is in use."))
            return
        if not reader.isRunning():
            panel.set_transfer_status(tr("Connect first, then start the receiver on the device."))
            return
        if not isinstance(reader, StreamReader):
            panel.set_transfer_status(tr("File transfer works over Serial or Network connections."))
            return
        path = os.path.expanduser(panel.transfer_path())
        if not os.path.isfile(path):
            panel.set_transfer_status(tr("File not found.") if path else tr("Choose a file first."))
            return

        mode = panel.transfer_mode()
        thread = TransferThread(reader, path, mode, parent=conn)
        thread.progress.connect(panel.set_transfer_progress)
        thread.status.connect(panel.set_transfer_status)
        thread.finished_result.connect(
            lambda error, secs, c=conn, p=path, m=mode: self._on_done(c, p, m, error, secs))
        conn.transfer = thread
        panel.set_transfer_running(True)
        panel.set_transfer_progress(0, 1)
        mw._log("SYS", f"▶ Sending {os.path.basename(path)} with {mode}: start the receiver on "
                       "the device if it isn't waiting yet.", _win.C_SYS, conn=conn)
        self._refresh(conn)
        thread.start()

    def cancel(self, conn=None) -> None:
        conn = conn or self._mw._active_conn
        if conn.transfer is not None:
            conn.transfer.cancel()

    def shutdown(self, conn) -> None:
        """The connection or the window is closing."""
        thread = conn.transfer
        if thread is None:
            return
        conn.transfer = None             # _on_done: nothing left to update
        thread.cancel()
        thread.wait(3000)

    def _on_done(self, conn, path: str, mode: str, error: str, secs: float) -> None:
        thread, conn.transfer = conn.transfer, None
        if thread is None:               # shut down; the panel may be gone
            return
        thread.deleteLater()
        mw = self._mw
        panel = conn.flash_panel
        panel.set_transfer_running(False)
        name = os.path.basename(path)
        if error == "Cancelled":
            panel.set_transfer_status(tr("Cancelled"))
            mw._log("SYS", f"Sending {name} cancelled.", _win.C_SYS, conn=conn)
        elif error:
            panel.set_transfer_status(error)
            mw._log("ERR", f"Sending {name} failed: {error}", _win.C_ERR, conn=conn)
        else:
            size = os.path.getsize(path)
            text = f"Sent {name} ({mw._fmt_bytes(size)}) with {mode} in {secs:.1f} s"
            panel.set_transfer_status(text)
            mw._log("SYS", text, _win.C_OK, conn=conn)
        self._refresh(conn)

    def _refresh(self, conn) -> None:
        if conn is self._mw._active_conn:
            self._mw._refresh_conn_state()
