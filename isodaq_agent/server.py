"""The agent: serves this machine's serial ports to paired IsoDAQ clients over TLS.

Qt-free: asyncio for the network, a thread per open port for reading it (pyserial
blocks), and a one-thread executor per port for writes and line changes, so they
reach the device in the order the client sent them.
"""
from __future__ import annotations

import asyncio
import collections
import concurrent.futures
import fnmatch
import glob
import ipaddress
import logging
import os
import socket
import ssl
import threading
import time
from pathlib import Path

from isodaq_agent import protocol, security

log = logging.getLogger("isodaq_agent")

#: Seconds a new connection has to say hello or pair.
AUTH_TIMEOUT = 10.0
#: Failed hellos or pairings per address in a minute before it is turned away.
MAX_FAILURES = 10
FAILURE_WINDOW = 60.0
MAX_SESSIONS = 16
#: A client that falls this far behind the device is disconnected, rather than
#: the agent holding the bytes for it without limit.
MAX_BACKLOG = 8 << 20

_PARITIES = {"N", "E", "O", "M", "S"}
_FLOWS = {"none", "rtscts", "xonxoff"}


def list_ports() -> list[dict]:
    """The serial ports here, in the form IsoDAQ's port list uses."""
    try:
        from serial.tools import list_ports as lp
        ports = lp.comports()
    except Exception:
        return []
    out = []
    for p in ports:
        desc = (p.description or "").strip()
        if desc in ("n/a", p.device, p.name):
            desc = ""
        if not desc and (p.manufacturer or p.product):
            desc = " ".join(x for x in (p.manufacturer, p.product) if x)
        out.append({"device": p.device, "description": desc,
                    "vid": p.vid, "pid": p.pid, "serial": p.serial_number})
    return out


_CGNAT = ipaddress.ip_network("100.64.0.0/10")     # Tailscale and other VPNs


def local_address(ip: str) -> bool:
    """A loopback, private (LAN), link-local or VPN address: not the open internet."""
    try:
        addr = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if getattr(addr, "ipv4_mapped", None):
        addr = addr.ipv4_mapped
    return (addr.is_private or addr.is_loopback or addr.is_link_local
            or (addr.version == 4 and addr in _CGNAT))


def same_device(device: str) -> str:
    """One key per physical port, whichever name it was opened by."""
    return os.path.realpath(device) if device.startswith("/") else device.upper()


def serial_settings(msg: dict) -> dict:
    """Check an ``open`` message's line settings. Raises ``ValueError``."""
    baud = msg.get("baud", 115200)
    if not isinstance(baud, int) or not 50 <= baud <= 20_000_000:
        raise ValueError(f"baud rate {baud!r} isn't usable")
    bytesize = msg.get("bytesize", 8)
    if bytesize not in (5, 6, 7, 8):
        raise ValueError(f"data bits {bytesize!r} isn't usable")
    parity = msg.get("parity", "N")
    if parity not in _PARITIES:
        raise ValueError(f"parity {parity!r} isn't usable")
    stopbits = msg.get("stopbits", 1)
    if stopbits not in (1, 1.5, 2):
        raise ValueError(f"stop bits {stopbits!r} isn't usable")
    flow = msg.get("flow", "none")
    if flow not in _FLOWS:
        raise ValueError(f"flow control {flow!r} isn't usable")
    return {"baudrate": baud, "bytesize": bytesize, "parity": parity,
            "stopbits": stopbits, "rtscts": flow == "rtscts",
            "xonxoff": flow == "xonxoff"}


class OpenPort:
    """One serial port, opened for one client."""

    def __init__(self, device: str, ser, loop, on_data, on_lost):
        self.device = device
        self.key = device              # in AgentServer.open_ports
        self.ser = ser
        self._loop = loop
        self._on_data = on_data
        self._on_lost = on_lost
        self._running = True
        self.executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=f"agent-write-{device}")
        self._thread = threading.Thread(target=self._read, name=f"agent-read-{device}",
                                        daemon=True)
        self._thread.start()

    def _read(self) -> None:
        while self._running:
            try:
                waiting = self.ser.in_waiting
                chunk = self.ser.read(waiting if waiting > 0 else 1)
            except Exception as e:
                if self._running:
                    self._loop.call_soon_threadsafe(self._on_lost, str(e) or "port error")
                return
            if chunk:
                self._loop.call_soon_threadsafe(self._on_data, chunk)

    async def write(self, payload: bytes) -> None:
        await self._loop.run_in_executor(self.executor, self.ser.write, payload)

    async def apply(self, msg: dict) -> None:
        """A ``set`` message: baud, DTR, RTS, BREAK, in the order written here."""
        def run():
            if "baud" in msg:
                self.ser.baudrate = serial_settings({"baud": msg["baud"]})["baudrate"]
            if isinstance(msg.get("dtr"), bool):
                self.ser.dtr = msg["dtr"]
            if isinstance(msg.get("rts"), bool):
                self.ser.rts = msg["rts"]
            if isinstance(msg.get("break"), bool):
                self.ser.break_condition = msg["break"]
        await self._loop.run_in_executor(self.executor, run)

    def close(self) -> None:
        self._running = False
        try:
            self.ser.close()
        except Exception:
            pass
        self._thread.join(1.0)
        self.executor.shutdown(wait=False)


class AgentServer:
    """Listens for IsoDAQ clients. ``extra_ports`` are glob patterns of devices that
    may be opened although the OS doesn't list them as serial ports (a pty, a
    symlink in /dev/serial/by-id). Nothing else can be opened: a client can't use
    the agent to open an arbitrary file."""

    def __init__(self, home: Path | None = None, host: str = "0.0.0.0",
                 port: int = protocol.DEFAULT_PORT, name: str = "",
                 extra_ports: tuple = (), lan_only: bool = True):
        self.home = Path(home) if home else security.default_home()
        self.host = host
        self.port = port
        self.name = name or socket.gethostname()
        self.extra_ports = tuple(extra_ports)
        self.lan_only = lan_only
        cert, key = security.ensure_certificate(self.home)
        self.fingerprint = security.certificate_fingerprint(cert)
        self._ssl = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._ssl.minimum_version = ssl.TLSVersion.TLSv1_2
        self._ssl.load_cert_chain(str(cert), str(key))
        self.clients = security.Clients(self.home)
        self.pairing = security.Pairing(self.home)
        self.open_ports: dict[str, str] = {}      # device → client name
        self._sessions: set = set()
        self._failures: dict[str, collections.deque] = {}
        self._server = None
        self._loop = None
        self._thread = None
        self._ready = threading.Event()

    # ── Running ───────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._server = await asyncio.start_server(self._handle, self.host, self.port,
                                                  ssl=self._ssl)
        self.port = self._server.sockets[0].getsockname()[1]
        log.info("isodaq-agent %s listening on %s:%d", self.name, self.host, self.port)
        log.info("certificate fingerprint %s", protocol.show_fingerprint(self.fingerprint))

    async def serve_forever(self) -> None:
        await self.start()
        async with self._server:
            await self._server.serve_forever()

    def start_in_thread(self) -> None:
        """Run on a background thread (tests, embedding). Returns once listening."""
        def main():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self.start())
            self._ready.set()
            loop.run_forever()
            loop.close()
        self._thread = threading.Thread(target=main, name="isodaq-agent", daemon=True)
        self._thread.start()
        if not self._ready.wait(10):
            raise RuntimeError("agent didn't start")

    def stop(self) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            return

        async def shutdown():
            self._server.close()
            for session in list(self._sessions):
                session.cancel()
            await asyncio.sleep(0)
            loop.stop()
        asyncio.run_coroutine_threadsafe(shutdown(), loop)
        if self._thread is not None:
            self._thread.join(5)

    # ── Limits ────────────────────────────────────────────────────────────────

    def _failed(self, ip: str) -> None:
        self._failures.setdefault(ip, collections.deque()).append(time.monotonic())

    def _blocked(self, ip: str) -> bool:
        dq = self._failures.get(ip)
        if not dq:
            return False
        while dq and dq[0] < time.monotonic() - FAILURE_WINDOW:
            dq.popleft()
        return len(dq) >= MAX_FAILURES

    def ports(self) -> list[dict]:
        """The serial ports the OS lists, then the devices that match ``extra_ports``
        (``/dev/serial/by-id/*`` names stay the same when a board is re-plugged)."""
        out = list_ports()
        seen = {p["device"] for p in out}
        for pattern in self.extra_ports:
            for path in sorted(glob.glob(pattern)):
                if path not in seen:
                    seen.add(path)
                    target = os.path.realpath(path)
                    out.append({"device": path, "description": "" if target == path
                                else f"→ {target}", "vid": None, "pid": None,
                                "serial": None})
        return out

    def allowed(self, device: str) -> bool:
        if any(p["device"] == device for p in list_ports()):
            return True
        return any(fnmatch.fnmatchcase(device, pat) for pat in self.extra_ports)

    # ── One client ────────────────────────────────────────────────────────────

    async def _handle(self, reader, writer) -> None:
        peer = writer.get_extra_info("peername") or ("?", 0)
        ip = str(peer[0])
        if (self.lan_only and not local_address(ip)) or self._blocked(ip) \
                or len(self._sessions) >= MAX_SESSIONS:
            writer.close()
            return
        task = asyncio.current_task()
        self._sessions.add(task)
        session = _Session(self, reader, writer, ip)
        try:
            await session.run()
        except asyncio.CancelledError:
            pass
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError, OSError):
            pass
        except protocol.ProtocolError as e:
            log.warning("%s: %s", ip, e)
        finally:
            self._sessions.discard(task)
            session.close()


class _Session:
    def __init__(self, server: AgentServer, reader, writer, ip: str):
        self.server = server
        self.reader = reader
        self.writer = writer
        self.ip = ip
        self.client = ""
        self.port: OpenPort | None = None
        self._closed = False

    def send(self, frame: bytes) -> None:
        if not self._closed:
            self.writer.write(frame)

    def error(self, code: str, message: str) -> None:
        self.send(protocol.control("error", code=code, message=message))

    async def run(self) -> None:
        try:
            kind, msg = await asyncio.wait_for(protocol.read_frame(self.reader), AUTH_TIMEOUT)
        except asyncio.TimeoutError:
            return
        if not await self._authenticate(kind, msg):
            await self._drain()
            return
        while True:
            kind, msg = await protocol.read_frame(self.reader)
            if kind == protocol.DATA:
                if self.port is None:
                    self.error("not-open", "No port is open")
                else:
                    await self.port.write(msg)
                continue
            await self._control(msg)
            await self._drain()

    async def _drain(self) -> None:
        try:
            await self.writer.drain()
        except (ConnectionError, OSError):
            pass

    async def _authenticate(self, kind: int, msg) -> bool:
        srv = self.server
        if kind != protocol.CONTROL or msg["op"] not in ("hello", "pair"):
            self.error("auth", "Say hello or pair first")
            srv._failed(self.ip)
            return False
        if msg.get("version") != protocol.VERSION:
            self.error("version", f"This agent speaks protocol version {protocol.VERSION}")
            return False
        if msg["op"] == "hello":
            name = srv.clients.check(msg.get("token"))
            if name is None:
                srv._failed(self.ip)
                log.warning("%s: refused an unknown or revoked token", self.ip)
                self.error("auth", "This computer isn't paired with the agent (or was "
                                   "removed): pair again")
                return False
            self.client = name
        else:
            result = srv.pairing.verify(msg.get("proof"), srv.fingerprint)
            if result != "ok":
                srv._failed(self.ip)
                log.warning("%s: pairing refused (%s)", self.ip, result)
                if result == "wrong":
                    self.error("pair-wrong", "Wrong pairing code")
                else:
                    self.error("pair-none", "No pairing code is waiting: run "
                                            "'isodaq-agent pair' on the agent")
                return False
            name, token = srv.clients.add(str(msg.get("name") or self.ip))
            self.client = name
            log.info("%s: paired as %r", self.ip, name)
            self.send(protocol.control("paired", token=token, client=name))
        self.send(protocol.control("welcome", agent=srv.name, version=protocol.VERSION,
                                   client=self.client))
        log.info("%s: %s connected", self.ip, self.client)
        return True

    async def _control(self, msg: dict) -> None:
        op = msg["op"]
        loop = asyncio.get_running_loop()
        if op == "ping":
            self.send(protocol.control("pong", t=msg.get("t")))
        elif op == "list":
            ports = await loop.run_in_executor(None, self.server.ports)
            for p in ports:
                p["busy"] = self.server.open_ports.get(same_device(p["device"]), "")
            self.send(protocol.control("ports", ports=ports))
        elif op == "open":
            await self._open(msg)
        elif op == "set":
            if self.port is None:
                self.error("not-open", "No port is open")
                return
            try:
                await self.port.apply(msg)
            except Exception as e:
                self.error("set", str(e) or "The port refused that setting")
        elif op == "close":
            self._close_port()
            self.send(protocol.control("closed", reason="closed"))
        else:
            self.error("unknown", f"Unknown request {op!r}")

    async def _open(self, msg: dict) -> None:
        srv = self.server
        device = msg.get("port")
        if self.port is not None:
            self.error("open", f"{self.port.device} is already open on this connection")
            return
        if not isinstance(device, str) or not device:
            self.error("open", "Which port?")
            return
        try:
            settings = serial_settings(msg)
        except ValueError as e:
            self.error("open", str(e))
            return
        loop = asyncio.get_running_loop()
        if not await loop.run_in_executor(None, srv.allowed, device):
            self.error("open", f"{device} isn't a serial port on {srv.name}")
            return
        key = same_device(device)              # by-id links and ttyUSB0 are one board
        if key in srv.open_ports:
            self.error("busy", f"{device} is in use by {srv.open_ports[key]}")
            return
        srv.open_ports[key] = self.client        # claimed while it opens

        def open_serial():
            import serial
            ser = serial.Serial(timeout=0.05, write_timeout=5.0, **settings)
            ser.port = device
            for line in ("dtr", "rts"):
                if isinstance(msg.get(line), bool):
                    setattr(ser, line, msg[line])   # applied as the port opens
            ser.open()
            return ser
        try:
            ser = await loop.run_in_executor(None, open_serial)
        except Exception as e:
            srv.open_ports.pop(key, None)
            self.error("open", str(e) or f"Couldn't open {device}")
            return
        self.port = OpenPort(device, ser, loop, self._on_data, self._on_lost)
        self.port.key = key
        log.info("%s: %s opened %s at %d", self.ip, self.client, device, settings["baudrate"])
        self.send(protocol.control("opened", port=device))

    def _on_data(self, chunk: bytes) -> None:
        if self._closed:
            return
        self.send(protocol.data(chunk))
        transport = self.writer.transport
        if transport.get_write_buffer_size() > MAX_BACKLOG:
            log.warning("%s: %s fell too far behind; disconnecting", self.ip, self.client)
            self.close()

    def _on_lost(self, reason: str) -> None:
        if self._closed:
            return
        device = self.port.device if self.port else "port"
        log.info("%s: %s went away (%s)", self.ip, device, reason)
        self.send(protocol.control("closed", reason=f"{device}: {reason}"))
        self.close()

    def _close_port(self) -> None:
        port, self.port = self.port, None
        if port is not None:
            port.close()
            # The key from when it opened: an unplugged board's link no longer resolves.
            if self.server.open_ports.get(port.key) == self.client:
                del self.server.open_ports[port.key]

    def close(self) -> None:
        self._close_port()
        if not self._closed:
            self._closed = True
            try:
                self.writer.close()
            except Exception:
                pass
