"""A tiny real MQTT 3.1.1 broker on localhost, for tests: no mock of the client
library, the bytes on the socket are the protocol. QoS 0, exact and `#` matching."""
from __future__ import annotations

import socket
import threading


def _encode_len(n: int) -> bytes:
    out = bytearray()
    while True:
        n, digit = n // 128, n % 128
        out.append(digit | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _matches(pattern: str, topic: str) -> bool:
    if pattern == "#":
        return True
    if pattern.endswith("/#"):
        return topic.startswith(pattern[:-1])
    return pattern == topic


class FakeBroker:
    def __init__(self, password: str | None = None, port: int = 0):
        self.password = password
        self.published: list[tuple[str, bytes]] = []   # everything clients sent
        self.subscriptions: list[str] = []
        self._clients: dict[socket.socket, list[str]] = {}
        self._lock = threading.Lock()
        self._srv = socket.socket()
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", port))
        self._srv.listen(5)
        self.port = self._srv.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                sock, _ = self._srv.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(sock,), daemon=True).start()

    @staticmethod
    def _read_exact(sock: socket.socket, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError
            buf += chunk
        return buf

    def _serve(self, sock: socket.socket) -> None:
        try:
            while True:
                head = self._read_exact(sock, 1)[0]
                length, shift = 0, 0
                while True:
                    b = self._read_exact(sock, 1)[0]
                    length |= (b & 0x7F) << shift
                    shift += 7
                    if not b & 0x80:
                        break
                body = self._read_exact(sock, length)
                self._handle(sock, head >> 4, body)
        except (ConnectionError, OSError):
            pass
        finally:
            with self._lock:
                self._clients.pop(sock, None)
            sock.close()

    def _handle(self, sock, kind: int, body: bytes) -> None:
        if kind == 1:                                   # CONNECT
            flags = body[7]
            pos = 10
            fields = []
            while pos < len(body):
                n = int.from_bytes(body[pos:pos + 2], "big")
                fields.append(body[pos + 2:pos + 2 + n])
                pos += 2 + n
            given = fields[-1].decode() if flags & 0x40 else None
            code = 5 if self.password is not None and given != self.password else 0
            sock.sendall(bytes([0x20, 2, 0, code]))
            if code:
                raise ConnectionError
            with self._lock:
                self._clients[sock] = []
        elif kind == 8:                                 # SUBSCRIBE
            packet_id, pos, granted = body[:2], 2, bytearray()
            while pos < len(body):
                n = int.from_bytes(body[pos:pos + 2], "big")
                topic = body[pos + 2:pos + 2 + n].decode()
                pos += 3 + n
                granted.append(0)
                with self._lock:
                    self._clients[sock].append(topic)
                    self.subscriptions.append(topic)
            sock.sendall(bytes([0x90]) + _encode_len(2 + len(granted)) + packet_id + bytes(granted))
        elif kind == 3:                                 # PUBLISH (QoS 0)
            n = int.from_bytes(body[:2], "big")
            topic, payload = body[2:2 + n].decode(), body[2 + n:]
            self.published.append((topic, payload))
            self.publish(topic, payload)
        elif kind == 12:                                # PINGREQ
            sock.sendall(bytes([0xD0, 0]))
        elif kind == 14:                                # DISCONNECT
            raise ConnectionError

    def publish(self, topic: str, payload: bytes) -> None:
        """Deliver to every subscribed client, as if another client published it."""
        t = topic.encode()
        packet = len(t).to_bytes(2, "big") + t + payload
        packet = bytes([0x30]) + _encode_len(len(packet)) + packet
        with self._lock:
            clients = [s for s, subs in self._clients.items()
                       if any(_matches(p, topic) for p in subs)]
        for s in clients:
            try:
                s.sendall(packet)
            except OSError:
                pass

    def kick_clients(self) -> None:
        with self._lock:
            clients = list(self._clients)
        for s in clients:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def stop(self) -> None:
        self.kick_clients()
        try:
            self._srv.close()
        except OSError:
            pass
