"""Bluetooth transport: GATT profile selection, and the reader's real thread and
asyncio flow against a fake ``bleak`` client. No radio is involved, so this can't
show how real hardware or an OS Bluetooth stack behaves."""
from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from types import SimpleNamespace as NS

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

from core.ble_reader import (BLEReader, BLEScanner, pick_characteristics, target_address,
                             target_label)

NUS = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
GAP = "00001800-0000-1000-8000-00805f9b34fb"


def char(uuid, *props):
    return NS(uuid=uuid, properties=list(props))


def service(uuid, *chars):
    return NS(uuid=uuid, characteristics=list(chars))


NUS_SERVICE = service(NUS, char(NUS_RX, "write-without-response", "write"), char(NUS_TX, "notify"))


# ── Profiles ────────────────────────────────────────────────────────────────────

def test_known_profile_wins_over_generic():
    other = service("aaaa0000-0000-0000-0000-000000000000",
                    char("aaaa0001-0000-0000-0000-000000000000", "notify", "write"))
    notify, write, name = pick_characteristics([other, NUS_SERVICE])
    assert name == "Nordic UART" and notify.uuid == NUS_TX and write.uuid == NUS_RX


def test_hm10_shares_one_characteristic():
    uuid = "0000ffe1-0000-1000-8000-00805f9b34fb"
    c = char(uuid, "notify", "write-without-response")
    got = pick_characteristics([service("0000ffe0-0000-1000-8000-00805f9b34fb", c)])
    assert got == (c, c, "HM-10")


def test_uuid_case_is_ignored():
    upper = service(NUS.upper(), char(NUS_RX.upper(), "write"), char(NUS_TX.upper(), "notify"))
    assert pick_characteristics([upper])[2] == "Nordic UART"


def test_generic_fallback_skips_standard_services():
    gap = service(GAP, char("00002a00-0000-1000-8000-00805f9b34fb", "notify", "write"))
    n = char("bbbb0001-0000-0000-0000-000000000000", "indicate")
    w = char("bbbb0002-0000-0000-0000-000000000000", "write")
    assert pick_characteristics([gap, service("bbbb0000-0000-0000-0000-000000000000", n, w)]) \
        == (n, w, "generic")


def test_device_without_a_serial_service():
    assert pick_characteristics([service(GAP, char("x", "read"))]) is None
    assert pick_characteristics([]) is None


def test_target_label_round_trip():
    assert target_address(target_label("My Board", "AA:BB:CC:DD:EE:FF")) == "AA:BB:CC:DD:EE:FF"
    assert target_address(target_label("", "AA:BB")) == "AA:BB"
    assert target_address("  C0A8-1234  ") == "C0A8-1234"
    assert target_address("Odd [name] [AA:BB]") == "AA:BB"


# ── Session, with a fake bleak ──────────────────────────────────────────────────

class FakeClient:
    instances: list = []
    services_for_next = [NUS_SERVICE]
    fail_connect: Exception | None = None

    def __init__(self, address, timeout=None, disconnected_callback=None):
        self.address, self._on_drop = address, disconnected_callback
        self.is_connected = False
        self.mtu_size = 23
        self.services = FakeClient.services_for_next
        self.writes = []
        self.responses = []
        self._notify = None
        self._loop = None
        FakeClient.instances.append(self)

    async def connect(self):
        if FakeClient.fail_connect:
            raise FakeClient.fail_connect
        self._loop = asyncio.get_running_loop()
        self.is_connected = True

    async def start_notify(self, characteristic, callback):
        self.notify_char, self._notify = characteristic, callback

    async def write_gatt_char(self, characteristic, data, response=False):
        await asyncio.sleep(0)           # would interleave without the lock
        self.write_char = characteristic
        self.writes.append(bytes(data))
        self.responses.append(response)

    async def disconnect(self):
        self.is_connected = False

    # The device's side:
    def push(self, data):
        self._loop.call_soon_threadsafe(self._notify, None, bytearray(data))

    def drop(self):
        self.is_connected = False
        threading.Thread(target=self._on_drop, args=(self,)).start()


@pytest.fixture
def fake_bleak(monkeypatch):
    FakeClient.instances = []
    FakeClient.services_for_next = [NUS_SERVICE]
    FakeClient.fail_connect = None
    module = NS(BleakClient=FakeClient, BleakScanner=NS())
    monkeypatch.setitem(sys.modules, "bleak", module)
    return module


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


def wait(qapp, cond, sec=5.0):
    end = time.monotonic() + sec
    while time.monotonic() < end and not cond():
        qapp.processEvents()
        time.sleep(0.005)
    assert cond()


class Session:
    def __init__(self, target="Board [AA:BB]"):
        self.reader = BLEReader()
        self.lines, self.raw, self.errors, self.up, self.down = [], [], [], [], []
        self.reader.lines_received.connect(self._on_lines)
        self.reader.error_occurred.connect(self.errors.append)
        self.reader.connected.connect(lambda label, _b: self.up.append(label))
        self.reader.disconnected.connect(lambda: self.down.append(1))
        self.reader.configure(target)
        assert self.reader.connect_port() is None

    def _on_lines(self, batch):
        self.lines.extend(x[0] for x in batch)
        self.raw.extend(x[3] for x in batch)


def test_receive_across_notifications(qapp, fake_bleak):
    s = Session()
    wait(qapp, lambda: s.up)
    assert s.up == ["ble://Board"]
    client = FakeClient.instances[0]
    assert client.address == "AA:BB" and client.notify_char.uuid == NUS_TX
    client.push(b"temp=2")                # a line split across BLE packets
    client.push(b"1.5\r\nnext\n")
    wait(qapp, lambda: len(s.lines) == 2)
    assert s.lines == ["temp=21.5", "next"]
    s.reader.disconnect_port()
    qapp.processEvents()
    assert s.down == [1] and not s.reader.lost and not client.is_connected


def test_partial_line_is_flushed(qapp, fake_bleak):
    s = Session()
    wait(qapp, lambda: s.up)
    FakeClient.instances[0].push(b"login: ")
    wait(qapp, lambda: s.lines)
    assert s.lines == ["login: "]
    s.reader.disconnect_port()


def test_writes_are_chunked_ordered_and_unmixed(qapp, fake_bleak):
    s = Session()
    wait(qapp, lambda: s.up)
    client = FakeClient.instances[0]
    assert s.reader.send(b"A" * 45) is None
    assert s.reader.send(b"B" * 45) is None
    wait(qapp, lambda: len(client.writes) == 6)
    assert [len(w) for w in client.writes] == [20, 20, 5, 20, 20, 5]   # MTU 23 − 3
    assert b"".join(client.writes) == b"A" * 45 + b"B" * 45
    assert client.write_char.uuid == NUS_RX
    assert set(client.responses) == {False}    # write-without-response is available
    s.reader.disconnect_port()


def test_write_failure_is_reported(qapp, fake_bleak, monkeypatch):
    s = Session()
    wait(qapp, lambda: s.up)

    async def boom(*_a, **_k):
        raise OSError("not permitted")
    monkeypatch.setattr(FakeClient.instances[0], "write_gatt_char", boom)
    assert s.reader.send(b"x") is None
    wait(qapp, lambda: s.errors)
    assert "not permitted" in s.errors[0]
    s.reader.disconnect_port()


def test_link_loss_is_flagged(qapp, fake_bleak):
    s = Session()
    wait(qapp, lambda: s.up)
    FakeClient.instances[0].drop()
    wait(qapp, lambda: s.down)
    assert s.reader.lost and "lost" in s.errors[-1]
    assert s.reader.send(b"x") == "Not connected"
    wait(qapp, lambda: not s.reader.isRunning())


def test_connect_failure_is_not_a_lost_link(qapp, fake_bleak):
    FakeClient.fail_connect = TimeoutError("no answer")
    s = Session()
    wait(qapp, lambda: s.down)
    assert not s.up and not s.reader.lost and "no answer" in s.errors[0]


def test_device_without_serial_service_explains_and_hangs_up(qapp, fake_bleak):
    FakeClient.services_for_next = [service(GAP, char("x", "read"))]
    s = Session()
    wait(qapp, lambda: s.down)
    assert "no serial-like" in s.errors[0] and not FakeClient.instances[0].is_connected


def test_missing_bleak_explains(qapp, monkeypatch):
    monkeypatch.setitem(sys.modules, "bleak", None)     # import raises ImportError
    s = Session()
    wait(qapp, lambda: s.down)
    assert "pip install bleak" in s.errors[0]


def test_scan_sorts_and_filters(qapp, fake_bleak):
    def adv(name, rssi, uuids=()):
        return NS(local_name=name, rssi=rssi, service_uuids=list(uuids))

    async def discover(timeout=None, return_adv=False):
        assert return_adv
        return {"1": (NS(name=None), adv(None, -40)),                   # anonymous: dropped
                "2": (NS(name="Phone"), adv("Phone", -30)),
                "3": (NS(name=None), adv(None, -80, [NUS.upper()])),    # serial, unnamed: kept
                "4": (NS(name="ESP32"), adv("ESP32", -60, [NUS]))}
    fake_bleak.BleakScanner = NS(discover=discover)
    got, failed = [], []
    scan = BLEScanner()
    scan.found.connect(got.append)
    scan.failed.connect(failed.append)
    scan.start()
    wait(qapp, lambda: got or failed)
    scan.wait(2000)
    assert not failed
    assert [d["address"] for d in got[0]] == ["4", "3", "2"]
    assert got[0][0]["uart"] and not got[0][2]["uart"]


def test_scan_failure_is_reported(qapp, fake_bleak):
    async def discover(timeout=None, return_adv=False):
        raise OSError("Bluetooth is off")
    fake_bleak.BleakScanner = NS(discover=discover)
    failed = []
    scan = BLEScanner()
    scan.failed.connect(failed.append)
    scan.start()
    wait(qapp, lambda: failed)
    scan.wait(2000)
    assert "Bluetooth is off" in failed[0]
