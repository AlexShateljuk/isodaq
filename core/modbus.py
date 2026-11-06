"""
core/modbus.py — the Modbus protocol: frames, register maps and links (CMP18).

No Qt here and no dependency beyond the socket or serial object passed in. Read
functions 1–4 and write functions 5, 6 and 16, over RTU (serial) and TCP.
``core/modbus_reader.py`` runs the polling.
"""
from __future__ import annotations

import math
import socket
import struct
import time
from dataclasses import dataclass

TABLES = {"holding": 3, "input": 4, "coil": 1, "discrete": 2}
#: Name → (registers, struct code). A trailing "s" means the word order is swapped
#: (low word first), a common variation between devices.
FORMATS = {"u16": (1, "H"), "i16": (1, "h"), "u32": (2, "I"), "i32": (2, "i"), "f32": (2, "f"),
           "u32s": (2, "I"), "i32s": (2, "i"), "f32s": (2, "f")}
#: A single request may ask for at most this many (Modbus application spec).
MAX_REGISTERS = 125
MAX_BITS = 2000

EXCEPTION_NAMES = {1: "illegal function", 2: "illegal data address", 3: "illegal data value",
                   4: "device failure", 5: "acknowledge", 6: "device busy",
                   8: "memory parity error", 10: "gateway path unavailable",
                   11: "gateway target failed to respond"}


class ModbusTimeout(Exception):
    """The device didn't answer in time; the link itself may be fine."""


class ModbusFrameError(Exception):
    """An answer arrived but is damaged or doesn't match the request."""


class ModbusDeviceError(Exception):
    """The device answered with a Modbus exception."""


class LinkLost(Exception):
    """The serial port or TCP connection itself is gone."""


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


# ── Register map ──────────────────────────────────────────────────────────────

@dataclass
class Point:
    """One value on a device. ``address`` is the 0-based protocol address: holding
    register "40001" in a device manual is address 0."""

    name: str
    unit: int = 1
    table: str = "holding"
    address: int = 0
    fmt: str = "u16"
    scale: float = 1.0

    @property
    def is_bit(self) -> bool:
        return self.table in ("coil", "discrete")

    @property
    def size(self) -> int:
        return 1 if self.is_bit else FORMATS[self.fmt][0]

    @property
    def writable(self) -> bool:
        return self.table in ("holding", "coil")

    def validate(self) -> str | None:
        if not self.name.strip():
            return "A register needs a name."
        if any(c.isspace() or c in ":=" for c in self.name):
            return f"The name {self.name!r} can't contain spaces, ':' or '='."
        if self.table not in TABLES:
            return f"Unknown table: {self.table}"
        if self.fmt not in FORMATS:
            return f"Unknown format: {self.fmt}"
        if not 0 <= self.unit <= 255:
            return "The unit ID must be 0–255."
        if not 0 <= self.address <= 0xFFFF - (self.size - 1):
            return "The address must be 0–65535."
        if not math.isfinite(self.scale) or self.scale == 0:
            return "The scale must be a non-zero number."
        return None

    def to_dict(self) -> dict:
        return {"name": self.name, "unit": self.unit, "table": self.table,
                "address": self.address, "fmt": self.fmt, "scale": self.scale}

    @classmethod
    def from_dict(cls, d: dict) -> Point:
        return cls(name=str(d.get("name", "")), unit=int(d.get("unit", 1)),
                   table=str(d.get("table", "holding")), address=int(d.get("address", 0)),
                   fmt=str(d.get("fmt", "u16")), scale=float(d.get("scale", 1.0)))


def load_points(data: object) -> list[Point]:
    """Points from parsed JSON, skipping anything malformed and repeated names."""
    out: list[Point] = []
    seen: set[str] = set()
    if not isinstance(data, list):
        return out
    for d in data:
        try:
            p = Point.from_dict(d)
        except (AttributeError, TypeError, ValueError):
            continue
        if p.validate() is None and p.name not in seen:
            seen.add(p.name)
            out.append(p)
    return out


@dataclass
class ReadBlock:
    """Adjacent points read with one request."""

    unit: int
    table: str
    start: int
    count: int
    points: list[Point]


def plan_reads(points: list[Point]) -> list[ReadBlock]:
    """Group points into as few requests as possible. Only directly adjacent
    addresses are merged: reading across a gap can hit an address the device
    doesn't have, and then it refuses the whole request."""
    blocks: list[ReadBlock] = []
    keyed = sorted(points, key=lambda p: (p.unit, TABLES[p.table], p.address))
    for p in keyed:
        last = blocks[-1] if blocks else None
        limit = MAX_BITS if p.is_bit else MAX_REGISTERS
        end = p.address + p.size
        if (last and last.unit == p.unit and last.table == p.table
                and p.address <= last.start + last.count
                and end - last.start <= limit):
            last.count = max(last.count, end - last.start)
            last.points.append(p)
        else:
            blocks.append(ReadBlock(p.unit, p.table, p.address, p.size, [p]))
    return blocks


# ── PDUs ──────────────────────────────────────────────────────────────────────

def read_request(table: str, start: int, count: int) -> bytes:
    return struct.pack(">BHH", TABLES[table], start, count)


def check_exception(pdu: bytes) -> None:
    if pdu and pdu[0] & 0x80:
        code = pdu[1] if len(pdu) > 1 else 0
        raise ModbusDeviceError(EXCEPTION_NAMES.get(code, f"exception {code}"))


def decode_block(block: ReadBlock, pdu: bytes) -> dict[str, float]:
    """``{name: scaled value}`` from a read answer. A float that is NaN or infinite
    is left out: it isn't a reading."""
    check_exception(pdu)
    if len(pdu) < 2 or pdu[0] != TABLES[block.table] or pdu[1] != len(pdu) - 2:
        raise ModbusFrameError("malformed read response")
    data = pdu[2:]
    out: dict[str, float] = {}
    if block.table in ("coil", "discrete"):
        if len(data) != (block.count + 7) // 8:
            raise ModbusFrameError("wrong number of bits in response")
        for p in block.points:
            bit = p.address - block.start
            out[p.name] = float((data[bit // 8] >> (bit % 8)) & 1)
        return out
    if len(data) != block.count * 2:
        raise ModbusFrameError("wrong number of registers in response")
    for p in block.points:
        size, code = FORMATS[p.fmt]
        at = (p.address - block.start) * 2
        raw = data[at:at + size * 2]
        if p.fmt.endswith("s"):
            raw = raw[2:4] + raw[0:2]
        value = struct.unpack(">" + code, raw)[0]
        if math.isfinite(value):
            out[p.name] = value * p.scale
    return out


def write_request(point: Point, value: float) -> bytes:
    """The PDU writing *value* (in scaled units). ``ValueError`` if it can't fit."""
    if not point.writable:
        raise ValueError(f"{point.name} is read-only ({point.table})")
    if point.table == "coil":
        return struct.pack(">BHH", 5, point.address, 0xFF00 if value else 0)
    size, code = FORMATS[point.fmt]
    raw_value = value / point.scale
    if code != "f":
        rounded = round(raw_value)
        lo, hi = {"H": (0, 0xFFFF), "h": (-0x8000, 0x7FFF),
                  "I": (0, 0xFFFFFFFF), "i": (-0x80000000, 0x7FFFFFFF)}[code]
        if not lo <= rounded <= hi:
            raise ValueError(f"{value:g} doesn't fit {point.name} ({point.fmt})")
        raw_value = rounded
    raw = struct.pack(">" + code, raw_value)
    if size == 1:
        return bytes([6]) + struct.pack(">H", point.address) + raw
    if point.fmt.endswith("s"):
        raw = raw[2:4] + raw[0:2]
    return struct.pack(">BHHB", 16, point.address, size, len(raw)) + raw


def check_write(request: bytes, pdu: bytes) -> None:
    check_exception(pdu)
    expected = request[:5] if request[0] == 16 else request
    if pdu != expected:
        raise ModbusFrameError("write response doesn't match the request")


# ── Links ─────────────────────────────────────────────────────────────────────

class RtuLink:
    """Modbus RTU over a serial-like object with ``read`` / ``write`` /
    ``reset_input_buffer`` and a short read timeout."""

    def __init__(self, ser, timeout: float = 0.5, baud: int = 9600):
        self._ser = ser
        self.timeout = timeout
        # Frames are separated by 3.5 characters of silence; at most 11 bits each.
        self._silence = max(0.002, 3.5 * 11 / max(baud, 1))
        self.last_tx = b""
        self.last_rx = b""

    def close(self) -> None:
        self._ser.close()

    def _read(self, n: int, deadline: float) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = self._ser.read(n - len(buf))
            except Exception as e:
                raise LinkLost(str(e)) from e
            buf += chunk
            if len(buf) < n and time.monotonic() > deadline:
                raise ModbusTimeout
        return bytes(buf)

    def transact(self, unit: int, pdu: bytes) -> bytes:
        frame = bytes([unit]) + pdu
        frame += struct.pack("<H", crc16(frame))
        self.last_tx, self.last_rx = frame, b""
        time.sleep(self._silence)
        try:
            self._ser.reset_input_buffer()
            self._ser.write(frame)
        except Exception as e:
            raise LinkLost(str(e)) from e
        deadline = time.monotonic() + self.timeout
        head = self._read(2, deadline)
        self.last_rx = head
        function = head[1]
        if function & 0x80:
            rest = self._read(3, deadline)
        elif function in (1, 2, 3, 4):
            count = self._read(1, deadline)
            rest = count + self._read(count[0] + 2, deadline)
        elif function in (5, 6, 15, 16):
            rest = self._read(6, deadline)
        else:
            # Noise or a stray frame: let it drain, the length can't be known.
            time.sleep(self._silence)
            raise ModbusFrameError(f"unexpected function code {function}")
        answer = head + rest
        self.last_rx = answer
        if struct.unpack("<H", answer[-2:])[0] != crc16(answer[:-2]):
            raise ModbusFrameError("CRC error")
        if answer[0] != unit:
            raise ModbusFrameError(f"answer from unit {answer[0]}, asked unit {unit}")
        if function & 0x7F != pdu[0]:
            raise ModbusFrameError("answer to a different function")
        return answer[1:-2]


class TcpLink:
    """Modbus TCP: the same PDUs behind a 7-byte MBAP header."""

    def __init__(self, sock: socket.socket, timeout: float = 1.0):
        self._sock = sock
        self._sock.settimeout(timeout)
        self._tid = 0
        self.last_tx = b""
        self.last_rx = b""

    def close(self) -> None:
        self._sock.close()

    def _read(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = self._sock.recv(n - len(buf))
            except socket.timeout as e:
                raise ModbusTimeout from e
            except OSError as e:
                raise LinkLost(str(e)) from e
            if not chunk:
                raise LinkLost("the device closed the connection")
            buf += chunk
        return bytes(buf)

    def transact(self, unit: int, pdu: bytes) -> bytes:
        self._tid = (self._tid + 1) & 0xFFFF
        frame = struct.pack(">HHHB", self._tid, 0, len(pdu) + 1, unit) + pdu
        self.last_tx, self.last_rx = frame, b""
        try:
            self._sock.sendall(frame)
        except OSError as e:
            raise LinkLost(str(e)) from e
        while True:
            head = self._read(7)
            tid, proto, length, answer_unit = struct.unpack(">HHHB", head)
            if length < 2 or length > 260:
                raise LinkLost("not a Modbus TCP stream")
            body = self._read(length - 1)
            self.last_rx = head + body
            if proto != 0:
                raise LinkLost("not a Modbus TCP stream")
            # The late answer to a request that timed out earlier: skip it.
            if tid != self._tid:
                continue
            if answer_unit != unit:
                raise ModbusFrameError(f"answer from unit {answer_unit}, asked unit {unit}")
            return body
