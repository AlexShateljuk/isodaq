"""IsoDAQ's side of ``isodaq-agent`` (AGT2): pairing, listing a remote machine's
ports, and :class:`AgentSerial`, a pyserial-like object for one remote port, so
:class:`core.agent_reader.AgentReader` reuses the whole StreamReader read loop.

Targets are written ``agent://host[:port]/dev/ttyUSB0`` (``agent://pc/COM5`` on
Windows). All agent connections share one background asyncio loop.

What is remembered per agent (``~/.isodaq_studio/agents.json``, readable only by
you): its name, its certificate fingerprint and the token it issued when paired.
The token works only with that agent and can be revoked there
(``isodaq-agent revoke``). The pairing code is never stored.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import socket
import ssl
import tempfile
import threading
import time
from pathlib import Path

from isodaq_agent import protocol

CONNECT_TIMEOUT = 8.0
WRITE_TIMEOUT = 5.0
#: Writes larger than this go as several frames.
CHUNK = 64 * 1024


class AgentError(Exception):
    """The agent refused, or isn't reachable. ``code`` says why (``auth``,
    ``not-paired``, ``fingerprint``, ``pair-wrong``, ``busy``, ``open`` …)."""

    #: Trying again won't help: the user has to pair (again). Not ``open``: a
    #: board unplugged from the agent comes back under the same name.
    FATAL = ("auth", "not-paired", "fingerprint", "version")

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code

    @property
    def fatal(self) -> bool:
        return self.code in self.FATAL


# ── Targets and trust ─────────────────────────────────────────────────────────

def parse_url(url: str) -> tuple[str, int, str]:
    """``agent://host:port/dev/ttyUSB0`` → ``("host", port, "/dev/ttyUSB0")``.
    Raises ``ValueError``."""
    url = (url or "").strip()
    if url.startswith("agent://"):
        url = url[len("agent://"):]
    if "/" not in url:
        raise ValueError("Write the target as agent://host/dev/ttyUSB0")
    address, device = url.split("/", 1)
    device = "/" + device
    if device[1:4].upper() == "COM" and device[4:].isdigit():
        device = device[1:]                 # agent://pc/COM5 → COM5
    host, port = split_address(address)
    if device in ("/", ""):
        raise ValueError("Which port on the agent? e.g. agent://host/dev/ttyUSB0")
    return host, port, device


def split_address(address: str) -> tuple[str, int]:
    """``host``, ``host:7780`` or ``[fe80::1]:7780`` → ``(host, port)``."""
    address = address.strip()
    if address.startswith("["):
        host, _, rest = address[1:].partition("]")
        port = rest.lstrip(":")
    elif address.count(":") == 1:
        host, port = address.split(":")
    else:
        host, port = address, ""
    if not host:
        raise ValueError("No agent address")
    try:
        number = int(port) if port else protocol.DEFAULT_PORT
    except ValueError:
        raise ValueError(f"{port!r} isn't a port number") from None
    if not 0 < number < 65536:
        raise ValueError(f"{number} isn't a port number")
    return host, number


def make_url(host: str, port: int, device: str) -> str:
    where = f"[{host}]" if ":" in host else host
    if port != protocol.DEFAULT_PORT:
        where += f":{port}"
    return f"agent://{where}{device if device.startswith('/') else '/' + device}"


def default_store_path() -> Path:
    return Path.home() / ".isodaq_studio" / "agents.json"


class TrustStore:
    """Paired agents by ``host:port``."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else default_store_path()
        self._lock = threading.Lock()

    @staticmethod
    def key(host: str, port: int) -> str:
        return f"{host.lower()}:{port}"

    def all(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def get(self, host: str, port: int) -> dict | None:
        entry = self.all().get(self.key(host, port))
        return entry if isinstance(entry, dict) else None

    def put(self, host: str, port: int, entry: dict) -> None:
        with self._lock:
            data = self.all()
            data[self.key(host, port)] = entry
            self._write(data)

    def forget(self, host: str, port: int) -> bool:
        with self._lock:
            data = self.all()
            if data.pop(self.key(host, port), None) is None:
                return False
            self._write(data)
            return True

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".agents.")
        try:
            os.chmod(tmp, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


# ── The shared event loop ─────────────────────────────────────────────────────

_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()


def _event_loop() -> asyncio.AbstractEventLoop:
    global _loop
    with _loop_lock:
        if _loop is None or _loop.is_closed():
            loop = asyncio.new_event_loop()
            threading.Thread(target=loop.run_forever, name="isodaq-agent-client",
                             daemon=True).start()
            _loop = loop
        return _loop


def _run(coro, timeout: float):
    future = asyncio.run_coroutine_threadsafe(coro, _event_loop())
    try:
        return future.result(timeout)
    except (concurrent.futures.TimeoutError, asyncio.TimeoutError):
        future.cancel()
        raise AgentError("timeout", "The agent didn't answer in time") from None


def _client_context() -> ssl.SSLContext:
    # The agent's certificate is self-signed: it is trusted by its fingerprint
    # (pinned at pairing), not by a certificate authority.
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def _connect(host: str, port: int, pinned: str | None):
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, ssl=_client_context()), CONNECT_TIMEOUT)
    except asyncio.TimeoutError:
        raise AgentError("unreachable", f"No answer from {host}:{port}") from None
    except (OSError, ssl.SSLError) as e:
        raise AgentError("unreachable", f"Can't reach the agent at {host}:{port}: "
                                        f"{e.strerror or e}") from None
    der = writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
    fp = protocol.fingerprint(der or b"")
    if pinned is not None and fp != pinned:
        writer.close()
        raise AgentError(
            "fingerprint",
            f"The agent at {host}:{port} isn't the one you paired with: its certificate "
            f"changed (now {protocol.show_fingerprint(fp)}). If it was reinstalled, forget "
            "it and pair again; otherwise something is impersonating it.")
    return reader, writer, fp


async def _expect(reader, *ops: str) -> dict:
    """The next control message, which must be one of *ops* (an ``error`` raises)."""
    while True:
        kind, msg = await asyncio.wait_for(protocol.read_frame(reader), CONNECT_TIMEOUT)
        if kind != protocol.CONTROL:
            continue
        if msg["op"] == "error":
            raise AgentError(str(msg.get("code", "error")), str(msg.get("message", "")))
        if msg["op"] in ops:
            return msg


async def _hello(host: str, port: int, entry: dict | None):
    if not entry or not entry.get("token") or not entry.get("fingerprint"):
        raise AgentError("not-paired", f"{host}:{port} isn't paired yet: "
                                       "Device → Pair with agent…")
    reader, writer, _ = await _connect(host, port, entry["fingerprint"])
    try:
        writer.write(protocol.control("hello", version=protocol.VERSION,
                                      token=entry["token"]))
        welcome = await _expect(reader, "welcome")
    except asyncio.IncompleteReadError:
        writer.close()
        raise AgentError("closed", "The agent closed the connection") from None
    except BaseException:
        writer.close()
        raise
    return reader, writer, welcome


# ── Pairing and listing (call from a worker thread: they block) ──────────────

def pair(host: str, port: int, code: str, store: TrustStore, name: str = "") -> dict:
    """Pair with the agent using the code it printed. Saves and returns the entry."""
    async def go():
        reader, writer, fp = await _connect(host, port, None)
        try:
            key = protocol.code_key(code)
            writer.write(protocol.control("pair", version=protocol.VERSION,
                                          name=name or socket.gethostname(),
                                          proof=protocol.pair_proof(key, fp)))
            paired = await _expect(reader, "paired")
            welcome = await _expect(reader, "welcome")
        except asyncio.IncompleteReadError:
            raise AgentError("closed", "The agent closed the connection") from None
        finally:
            writer.close()
        return {"name": str(welcome.get("agent") or host), "host": host, "port": port,
                "fingerprint": fp,
                "token": paired["token"], "client": paired.get("client", ""),
                "paired": time.strftime("%Y-%m-%d %H:%M:%S")}
    entry = _run(go(), CONNECT_TIMEOUT * 2)
    store.put(host, port, entry)
    return entry


def list_ports(host: str, port: int, store: TrustStore) -> tuple[str, list[dict]]:
    """``(agent name, ports)`` of a paired agent."""
    async def go():
        reader, writer, welcome = await _hello(host, port, store.get(host, port))
        try:
            writer.write(protocol.control("list"))
            ports = await _expect(reader, "ports")
        finally:
            writer.close()
        return str(welcome.get("agent") or host), list(ports.get("ports") or [])
    return _run(go(), CONNECT_TIMEOUT * 2)


# ── One remote port ───────────────────────────────────────────────────────────

class AgentSerial:
    """A port on an agent, with the parts of the pyserial API the readers use:
    ``in_waiting``, ``read``, ``write``, ``close``, ``is_open``, and ``dtr`` /
    ``rts`` / ``break_condition`` / ``baudrate`` (sent to the agent in order)."""

    def __init__(self, host: str, port: int, device: str, store: TrustStore,
                 settings: dict, timeout: float = 0.05):
        self.host, self.port, self.device = host, port, device
        self.timeout = timeout
        self.agent = ""
        self.is_open = False
        self._rx = bytearray()
        self._cond = threading.Condition()
        self._closed: str | None = None
        self.last_error = ""
        self._writer = None
        self._pump_task = None
        self._baud = settings.get("baud", 115200)
        self._loop = _event_loop()
        _run(self._start(store.get(host, port), settings), CONNECT_TIMEOUT * 2)
        self.is_open = True

    async def _start(self, entry, settings: dict) -> None:
        reader, writer, welcome = await _hello(self.host, self.port, entry)
        self.agent = str(welcome.get("agent") or self.host)
        try:
            writer.write(protocol.control("open", port=self.device, **settings))
            await _expect(reader, "opened")
        except asyncio.IncompleteReadError:
            writer.close()
            raise AgentError("closed", "The agent closed the connection") from None
        except BaseException:
            writer.close()
            raise
        self._writer = writer
        self._pump_task = asyncio.ensure_future(self._pump(reader))

    async def _pump(self, reader) -> None:
        reason = "The agent closed the connection"
        try:
            while True:
                kind, msg = await protocol.read_frame(reader)
                if kind == protocol.DATA:
                    with self._cond:
                        self._rx.extend(msg)
                        self._cond.notify_all()
                elif msg["op"] == "closed":
                    reason = str(msg.get("reason") or reason)
                    break
                elif msg["op"] == "error":
                    self.last_error = str(msg.get("message", ""))
        except asyncio.CancelledError:
            reason = "closed"
        except (asyncio.IncompleteReadError, ConnectionError, OSError, ssl.SSLError):
            pass
        except protocol.ProtocolError as e:
            reason = f"The agent sent something unexpected: {e}"
        finally:
            with self._cond:
                if self._closed is None:
                    self._closed = f"{self.agent or self.host}: {reason}"
                self._cond.notify_all()
            if self._writer is not None:
                self._writer.close()

    # ── pyserial-like API ─────────────────────────────────────────────────────

    @property
    def in_waiting(self) -> int:
        with self._cond:
            if not self._rx and self._closed is not None:
                raise OSError(self._closed)
            return len(self._rx)

    def read(self, n: int = 1) -> bytes:
        deadline = time.monotonic() + (self.timeout or 0)
        with self._cond:
            while len(self._rx) < n and self._closed is None:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                self._cond.wait(left)
            if not self._rx and self._closed is not None:
                raise OSError(self._closed)
            out = bytes(self._rx[:n])
            del self._rx[:n]
            return out

    def write(self, data: bytes) -> int:
        if self._closed is not None or not self.is_open:
            raise OSError(self._closed or "Not connected")
        view = memoryview(bytes(data))
        for i in range(0, len(view), CHUNK):
            frame = protocol.data(bytes(view[i:i + CHUNK]))
            _run(self._send(frame), WRITE_TIMEOUT)
        return len(data)

    async def _send(self, frame: bytes) -> None:
        self._writer.write(frame)
        await self._writer.drain()

    def _set(self, **fields) -> None:
        if self._closed is not None or self._writer is None:
            raise OSError(self._closed or "Not connected")
        frame = protocol.control("set", **fields)
        self._loop.call_soon_threadsafe(self._writer.write, frame)

    dtr = property(None, lambda self, on: self._set(dtr=bool(on)))
    rts = property(None, lambda self, on: self._set(rts=bool(on)))
    break_condition = property(None, lambda self, on: self._set(**{"break": bool(on)}))

    @property
    def baudrate(self) -> int:
        return self._baud

    @baudrate.setter
    def baudrate(self, baud: int) -> None:
        self._baud = int(baud)
        self._set(baud=self._baud)

    def close(self) -> None:
        if not self.is_open:
            return
        self.is_open = False
        task = self._pump_task
        if task is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(task.cancel)
        with self._cond:
            if self._closed is None:
                self._closed = "closed"
            self._cond.notify_all()
