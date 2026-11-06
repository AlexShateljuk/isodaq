"""Modbus protocol: frames, register maps and links against a real-frame slave."""
import struct
import sys

import pytest

from core.modbus import (LinkLost, ModbusDeviceError, ModbusFrameError, ModbusTimeout, Point,
                         ReadBlock, TcpLink, crc16, decode_block, load_points, plan_reads,
                         read_request, write_request)


# ── Frames, checked against values published outside this project ───────────────

@pytest.mark.parametrize("frame_hex", [
    "0103000000 0AC5CD",     # read 10 holding registers, widely quoted example
    "0103000000 02C40B",     # read 2 holding registers
    "010600010017 9804",     # write register 1 = 23, Modbus FAQ-style example
])
def test_crc_matches_published_frames(frame_hex):
    frame = bytes.fromhex(frame_hex.replace(" ", ""))
    assert struct.pack("<H", crc16(frame[:-2])) == frame[-2:]


def test_read_request_bytes():
    assert read_request("holding", 0, 10).hex() == "030000000a"
    assert read_request("input", 0x1234, 2).hex() == "0412340002"
    assert read_request("coil", 19, 19).hex() == "0100130013"      # spec's own example
    assert read_request("discrete", 196, 22).hex() == "0200c40016"


def _block(*points):
    blocks = plan_reads(list(points))
    assert len(blocks) == 1
    return blocks[0]


def test_decode_all_register_formats():
    # 123.456 is 0x42F6E979 in IEEE 754; -2 as i16 is 0xFFFE.
    regs = struct.pack(">H", 40000) + struct.pack(">H", 0xFFFE) + bytes.fromhex("42F6E979")
    regs += bytes.fromhex("E97942F6") + bytes.fromhex("00010000") + bytes.fromhex("00000001")
    pdu = bytes([3, len(regs)]) + regs
    b = _block(Point("u", address=0, fmt="u16"), Point("i", address=1, fmt="i16"),
               Point("f", address=2, fmt="f32"), Point("fs", address=4, fmt="f32s"),
               Point("big", address=6, fmt="u32"), Point("swap", address=8, fmt="u32s"))
    got = decode_block(b, pdu)
    assert got["u"] == 40000 and got["i"] == -2
    assert got["f"] == pytest.approx(123.456, abs=1e-3)
    assert got["fs"] == pytest.approx(123.456, abs=1e-3)
    assert got["big"] == 65536 and got["swap"] == 65536


def test_decode_scale_and_bits():
    b = _block(Point("t", address=0, scale=0.1))
    assert decode_block(b, bytes([3, 2, 0, 215]))["t"] == pytest.approx(21.5)
    # Spec example: coils 20–38 answer with bytes CD 6B 05.
    coils = [Point(f"c{i}", table="coil", address=19 + i) for i in range(19)]
    got = decode_block(_block(*coils), bytes.fromhex("0103CD6B05"))
    assert [int(got[f"c{i}"]) for i in range(8)] == [1, 0, 1, 1, 0, 0, 1, 1]   # 0xCD, LSB first


def test_nan_float_is_not_a_reading():
    b = _block(Point("f", fmt="f32"))
    assert decode_block(b, bytes([3, 4]) + bytes.fromhex("7FC00000")) == {}


@pytest.mark.parametrize("pdu, exc", [
    (bytes([0x83, 2]), ModbusDeviceError),
    (bytes([3, 4, 0, 1]), ModbusFrameError),          # byte count lies
    (bytes([4, 2, 0, 1]), ModbusFrameError),          # other function
    (bytes([3, 4, 0, 1, 0, 2]), ModbusFrameError),    # too many registers
])
def test_decode_rejects(pdu, exc):
    with pytest.raises(exc):
        decode_block(ReadBlock(1, "holding", 0, 1, [Point("x")]), pdu)


def test_plan_merges_only_adjacent_of_same_unit_and_table():
    pts = [Point("a", address=0), Point("b", address=1), Point("gap", address=5),
           Point("f", address=6, fmt="f32"), Point("in", table="input", address=2),
           Point("u2", unit=2, address=2)]
    got = [(b.unit, b.table, b.start, b.count) for b in plan_reads(pts)]
    assert got == [(1, "holding", 0, 2), (1, "holding", 5, 3), (1, "input", 2, 1),
                   (2, "holding", 2, 1)]


def test_plan_respects_request_limits():
    regs = [Point(f"r{i}", address=i) for i in range(130)]
    assert [b.count for b in plan_reads(regs)] == [125, 5]
    bits = [Point(f"c{i}", table="coil", address=i) for i in range(2005)]
    assert [b.count for b in plan_reads(bits)] == [2000, 5]


def test_plan_overlap_and_order():
    pts = [Point("b", address=1), Point("a32", address=0, fmt="u32")]
    b = plan_reads(pts)
    assert (b[0].start, b[0].count) == (0, 2)


@pytest.mark.parametrize("point, value, hex_", [
    (Point("r", address=1), 23, "0600010017"),
    (Point("r", address=1, fmt="i16"), -2, "060001fffe"),
    (Point("r", address=1, scale=0.1), 2.3, "0600010017"),
    (Point("c", table="coil", address=172), 1, "0500acff00"),     # spec's example
    (Point("c", table="coil", address=172), 0, "0500ac0000"),
    (Point("d", address=1, fmt="u32"), 65536, "1000010002040001" + "0000"),
    (Point("d", address=1, fmt="u32s"), 65536, "1000010002040000" + "0001"),
    (Point("f", address=0, fmt="f32"), 1.0, "1000000002043f800000"),
])
def test_write_requests(point, value, hex_):
    assert write_request(point, value).hex() == hex_


@pytest.mark.parametrize("point, value", [
    (Point("i", table="input"), 1), (Point("d", table="discrete"), 1),
    (Point("r"), 70000), (Point("r"), -1), (Point("s", fmt="i16"), 40000),
])
def test_write_rejects(point, value):
    with pytest.raises(ValueError):
        write_request(point, value)


@pytest.mark.parametrize("kwargs", [
    {"name": ""}, {"name": "a b"}, {"name": "a:b"}, {"name": "a=b"},
    {"name": "x", "table": "nope"}, {"name": "x", "fmt": "nope"}, {"name": "x", "unit": 300},
    {"name": "x", "address": 70000}, {"name": "x", "scale": 0},
    {"name": "x", "scale": float("nan")}, {"name": "x", "address": 65535, "fmt": "u32"},
])
def test_invalid_points(kwargs):
    assert Point(**kwargs).validate() is not None


def test_load_points_skips_junk():
    got = load_points([{"name": "a"}, {"name": "a"}, {"name": "b c"}, "junk",
                       {"name": "d", "address": "x"}, {"name": "e", "fmt": "f32", "scale": 2}])
    assert [p.name for p in got] == ["a", "e"]
    assert load_points("nope") == []


# ── Links, against a slave speaking real frames ─────────────────────────────────

import socket  # noqa: E402

from fake_modbus import RtuSlave, TcpSlave  # noqa: E402


def test_tcp_read_write_and_exception():
    slave = TcpSlave()
    slave.holding.update({0: 215, 1: 7})
    link = TcpLink(socket.create_connection(("127.0.0.1", slave.port)))
    try:
        b = _block(Point("a"), Point("b", address=1))
        assert decode_block(b, link.transact(1, read_request("holding", 0, 2))) == {"a": 215, "b": 7}
        assert link.last_tx[2:4] == b"\0\0"           # protocol id
        with pytest.raises(ModbusDeviceError, match="illegal data address"):
            decode_block(b, link.transact(1, read_request("holding", 50, 2)))
        link.transact(1, write_request(Point("w", address=9), 5))
        assert slave.holding[9] == 5
    finally:
        link.close()
        slave.stop()


def test_tcp_timeout_then_late_answers_are_skipped():
    slave = TcpSlave()
    slave.holding[0] = 1
    link = TcpLink(socket.create_connection(("127.0.0.1", slave.port)), timeout=0.15)
    try:
        slave.silent = True
        with pytest.raises(ModbusTimeout):
            link.transact(1, read_request("holding", 0, 1))
        slave.silent = False
        assert link.transact(1, read_request("holding", 0, 1))[2:] == b"\0\1"
    finally:
        link.close()
        slave.stop()


def test_tcp_closed_connection_is_link_lost():
    slave = TcpSlave()
    slave.holding[0] = 1
    link = TcpLink(socket.create_connection(("127.0.0.1", slave.port)), timeout=1)
    link.transact(1, read_request("holding", 0, 1))     # the slave has accepted by now
    slave.kick()
    with pytest.raises(LinkLost):
        for _ in range(3):
            link.transact(1, read_request("holding", 0, 1))
    link.close()
    slave.stop()


@pytest.mark.skipif(sys.platform == "win32", reason="needs POSIX ptys")
def test_rtu_read_timeout_crc_and_other_unit():
    serial = pytest.importorskip("serial")
    from core.modbus import RtuLink
    slave = RtuSlave(crc16)
    slave.holding.update({0: 215})
    ser = serial.Serial(slave.port, 115200, timeout=0.02)
    link = RtuLink(ser, timeout=0.2, baud=115200)
    try:
        pdu = link.transact(1, read_request("holding", 0, 1))
        assert pdu == bytes([3, 2, 0, 215])
        assert link.last_tx.hex() == "010300000001840a"     # published CRC for this request

        slave.corrupt_next = True
        with pytest.raises(ModbusFrameError, match="CRC"):
            link.transact(1, read_request("holding", 0, 1))

        assert link.transact(1, read_request("holding", 0, 1))[2:] == b"\0\xd7"   # recovered

        with pytest.raises(ModbusTimeout):                  # nobody at unit 9
            link.transact(9, read_request("holding", 0, 1))

        link.transact(1, write_request(Point("w", address=3, fmt="u32"), 65536))
        assert (slave.holding[3], slave.holding[4]) == (1, 0)
    finally:
        link.close()
        slave.stop()
