"""A Modbus slave for tests, answering real frames over TCP or a pseudo-terminal.
Deliberately independent of core.modbus apart from the CRC (checked separately
against published frames), so an encoding mistake there can't cancel out here."""
from __future__ import annotations

import os
import select
import socket
import struct
import threading
import time
import tty


class Slave:
    def __init__(self, unit: int = 1):
        self.unit = unit
        self.holding: dict[int, int] = {}
        self.input: dict[int, int] = {}
        self.coils: dict[int, bool] = {}
        self.discrete: dict[int, bool] = {}
        self.requests: list[bytes] = []
        self.silent = False          # no answer at all
        self.corrupt_next = False    # flip a CRC bit / a byte in the next answer

    def answer(self, pdu: bytes) -> bytes:
        """The response PDU for a request PDU."""
        self.requests.append(pdu)
        fc = pdu[0]
        if fc in (3, 4):
            start, count = struct.unpack(">HH", pdu[1:5])
            table = self.holding if fc == 3 else self.input
            if any(a not in table for a in range(start, start + count)):
                return bytes([fc | 0x80, 2])
            data = b"".join(struct.pack(">H", table[a]) for a in range(start, start + count))
            return bytes([fc, len(data)]) + data
        if fc in (1, 2):
            start, count = struct.unpack(">HH", pdu[1:5])
            table = self.coils if fc == 1 else self.discrete
            if any(a not in table for a in range(start, start + count)):
                return bytes([fc | 0x80, 2])
            data = bytearray((count + 7) // 8)
            for i in range(count):
                if table[start + i]:
                    data[i // 8] |= 1 << (i % 8)
            return bytes([fc, len(data)]) + bytes(data)
        if fc == 5:
            addr, value = struct.unpack(">HH", pdu[1:5])
            self.coils[addr] = value == 0xFF00
            return pdu
        if fc == 6:
            addr, value = struct.unpack(">HH", pdu[1:5])
            self.holding[addr] = value
            return pdu
        if fc == 16:
            addr, count, size = struct.unpack(">HHB", pdu[1:6])
            for i in range(count):
                self.holding[addr + i] = struct.unpack(">H", pdu[6 + i * 2:8 + i * 2])[0]
            return pdu[:5]
        return bytes([fc | 0x80, 1])


class TcpSlave(Slave):
    def __init__(self, unit: int = 1, port: int = 0):
        super().__init__(unit)
        self._srv = socket.socket()
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", port))
        self._srv.listen(2)
        self.port = self._srv.getsockname()[1]
        self._conns: list[socket.socket] = []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                sock, _ = self._srv.accept()
            except OSError:
                return
            self._conns.append(sock)
            threading.Thread(target=self._serve, args=(sock,), daemon=True).start()

    def _serve(self, sock: socket.socket) -> None:
        try:
            while True:
                head = b""
                while len(head) < 7:
                    chunk = sock.recv(7 - len(head))
                    if not chunk:
                        return
                    head += chunk
                tid, _proto, length, unit = struct.unpack(">HHHB", head)
                pdu = b""
                while len(pdu) < length - 1:
                    chunk = sock.recv(length - 1 - len(pdu))
                    if not chunk:
                        return
                    pdu += chunk
                if self.silent or unit != self.unit:
                    self.requests.append(pdu)
                    continue
                reply = self.answer(pdu)
                sock.sendall(struct.pack(">HHHB", tid, 0, len(reply) + 1, unit) + reply)
        except OSError:
            pass

    def kick(self) -> None:
        for s in self._conns:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def stop(self) -> None:
        self.kick()
        self._srv.close()


class RtuSlave(Slave):
    """Listens on a pty master; the app opens the slave side as a serial port."""

    def __init__(self, crc16, unit: int = 1):
        super().__init__(unit)
        self._crc = crc16
        self.master, self._slave_fd = os.openpty()
        tty.setraw(self._slave_fd)
        self.port = os.ttyname(self._slave_fd)
        self._stop = False
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        buf = b""
        while not self._stop:
            # select, not a blocking read: closing a tty while a thread is blocked
            # reading it can block the close on macOS.
            if not select.select([self.master], [], [], 0.05)[0]:
                continue
            try:
                chunk = os.read(self.master, 512)
            except OSError:
                return
            buf += chunk
            frame_len = self._frame_len(buf)
            if frame_len is None or len(buf) < frame_len:
                continue
            frame, buf = buf[:frame_len], buf[frame_len:]
            if struct.unpack("<H", frame[-2:])[0] != self._crc(frame[:-2]):
                continue                    # as a real slave does: stays silent
            if self.silent or frame[0] != self.unit:
                self.requests.append(frame[1:-2])
                continue
            reply = bytes([self.unit]) + self.answer(frame[1:-2])
            reply += struct.pack("<H", self._crc(reply))
            if self.corrupt_next:
                self.corrupt_next = False
                reply = reply[:-1] + bytes([reply[-1] ^ 0x55])
            time.sleep(0.002)
            os.write(self.master, reply)

    @staticmethod
    def _frame_len(buf: bytes):
        if len(buf) < 2:
            return None
        if buf[1] in (1, 2, 3, 4, 5, 6):
            return 8
        if buf[1] == 16:
            return 9 + buf[6] if len(buf) > 6 else None
        return len(buf)

    def stop(self) -> None:
        self._stop = True
        self._thread.join(1)
        for fd in (self.master, self._slave_fd):
            try:
                os.close(fd)
            except OSError:
                pass
