"""
core/file_transfer.py — send a file with XMODEM or YMODEM (CMP19).

For bootloaders and firmware that receive uploads this way. The receiver starts the
transfer, so the device has to be told first, from the command bar (U-Boot's
``loady``, for example). Send only; receiving isn't implemented.

:func:`send_file` is plain protocol over ``read`` / ``write`` callables.
:class:`TransferThread` runs it on an open connection, which holds its reader off
the port for the duration.
"""
from __future__ import annotations

import os
import time

from PyQt6.QtCore import QThread, pyqtSignal

from core.checksum import crc16_xmodem

SOH, STX, EOT, ACK, NAK, CAN, CRC_REQUEST, PAD = 0x01, 0x02, 0x04, 0x06, 0x15, 0x18, 0x43, 0x1A

MODES = ("XMODEM", "XMODEM-1K", "YMODEM")
START_TIMEOUT_S = 60.0       # for the user to start the receiver on the device
BLOCK_TIMEOUT_S = 10.0       # the conventional per-block answer timeout
MAX_RETRIES = 10


class TransferError(Exception):
    """The transfer failed; the message is for the user."""


class TransferCancelled(TransferError):
    pass


def make_block(number: int, payload: bytes, use_crc: bool) -> bytes:
    """One block: header, number and its complement, payload, CRC or checksum."""
    head = bytes([STX if len(payload) == 1024 else SOH, number & 0xFF, 0xFF - (number & 0xFF)])
    if use_crc:
        tail = crc16_xmodem(payload).to_bytes(2, "big")
    else:
        tail = bytes([sum(payload) & 0xFF])
    return head + payload + tail


def _is_alnum(byte: int) -> bool:
    return 48 <= byte <= 57 or 65 <= byte <= 90 or 97 <= byte <= 122


class _Sender:
    def __init__(self, read, write, should_cancel):
        self._read, self._write = read, write
        self._should_cancel = should_cancel
        self.use_crc = True

    def _check_cancel(self) -> None:
        if self._should_cancel():
            self._write(bytes([CAN] * 8))    # the convention: several, so noise can't
            raise TransferCancelled("Cancelled")

    def _next_byte(self, deadline: float) -> int | None:
        """The next received byte, or ``None`` at the deadline."""
        while True:
            self._check_cancel()
            chunk = self._read(1)
            if chunk:
                return chunk[0]
            if time.monotonic() > deadline:
                return None

    def wait_for_start(self, allow_checksum: bool, timeout: float) -> None:
        """Wait for the receiver to ask for data, ``C`` (CRC) or NAK (checksum).

        A device usually prints a banner first, and the ``C`` in ``Connected`` must
        not start the transfer: a ``C`` only counts if no letter or digit is right
        next to it."""
        deadline = time.monotonic() + timeout
        previous = 0
        cans = 0
        while True:
            byte = self._next_byte(deadline)
            if byte is None:
                raise TransferError("The receiver never started. Start it on the device "
                                    "first (for example rx or loady).")
            cans = cans + 1 if byte == CAN else 0
            if cans >= 2:
                raise TransferError("The receiver cancelled the transfer.")
            if byte == CRC_REQUEST and (not _is_alnum(previous) or previous == CRC_REQUEST):
                following = self._next_byte(time.monotonic() + 0.05)
                if following is None or not _is_alnum(following) or following == CRC_REQUEST:
                    self.use_crc = True
                    return
                previous = following
                continue
            if byte == NAK and allow_checksum:
                self.use_crc = False
                return
            previous = byte

    def _send_block(self, block: bytes, first: bool = False) -> None:
        """Send until ACK. A ``C`` answering the first block means the receiver hadn't
        seen it yet, and is treated like a NAK."""
        for _attempt in range(MAX_RETRIES):
            self._write(block)
            deadline = time.monotonic() + BLOCK_TIMEOUT_S
            cans = 0
            while True:
                byte = self._next_byte(deadline)
                if byte is None or byte == NAK or (first and byte == CRC_REQUEST):
                    break                    # send it again
                if byte == ACK:
                    return
                cans = cans + 1 if byte == CAN else 0
                if cans >= 2:
                    raise TransferError("The receiver cancelled the transfer.")
        self._write(bytes([CAN] * 8))
        raise TransferError(f"No answer after {MAX_RETRIES} tries; the device stopped responding.")

    def send_data(self, data: bytes, block_size: int, progress) -> None:
        total, sent, number = len(data), 0, 1
        size = block_size if self.use_crc else 128   # 1K blocks need the CRC
        while sent < total:
            # Under 128 bytes left: a short block, which pads less.
            this = size if total - sent > 128 else 128
            payload = data[sent:sent + this]
            payload += bytes([PAD]) * (this - len(payload))
            self._send_block(make_block(number, payload, self.use_crc), first=number == 1)
            sent = min(total, sent + this)
            number += 1
            progress(sent, total)

    def finish_file(self) -> None:
        """EOT until ACK. YMODEM receivers NAK the first one by design."""
        for _attempt in range(MAX_RETRIES):
            self._write(bytes([EOT]))
            byte = self._next_byte(time.monotonic() + BLOCK_TIMEOUT_S)
            if byte == ACK:
                return
        raise TransferError("The device never confirmed the end of the file.")

    def send_header(self, name: str, size: int) -> None:
        body = name.encode("utf-8", errors="replace") + b"\0" + str(size).encode() + b"\0"
        if len(body) > 1024:
            raise TransferError("The file name is too long for YMODEM.")
        block_len = 128 if len(body) <= 128 else 1024
        self._send_block(make_block(0, body + bytes(block_len - len(body)), True), first=True)

    def wait_for_c(self) -> None:
        deadline = time.monotonic() + BLOCK_TIMEOUT_S
        while True:
            byte = self._next_byte(deadline)
            if byte is None:
                raise TransferError("The device didn't ask for the data.")
            if byte == CRC_REQUEST:
                return
            if byte == CAN:
                raise TransferError("The receiver cancelled the transfer.")

    def send_end_of_batch(self) -> None:
        self._send_block(make_block(0, bytes(128), True), first=True)


def send_file(read, write, data: bytes, name: str, mode: str, *, progress=lambda _s, _t: None,
              should_cancel=lambda: False, on_status=lambda _m: None,
              start_timeout: float = START_TIMEOUT_S) -> None:
    """Send *data*. ``read(1)`` returns ``b""`` on a short timeout. Raises
    :class:`TransferError`, or :class:`TransferCancelled` if *should_cancel*."""
    if mode not in MODES:
        raise TransferError(f"Unknown protocol: {mode}")
    sender = _Sender(read, write, should_cancel)
    on_status("Waiting for the receiver…")
    ymodem = mode == "YMODEM"
    sender.wait_for_start(allow_checksum=not ymodem, timeout=start_timeout)
    on_status("Sending…")
    if ymodem:
        sender.send_header(name, len(data))
        sender.wait_for_c()
    sender.send_data(data, 128 if mode == "XMODEM" else 1024, progress)
    sender.finish_file()
    if ymodem:
        sender.wait_for_c()
        sender.send_end_of_batch()


class TransferThread(QThread):
    """One send over an open connection's reader."""

    progress = pyqtSignal(int, int)      # bytes sent, total
    status = pyqtSignal(str)
    finished_result = pyqtSignal(str, float)     # error ("" = success), seconds

    def __init__(self, reader, path: str, mode: str, parent=None):
        super().__init__(parent)
        self._reader = reader
        self._path = path
        self._mode = mode
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        t0 = time.monotonic()
        try:
            with open(self._path, "rb") as f:
                data = f.read()
        except OSError as e:
            self.finished_result.emit(f"Could not read the file: {e}", 0.0)
            return
        if not data:
            self.finished_result.emit("The file is empty.", 0.0)
            return
        if not self._reader.acquire_port():
            self.finished_result.emit("Could not take over the port.", 0.0)
            return
        try:
            send_file(self._reader.port_read, self._reader.port_write, data,
                      os.path.basename(self._path), self._mode,
                      progress=self.progress.emit, should_cancel=lambda: self._cancel,
                      on_status=self.status.emit)
            error = ""
        except TransferCancelled:
            error = "Cancelled"
        except TransferError as e:
            error = str(e)
        except Exception as e:               # the port itself failed
            error = f"The connection failed during the transfer: {e}"
        finally:
            self._reader.release_port()
        self.finished_result.emit(error, time.monotonic() - t0)
