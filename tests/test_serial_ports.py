"""Port listing with descriptions (CMP4) and finding a re-plugged board (CMP1)."""
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")
pytest.importorskip("serial")

from core.serial_reader import SerialReader


def _port(device, description="n/a", vid=None, pid=None, serial=None,
          manufacturer=None, product=None):
    return SimpleNamespace(device=device, name=device.rsplit("/", 1)[-1],
                           description=description, vid=vid, pid=pid,
                           serial_number=serial, manufacturer=manufacturer,
                           product=product)


@pytest.fixture
def ports(monkeypatch):
    from serial.tools import list_ports
    current = []
    monkeypatch.setattr(list_ports, "comports", lambda: list(current))
    return current


def test_port_info_descriptions(ports):
    ports += [
        _port("/dev/ttyUSB0", "CP2102 USB to UART Bridge Controller", 0x10C4, 0xEA60, "0001"),
        _port("/dev/ttyS0", "ttyS0"),                         # name only → no description
        _port("/dev/cu.debug", "n/a"),
        _port("COM7", "n/a", manufacturer="FTDI", product="FT232R"),
    ]
    info = {p["device"]: p for p in SerialReader.port_info()}
    assert info["/dev/ttyUSB0"]["description"].startswith("CP2102")
    assert info["/dev/ttyUSB0"]["vid"] == 0x10C4
    assert info["/dev/ttyS0"]["description"] == ""
    assert info["/dev/cu.debug"]["description"] == ""
    assert info["COM7"]["description"] == "FTDI FT232R"
    assert SerialReader.list_ports() == ["/dev/ttyUSB0", "/dev/ttyS0", "/dev/cu.debug", "COM7"]


def test_find_port_same_name_or_reenumerated(ports):
    ports.append(_port("/dev/ttyUSB0", "CP2102", 0x10C4, 0xEA60, "A1"))
    usb = SerialReader.usb_id("/dev/ttyUSB0")
    assert usb == (0x10C4, 0xEA60, "A1")
    assert SerialReader.find_port("/dev/ttyUSB0", usb) == "/dev/ttyUSB0"

    ports.clear()                                             # unplugged
    assert SerialReader.find_port("/dev/ttyUSB0", usb) is None

    ports.append(_port("/dev/ttyUSB1", "CP2102", 0x10C4, 0xEA60, "A1"))   # back, new name
    assert SerialReader.find_port("/dev/ttyUSB0", usb) == "/dev/ttyUSB1"

    ports[0] = _port("/dev/ttyUSB1", "CP2102", 0x10C4, 0xEA60, "B2")      # another board
    assert SerialReader.find_port("/dev/ttyUSB0", usb) is None


def test_find_port_accepts_existing_unlisted_path(ports, tmp_path):
    link = tmp_path / "by-id-link"
    assert SerialReader.find_port(str(link), None) is None
    link.write_text("")
    assert SerialReader.find_port(str(link), None) == str(link)
