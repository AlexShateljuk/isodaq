"""
core/capture.py — save the raw bytes from a device to a file (F11), for flash dumps
and anything else that has to be kept exactly. No Qt.

:meth:`RawCapture.feed` runs on the reader thread, so a full disk stops the capture
and is reported rather than raising there.
"""
from __future__ import annotations

import threading
from pathlib import Path


class RawCapture:
    def __init__(self, path: str):
        self.path = Path(path)
        self.size = 0
        self.error = ""
        self._lock = threading.Lock()
        self._file = open(path, "wb")    # noqa: SIM115  (closed by close())

    def feed(self, chunk: bytes) -> None:
        with self._lock:
            if self._file is None:
                return
            try:
                self._file.write(chunk)
                self.size += len(chunk)
            except OSError as e:
                self.error = str(e)
                self._close_file()

    def close(self) -> None:
        """Whatever is still buffered is written here, so this can fail too."""
        with self._lock:
            if self._file is not None:
                self._close_file()

    def _close_file(self) -> None:
        try:
            self._file.close()
        except OSError as e:
            self.error = self.error or str(e)
        self._file = None
