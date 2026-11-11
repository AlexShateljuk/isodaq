"""CaptureController — save a connection's raw bytes to a file (F11).

Like a TCP share it lives through an automatic reconnect and a flash, since the same
device is coming back, and ends when the connection really does.
"""
from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWidgets import QFileDialog

import ui.main_window as _win   # for theme-updated colour globals (runtime access only)
from core.capture import RawCapture
from core.i18n import tr
from core.stream_reader import add_tap, remove_tap

REFRESH_MS = 500


class CaptureController(QObject):
    def __init__(self, mw):
        super().__init__(mw)
        self._mw = mw
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self._tick)

    def toggle(self) -> None:
        """Start asking where to save, or stop the capture that is running."""
        conn = self._mw._active_conn
        if conn.capture is not None:
            self.stop(conn)
            return
        path, _ = QFileDialog.getSaveFileName(self._mw, tr("Capture raw bytes to file"),
                                              "capture.bin", tr("Binary file (*.bin);;All files (*)"))
        if path:
            err = self.start(conn, path)
            if err:
                self._mw._log("ERR", err, _win.C_ERR, conn=conn)

    def start(self, conn, path: str) -> str | None:
        reader = conn.reader
        if not reader.isRunning():
            return tr("Connect to the device first.")
        if not getattr(reader, "shareable", False):
            return tr("MQTT and Modbus connections aren't byte streams, so there are no raw "
                      "bytes to capture.")
        try:
            capture = RawCapture(path)
        except OSError as e:
            return tr("Could not open the file: {err}").format(err=e)
        conn.capture = capture
        add_tap(reader, capture.feed)
        self._timer.start()
        self._mw._log("SYS", tr("Capturing raw bytes to {path}").format(path=path),
                      _win.C_OK, conn=conn)
        self._mw._refresh_activity()
        return None

    def stop(self, conn, why: str = "") -> None:
        capture = conn.capture
        if capture is None:
            return
        conn.capture = None
        for reader in conn.readers.values():
            remove_tap(reader, capture.feed)
        capture.close()
        if not any(c.capture for c in self._mw._connections):
            self._timer.stop()
        mw = self._mw
        if capture.error:
            mw._log("ERR", tr("Capture stopped at {n} bytes: {err}").format(
                n=f"{capture.size:,}", err=capture.error), _win.C_ERR, conn=conn)
        else:
            mw._log("SYS", (why + " " if why else "") + tr("Saved {n} bytes to {path}").format(
                n=f"{capture.size:,}", path=capture.path), _win.C_SYS, conn=conn)
        mw._refresh_activity()

    def on_link_closed(self, conn) -> None:
        if conn.capture is not None and not conn.resume_after_flash:
            self.stop(conn, tr("Capture stopped: the connection closed."))

    def _tick(self) -> None:
        for conn in self._mw._connections:
            if conn.capture is not None and conn.capture.error:
                self.stop(conn)
        self._mw._refresh_activity()
