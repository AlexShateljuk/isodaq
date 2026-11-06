"""
core/ble_reader.py — Bluetooth Low Energy serial transport (CMP16).

Talks to boards that expose a serial-over-BLE service: the Nordic UART Service
(the usual ESP32 / nRF choice), HM-10 style modules, Microchip Transparent UART
and u-blox SPS. Anything else that has one notifying and one writable
characteristic in the same service is tried as a fallback.

``bleak`` is asyncio-based, so the session runs its own event loop on this thread.
It is imported lazily, like paramiko. Same signals and ``configure`` /
``connect_port`` / ``disconnect_port`` / ``send`` surface as the other readers.
"""
from __future__ import annotations

import asyncio
import re

from PyQt6.QtCore import QThread, pyqtSignal

from core.stream_reader import LineSplitter, StreamReader, deliver, receive

MISSING_BLEAK = ("Bluetooth needs the 'bleak' package.\n"
                 "  Install it:  pip install bleak")
CONNECT_TIMEOUT_S = 15.0
SCAN_SECONDS = 5.0
#: Flush a partial line after this much silence, as the other readers do.
PARTIAL_FLUSH_S = StreamReader.PARTIAL_FLUSH_TIMEOUT

#: (name, service, device→app notify characteristic, app→device write characteristic)
UART_PROFILES = (
    ("Nordic UART", "6e400001-b5a3-f393-e0a9-e50e24dcca9e",
     "6e400003-b5a3-f393-e0a9-e50e24dcca9e", "6e400002-b5a3-f393-e0a9-e50e24dcca9e"),
    ("HM-10", "0000ffe0-0000-1000-8000-00805f9b34fb",
     "0000ffe1-0000-1000-8000-00805f9b34fb", "0000ffe1-0000-1000-8000-00805f9b34fb"),
    ("Microchip UART", "49535343-fe7d-4ae5-8fa9-9fafd205e455",
     "49535343-1e4d-4bd9-ba61-23c647249616", "49535343-8841-43f4-a8d4-ecbe34729bb3"),
    ("u-blox SPS", "2456e1b9-26e2-8f83-e744-f34f01e9d701",
     "2456e1b9-26e2-8f83-e744-f34f01e9d703", "2456e1b9-26e2-8f83-e744-f34f01e9d703"),
)
_UART_SERVICES = {p[1] for p in UART_PROFILES}
#: GAP, GATT, Device Information, Battery: every device has them, none carries data.
_STANDARD_SERVICES = ("00001800-", "00001801-", "0000180a-", "0000180f-")
_ADDRESS_IN_LABEL = re.compile(r"\[([^\[\]]+)\]\s*$")


def target_label(name: str, address: str) -> str:
    return f"{name} [{address}]" if name else address


def target_address(text: str) -> str:
    """The address from a ``Name [address]`` entry, or the text if it is one."""
    m = _ADDRESS_IN_LABEL.search(text)
    return m.group(1).strip() if m else text.strip()


def pick_characteristics(services) -> tuple[object, object, str] | None:
    """``(notify, write, profile name)`` from a device's GATT services, or ``None``."""
    by_uuid = {s.uuid.lower(): s for s in services}
    for name, service, notify_uuid, write_uuid in UART_PROFILES:
        s = by_uuid.get(service)
        if s is None:
            continue
        chars = {c.uuid.lower(): c for c in s.characteristics}
        if notify_uuid in chars and write_uuid in chars:
            return chars[notify_uuid], chars[write_uuid], name
    for s in services:
        if s.uuid.lower().startswith(_STANDARD_SERVICES):
            continue
        notify = next((c for c in s.characteristics
                       if {"notify", "indicate"} & set(c.properties)), None)
        write = next((c for c in s.characteristics
                      if {"write", "write-without-response"} & set(c.properties)), None)
        if notify is not None and write is not None:
            return notify, write, "generic"
    return None


class BLEScanner(QThread):
    """One scan: ``found`` carries dicts of ``address, name, rssi, uart``, serial
    devices first and then by signal strength. Unnamed non-serial devices (nearby
    phones, beacons…) are left out: they would bury what the user is looking for."""

    found = pyqtSignal(object)
    failed = pyqtSignal(str)

    def run(self) -> None:
        try:
            from bleak import BleakScanner
        except ImportError:
            self.failed.emit(MISSING_BLEAK)
            return
        try:
            seen = asyncio.run(BleakScanner.discover(timeout=SCAN_SECONDS, return_adv=True))
        except Exception as e:
            self.failed.emit(f"Bluetooth scan failed: {e or type(e).__name__}")
            return
        out = []
        for address, (device, adv) in seen.items():
            uart = bool(_UART_SERVICES & {u.lower() for u in (adv.service_uuids or [])})
            name = adv.local_name or device.name or ""
            if name or uart:
                out.append({"address": address, "name": name, "rssi": adv.rssi, "uart": uart})
        out.sort(key=lambda d: (not d["uart"], -(d["rssi"] or -999)))
        self.found.emit(out)


class BLEReader(QThread):
    line_received  = pyqtSignal(str, str)
    lines_received = pyqtSignal(object)
    error_occurred = pyqtSignal(str)
    connected      = pyqtSignal(str, int)
    disconnected   = pyqtSignal()

    shareable = True

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tap = None            # see StreamReader.tap
        self.frame_decoder = None  # see StreamReader.frame_decoder
        self._running = False
        self._disconnected_emitted = False
        self.lost = False
        self._address = ""
        self._name = ""
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client = None
        self._write_char = None
        self._write_lock: asyncio.Lock | None = None
        self._splitter = LineSplitter(StreamReader.MAX_LINE_BYTES)

    def configure(self, target: str) -> None:
        self._address = target_address(target)
        name = target.rpartition("[")[0].strip() if "[" in target else ""
        self._name = name

    # ── Connect / disconnect ──────────────────────────────────────────────────

    def connect_port(self) -> str | None:
        if not self._address:
            return "No Bluetooth device set"
        self._disconnected_emitted = False
        self.lost = False
        self._running = True
        self.start()
        return None

    def disconnect_port(self) -> None:
        self._running = False
        self.wait(int(CONNECT_TIMEOUT_S * 1000) + 3000)
        if not self._disconnected_emitted:
            self.disconnected.emit()
        self._disconnected_emitted = False

    def send(self, data: bytes) -> str | None:
        """Queue *data* for the device. A BLE write can take a while, so it runs on
        the session's loop rather than blocking the window; a failure is reported
        through ``error_occurred``."""
        loop, client = self._loop, self._client
        if loop is None or client is None or not client.is_connected:
            return "Not connected"
        future = asyncio.run_coroutine_threadsafe(self._write(data), loop)
        future.add_done_callback(self._report_write)
        return None

    def _report_write(self, future) -> None:
        if not future.cancelled() and future.exception() is not None:
            self.error_occurred.emit(f"Bluetooth write failed: {future.exception()}")

    async def _write(self, data: bytes) -> None:
        """In pieces that fit the link (ATT MTU minus its 3-byte header). The lock
        keeps two sends from interleaving."""
        size = max(20, (getattr(self._client, "mtu_size", 23) or 23) - 3)
        response = "write-without-response" not in self._write_char.properties
        async with self._write_lock:
            for i in range(0, len(data), size):
                await self._client.write_gatt_char(self._write_char, data[i:i + size],
                                                   response=response)

    @staticmethod
    def set_lines(dtr=None, rts=None) -> str | None:   # pylint: disable=unused-argument
        return "DTR/RTS are not available over Bluetooth"

    @staticmethod
    def set_break(on: bool) -> str | None:   # pylint: disable=unused-argument
        return "BREAK is not available over Bluetooth"

    # ── Worker thread ─────────────────────────────────────────────────────────

    def run(self) -> None:
        try:
            from bleak import BleakClient
        except ImportError:
            self.error_occurred.emit(MISSING_BLEAK)
            self._finish(lost=False)
            return
        try:
            was_online = asyncio.run(self._session(BleakClient))
        except Exception as e:
            self._online_cleanup()
            if not self._running:
                return
            self.error_occurred.emit(f"Bluetooth connection failed: {e or type(e).__name__}")
            self._finish(lost=False)
            return
        self._online_cleanup()
        if not self._running:
            return                       # disconnect_port() finishes up
        self.error_occurred.emit("Bluetooth link lost")
        self._finish(lost=was_online)

    def _online_cleanup(self) -> None:
        self._client = self._loop = self._write_char = self._write_lock = None

    def _finish(self, lost: bool) -> None:
        self._running = False
        self.lost = lost
        self._disconnected_emitted = True
        self.disconnected.emit()

    async def _session(self, client_cls) -> bool:
        loop = asyncio.get_running_loop()
        link_down = asyncio.Event()
        client = client_cls(self._address, timeout=CONNECT_TIMEOUT_S,
                            disconnected_callback=lambda _c: loop.call_soon_threadsafe(link_down.set))
        await client.connect()
        try:
            chosen = pick_characteristics(client.services)
            if chosen is None:
                raise RuntimeError("this device has no serial-like Bluetooth service")
            notify, write, _profile = chosen
            self._splitter = LineSplitter(StreamReader.MAX_LINE_BYTES)
            await client.start_notify(notify, self._on_notify)
            self._write_char, self._write_lock = write, asyncio.Lock()
            self._client, self._loop = client, loop
            self.connected.emit(f"ble://{self._name or self._address}", 0)
            while self._running and not link_down.is_set():
                await asyncio.sleep(0.05)
                deliver(self, self._splitter.flush_if_idle(PARTIAL_FLUSH_S))
            return True
        finally:
            if client.is_connected:
                try:
                    await client.disconnect()
                except Exception:
                    pass

    def _on_notify(self, _sender, data: bytearray) -> None:
        chunk = bytes(data)
        if self.tap is not None:
            self.tap(chunk)
        receive(self, self._splitter, chunk)
