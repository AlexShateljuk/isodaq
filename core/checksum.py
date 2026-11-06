"""
core/checksum.py — checksums for frames being sent (CMP9), checked on receive too.

No Qt. Every algorithm returns an integer; :func:`append` puts it on a frame the
way the user chose:

  high_first / low_first  raw bytes (CRC-16/MODBUS goes low byte first on the wire)
  hex                     upper-case hex text, ``AT+X4B37``
  star                    ``*`` + hex text, the NMEA form: ``$GPGLL,…*1D``

The star form leaves a leading ``$`` or ``!`` out of the sum, as NMEA does, and
it is the form :func:`star_mismatch` can check on received lines: it has a marker
saying where the checksum starts, so ordinary log lines are never mistaken for it.
"""
from __future__ import annotations

import re
import zlib
from dataclasses import dataclass
from typing import Callable

from core.modbus import crc16 as crc16_modbus


def sum8(data: bytes) -> int:
    return sum(data) & 0xFF


def xor8(data: bytes) -> int:
    out = 0
    for byte in data:
        out ^= byte
    return out


def lrc8(data: bytes) -> int:
    return -sum(data) & 0xFF


def crc8(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def crc8_maxim(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8C if crc & 1 else crc >> 1
    return crc


def _crc16_msb(data: bytes, crc: int) -> int:
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return crc


def crc16_xmodem(data: bytes) -> int:
    return _crc16_msb(data, 0)


def crc16_ccitt(data: bytes) -> int:
    return _crc16_msb(data, 0xFFFF)


def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


@dataclass(frozen=True)
class Algorithm:
    label: str
    size: int                        # bytes
    compute: Callable[[bytes], int]
    low_first: bool = False          # the usual byte order of this checksum


ALGORITHMS: dict[str, Algorithm] = {
    "sum8":         Algorithm("Sum-8", 1, sum8),
    "xor8":         Algorithm("XOR-8", 1, xor8),
    "lrc8":         Algorithm("LRC-8", 1, lrc8),
    "crc8":         Algorithm("CRC-8", 1, crc8),
    "crc8-maxim":   Algorithm("CRC-8/Maxim", 1, crc8_maxim),
    "crc16-modbus": Algorithm("CRC-16/Modbus", 2, crc16_modbus, low_first=True),
    "crc16-ccitt":  Algorithm("CRC-16/CCITT", 2, crc16_ccitt),
    "crc16-xmodem": Algorithm("CRC-16/XMODEM", 2, crc16_xmodem),
    "crc32":        Algorithm("CRC-32", 4, crc32),
}

FORMATS = ("high_first", "low_first", "hex", "star")

_HEX_ONLY = re.compile(r"[0-9A-Fa-f]+")
_HEX_NOISE = re.compile(r"0[xX]|[,;:]")


def _star_body(data: bytes) -> bytes:
    return data[1:] if data[:1] in (b"$", b"!") else data


def tail_for(algorithm: str, fmt: str, data: bytes) -> bytes:
    """The bytes to put after *data*."""
    algo = ALGORITHMS[algorithm]
    if fmt == "star":
        value = algo.compute(_star_body(data))
        return b"*" + f"{value:0{algo.size * 2}X}".encode()
    value = algo.compute(data)
    if fmt == "hex":
        return f"{value:0{algo.size * 2}X}".encode()
    return value.to_bytes(algo.size, "little" if fmt == "low_first" else "big")


def star_mismatch(algorithm: str, line: str) -> bool:
    """True if *line* ends in ``*<hex>`` of the right width and that isn't its
    checksum. A line without that ending has nothing to check, so it isn't a
    mismatch; neither is a line that won't encode back to the bytes that came in."""
    algo = ALGORITHMS[algorithm]
    body, star, given = line.rstrip().rpartition("*")
    if not star or len(given) != algo.size * 2 or not _HEX_ONLY.fullmatch(given):
        return False
    if "\ufffd" in body:
        return False
    return int(given, 16) != algo.compute(_star_body(body.encode()))


def parse_hex(text: str) -> bytes:
    """``01 03 0A``, ``0x55 0xAA``, ``55,AA`` and ``55AA`` are all accepted."""
    cleaned = _HEX_NOISE.sub(" ", text)
    try:
        out = bytes.fromhex(cleaned)
    except ValueError:
        out = b""
    if not out:
        raise ValueError(f"Not hex bytes: {text.strip()}")
    return out
