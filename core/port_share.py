"""
core/port_share.py — expose a connection as a raw TCP port (CMP22).

Other programs, or another IsoDAQ with ``socket://host:port``, can then read and
write the device while this app stays connected to it. Bytes pass unchanged, in
both directions; every client gets everything the device sends.

There is no password and no encryption, and a client can write to the device, so
it is off until asked for, off again at every start, and listens on this computer
only unless the user chooses otherwise. See SECURITY.md.

Threads: :meth:`PortShare.feed` is called by the reader thread and only queues.
One thread serves the sockets and another writes to the device, because a slow
serial port blocks in ``write`` and must not hold up what clients receive. While
the device can't keep up, clients aren't read, so TCP itself slows them down.
"""
from __future__ import annotations

import queue
import select
import socket
import sys
import threading
from typing import Callable

from PyQt6.QtCore import QObject, pyqtSignal

DEFAULT_PORT = 7777
LOCAL_HOST = "127.0.0.1"
ALL_HOSTS = "0.0.0.0"     # noqa: S104  (only when the user chooses it)
MAX_CLIENTS = 8
#: A client this far behind is disconnected, rather than holding up the device's
#: data or growing without limit.
MAX_CLIENT_BACKLOG = 1 << 20
_READ_SIZE = 4096
_PREVIEW = 200


class _Client:
    def __init__(self, sock: socket.socket, label: str):
        self.sock = sock
        self.label = label
        self.out = bytearray()
        self.dropped = False


class PortShare(QObject):
    clients_changed = pyqtSignal(int)
    client_dropped = pyqtSignal(str)     # address of a client that fell too far behind

    def __init__(self, write: Callable[[bytes], "str | None"], parent=None):
        super().__init__(parent)
        self._write = write
        self.host = LOCAL_HOST
        self.port = DEFAULT_PORT
        self._listener: socket.socket | None = None
        self._wake_r: socket.socket | None = None
        self._wake_w: socket.socket | None = None
        self._clients: list[_Client] = []
        self._lock = threading.Lock()
        self._running = False
        self._threads: list[threading.Thread] = []
        self._to_device: queue.Queue = queue.Queue(maxsize=32)
        self._sent = 0
        self._preview = bytearray()
        self._error = ""

    # ── Control (GUI thread) ──────────────────────────────────────────────────

    def start(self, host: str = LOCAL_HOST, port: int = DEFAULT_PORT) -> str | None:
        """Start listening. Returns an error (the port is taken, say) or ``None``."""
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if sys.platform != "win32":
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((host, port))
            listener.listen(MAX_CLIENTS)
            listener.setblocking(False)
        except OSError as e:
            listener.close()
            return str(e)
        self._listener = listener
        self.host = host
        self.port = listener.getsockname()[1]
        self._wake_r, self._wake_w = socket.socketpair()
        self._wake_r.setblocking(False)
        self._wake_w.setblocking(False)
        self._running = True
        self._threads = [threading.Thread(target=self._serve, daemon=True),
                         threading.Thread(target=self._pump, daemon=True)]
        for t in self._threads:
            t.start()
        return None

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._wake()
        self._threads[0].join(2.0)
        while True:                      # make room for the pump's stop mark
            try:
                self._to_device.put_nowait(None)
                break
            except queue.Full:
                try:
                    self._to_device.get_nowait()
                except queue.Empty:
                    pass
        self._threads[1].join(2.0)       # a device stuck in write() stays daemon
        self._threads = []
        for s in (self._listener, self._wake_r, self._wake_w):
            if s is not None:
                s.close()
        self._listener = self._wake_r = self._wake_w = None

    @property
    def running(self) -> bool:
        return self._running

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)

    def take_sent(self) -> tuple[int, bytes, str]:
        """``(bytes written to the device, start of them, last error)`` since the
        last call, for the GUI to count and show at its own pace."""
        with self._lock:
            out = (self._sent, bytes(self._preview), self._error)
            self._sent = 0
            self._preview.clear()
            self._error = ""
        return out

    # ── Device → clients (reader thread) ──────────────────────────────────────

    def feed(self, chunk: bytes) -> None:
        """Queue *chunk* for every client. Never blocks."""
        if not self._clients:
            return
        with self._lock:
            for c in self._clients:
                c.out += chunk
                if len(c.out) > MAX_CLIENT_BACKLOG:
                    c.dropped = True
        self._wake()

    def _wake(self) -> None:
        try:
            if self._wake_w is not None:
                self._wake_w.send(b"\0")
        except OSError:
            pass                         # buffer full: it's awake already

    # ── Socket thread ─────────────────────────────────────────────────────────

    def _serve(self) -> None:
        try:
            while self._running:
                self._serve_once()
        finally:
            with self._lock:
                clients, self._clients = self._clients, []
            for c in clients:
                c.sock.close()
            if clients:
                self.clients_changed.emit(0)

    def _serve_once(self) -> None:
        with self._lock:
            clients = list(self._clients)
            for c in clients:
                if c.dropped:
                    self._close(c)
                    self.client_dropped.emit(c.label)
            clients = [c for c in clients if not c.dropped]
            writers = [c.sock for c in clients if c.out]
        # Clients aren't read while the device is behind; look again soon.
        full = self._to_device.full()
        readers = [self._listener, self._wake_r] + ([] if full else [c.sock for c in clients])
        readable, writable, _ = select.select(readers, writers, [], 0.02 if full else 0.5)
        if self._wake_r in readable:
            try:
                self._wake_r.recv(1024)
            except OSError:
                pass
        if self._listener in readable:
            self._accept()
        for c in clients:
            # Only this thread adds to the queue, so "not full" stays true for the
            # put. Anything not read now waits in the socket for the next pass.
            if c.sock in readable and not self._to_device.full() and not self._receive(c):
                continue
            if c.sock in writable:
                self._flush(c)

    def _accept(self) -> None:
        try:
            sock, addr = self._listener.accept()
        except OSError:
            return
        with self._lock:
            if len(self._clients) >= MAX_CLIENTS:
                sock.close()
                return
            sock.setblocking(False)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._clients.append(_Client(sock, f"{addr[0]}:{addr[1]}"))
            count = len(self._clients)
        self.clients_changed.emit(count)

    def _receive(self, client: _Client) -> bool:
        try:
            data = client.sock.recv(_READ_SIZE)
        except (BlockingIOError, InterruptedError):
            return True
        except OSError:
            data = b""
        if not data:
            with self._lock:
                self._close(client)
            return False
        self._to_device.put_nowait(data)
        return True

    def _flush(self, client: _Client) -> None:
        with self._lock:
            pending = bytes(client.out[:65536])
        try:
            sent = client.sock.send(pending)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            with self._lock:
                self._close(client)
            return
        with self._lock:
            del client.out[:sent]

    def _close(self, client: _Client) -> None:
        """Caller holds the lock."""
        if client in self._clients:
            self._clients.remove(client)
            client.sock.close()
            self.clients_changed.emit(len(self._clients))

    # ── Writer thread ─────────────────────────────────────────────────────────

    def _pump(self) -> None:
        while True:
            data = self._to_device.get()
            if data is None:
                return
            err = self._write(data)
            with self._lock:
                if err:
                    self._error = err
                else:
                    self._sent += len(data)
                    if len(self._preview) < _PREVIEW:
                        self._preview += data[:_PREVIEW - len(self._preview)]

