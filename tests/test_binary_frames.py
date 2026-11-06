"""Binary frame decoding (CMP15). Frames are built here with struct, by hand, as a
device's firmware would lay them out, and never with the decoder's own code."""
import math
import random
import struct

import pytest

from core.binary_frames import (MAX_ANY_FLOATS, MAX_PAYLOAD, PRESETS, Field, FrameDecoder,
                                FrameSpec, decode_sample, looks_like_text)

TAIL = bytes([0x00, 0x00, 0x80, 0x7F])       # from the VOFA+ JustFloat documentation


def lines_of(items):
    return [ln for item in items if not isinstance(item, bytes) for ln in item[0]]


def text_of(items):
    return b"".join(item for item in items if isinstance(item, bytes))


def basic(**kw):
    options = dict(header="AA 55", fields=[Field("temp", "i16", 0.1), Field("rpm", "u16")])
    options.update(kw)
    return FrameSpec(**options)


def frame(temp=215, rpm=1500):
    return b"\xAA\x55" + struct.pack("<hH", temp, rpm)


# ── Layouts ───────────────────────────────────────────────────────────────────

def test_header_and_fields():
    d = FrameDecoder(basic())
    assert lines_of(d.feed(frame())) == ["temp:21.5 rpm:1500"]
    assert d.frames == 1 and d.last == {"temp": pytest.approx(21.5), "rpm": 1500}


def test_raw_bytes_travel_with_the_frame():
    data = frame()
    (lines, raw), = FrameDecoder(basic()).feed(data)
    assert raw == data


@pytest.mark.parametrize("big", [False, True])
@pytest.mark.parametrize("name, code, value, shown", [
    ("u8", "B", 200, "200"), ("i8", "b", -100, "-100"),
    ("u16", "H", 60000, "60000"), ("i16", "h", -30000, "-30000"),
    ("u32", "I", 4000000000, "4000000000"), ("i32", "i", -2000000000, "-2000000000"),
    ("u64", "Q", 2 ** 63 + 1, str(2 ** 63 + 1)), ("i64", "q", -(2 ** 62), str(-(2 ** 62))),
    ("f32", "f", 1.5, "1.5"), ("f64", "d", 1.0 / 3, "0.3333333333333333"),
])
def test_every_type_in_both_byte_orders(big, name, code, value, shown):
    spec = FrameSpec(header="7E", fields=[Field("x", name)], big_endian=big)
    data = b"\x7E" + struct.pack((">" if big else "<") + code, value)
    assert lines_of(FrameDecoder(spec).feed(data)) == [f"x:{shown}"]


def test_skip_ignores_bytes():
    spec = FrameSpec(header="7E", fields=[Field("a", "u8"), Field("", "skip"), Field("b", "u8")])
    assert lines_of(FrameDecoder(spec).feed(b"\x7E\x01\xFF\x02")) == ["a:1 b:2"]


def test_tail_only_finds_fixed_frames_after_a_partial_one():
    spec = FrameSpec(tail="0D 0A", fields=[Field("a", "u16"), Field("b", "u16")])
    data = struct.pack("<HH", 1, 2) + b"\r\n" + struct.pack("<HH", 3, 4) + b"\r\n"
    assert lines_of(FrameDecoder(spec).feed(b"\x99" + data)) == ["a:1 b:2", "a:3 b:4"]


def test_wrong_tail_isnt_a_frame():
    spec = basic(tail="0A")
    d = FrameDecoder(spec)
    assert lines_of(d.feed(frame() + b"\x0B" + frame() + b"\x0A")) == ["temp:21.5 rpm:1500"]
    assert d.frames == 1 and d.bad_length == 1


def test_many_channels_split_over_lines_quick_plot_accepts():
    from core.quick_plot import quick_parse
    fields = [Field(f"c{i}", "u8") for i in range(20)]
    spec = FrameSpec(header="7E", fields=fields)
    lines = lines_of(FrameDecoder(spec).feed(b"\x7E" + bytes(range(20))))
    assert len(lines) == 3
    merged = {}
    for line in lines:
        merged.update(quick_parse(line))
    assert merged == {f"c{i}": float(i) for i in range(20)}


def test_numbers_are_ones_quick_plot_reads():
    from core.quick_plot import quick_parse
    spec = FrameSpec(tail="00 00 80 7F", any_floats=True)
    d = FrameDecoder(spec)
    data = struct.pack("<ff", 1e9, -1.5e-7) + TAIL
    lines = lines_of(d.feed(TAIL + data))
    assert quick_parse(lines[0]) == {"ch1": pytest.approx(1e9), "ch2": pytest.approx(-1.5e-7, rel=1e-5)}


# ── Length ────────────────────────────────────────────────────────────────────

def test_length_counts_the_payload():
    spec = FrameSpec(header="AA", length="u8", fields=[Field("a", "u16")])
    assert lines_of(FrameDecoder(spec).feed(b"\xAA\x02\x07\x00")) == ["a:7"]


def test_length_big_endian_u16():
    spec = FrameSpec(header="AA", length="u16", big_endian=True, fields=[Field("a", "u16")])
    assert lines_of(FrameDecoder(spec).feed(b"\xAA\x00\x02\x00\x07")) == ["a:7"]


def test_length_that_counts_more_than_the_payload():
    spec = FrameSpec(header="AA", length="u8", length_extra=3, fields=[Field("a", "u16")])
    assert lines_of(FrameDecoder(spec).feed(b"\xAA\x05\x07\x00")) == ["a:7"]


def test_payload_can_hold_several_records_with_the_raw_bytes_once():
    spec = FrameSpec(header="AA", length="u8", fields=[Field("a", "u8"), Field("b", "u8")])
    data = b"\xAA\x04\x01\x02\x03\x04"
    (l1, r1), (l2, r2) = FrameDecoder(spec).feed(data)
    assert (l1, l2) == (["a:1 b:2"], ["a:3 b:4"])
    assert r1 == data and r2 == b""


@pytest.mark.parametrize("length", [0, 3, 255])
def test_bad_length_recovers_at_the_next_frame(length):
    spec = FrameSpec(header="AA", length="u8", fields=[Field("a", "u16")])
    d = FrameDecoder(spec)
    good = b"\xAA\x02\x07\x00"
    # 255 isn't whole records of 2 bytes, 0 and 3 are refused too: all without waiting.
    assert lines_of(d.feed(bytes([0xAA, length]) + good)) == ["a:7"]
    assert d.bad_length == 1


def test_a_length_too_big_to_be_true_isnt_waited_for():
    spec = FrameSpec(header="AA", length="u16", fields=[Field("a", "u8")])
    d = FrameDecoder(spec)
    too_big = MAX_PAYLOAD + 1
    good = b"\xAA\x01\x00\x09"
    assert lines_of(d.feed(b"\xAA" + too_big.to_bytes(2, "little") + good)) == ["a:9"]


# ── Checksums ─────────────────────────────────────────────────────────────────

def test_modbus_crc_on_a_published_frame():
    # A real Modbus answer shape: the check value is from the Modbus examples.
    spec = FrameSpec(header="01 03", fields=[Field("a", "u32")], big_endian=True,
                     checksum="crc16-modbus", checksum_low_first=True)
    assert lines_of(FrameDecoder(spec).feed(bytes.fromhex("010300000002C40B"))) == ["a:2"]


@pytest.mark.parametrize("over, covered", [
    ("frame", b"\xAA\x02\x07\x00"),
    ("after_header", b"\x02\x07\x00"),
    ("payload", b"\x07\x00"),
])
def test_checksum_range(over, covered):
    spec = FrameSpec(header="AA", length="u8", fields=[Field("a", "u16")], checksum="sum8",
                     checksum_over=over)
    data = b"\xAA\x02\x07\x00" + bytes([sum(covered) & 0xFF])
    assert lines_of(FrameDecoder(spec).feed(data)) == ["a:7"]


def test_bad_checksum_is_counted_and_the_next_frame_decoded():
    spec = basic(checksum="xor8")
    body = frame()
    good = body + bytes([_xor(body)])
    bad = body + bytes([_xor(body) ^ 1])
    d = FrameDecoder(spec)
    assert lines_of(d.feed(bad + good)) == ["temp:21.5 rpm:1500"]
    assert d.frames == 1 and d.bad_checksum == 1


def _xor(data):
    out = 0
    for b in data:
        out ^= b
    return out


# ── The stream ────────────────────────────────────────────────────────────────

def stream_of_frames(n=50):
    rng = random.Random(3)
    data = b""
    expect = []
    for _ in range(n):
        t, r = rng.randint(-300, 300), rng.randint(0, 65535)
        data += frame(t, r)
        expect.append(f"temp:{t * 0.1:.10g} rpm:{r}")
    return data, expect


def test_chunk_boundaries_dont_matter_at_any_split():
    data, expect = stream_of_frames(8)
    for at in range(len(data) + 1):
        d = FrameDecoder(basic())
        got = lines_of(d.feed(data[:at])) + lines_of(d.feed(data[at:]))
        assert got == expect


def test_byte_by_byte_is_the_same_as_all_at_once():
    data, expect = stream_of_frames()
    d = FrameDecoder(basic())
    got = []
    for i in range(len(data)):
        got += lines_of(d.feed(data[i:i + 1]))
    assert got == expect


def test_header_bytes_inside_the_data_dont_lose_frames():
    data = frame(0x55AA, 0x55AA)              # little-endian, both values read AA 55
    d = FrameDecoder(basic())
    assert len(lines_of(d.feed(data * 3))) == 3


def test_starting_mid_frame():
    data, expect = stream_of_frames(5)
    d = FrameDecoder(basic())
    got = lines_of(d.feed(data[3:]))
    assert got[-4:] == expect[-4:]


def test_text_between_frames_is_passed_on_in_order():
    boot = b"I (120) boot: ready\r\n"
    items = FrameDecoder(basic()).feed(boot + frame() + b"E (200) lost\r\n" + frame())
    kinds = [isinstance(i, bytes) for i in items]
    assert kinds == [True, False, True, False]
    assert items[0] == boot


def test_text_can_be_turned_off_but_is_still_counted():
    d = FrameDecoder(basic(), show_other=False)
    items = d.feed(b"hello\r\n" + frame())
    assert text_of(items) == b"" and d.other_bytes == 7 and d.frames == 1


def test_binary_noise_isnt_passed_on_as_text():
    rng = random.Random(1)
    noise = bytes(rng.getrandbits(8) for _ in range(300)).replace(b"\xAA", b"\x01")
    items = FrameDecoder(basic()).feed(noise + frame())
    assert text_of(items) == b"" and lines_of(items) == ["temp:21.5 rpm:1500"]


@pytest.mark.parametrize("data, expected", [
    (b"I (120) boot\r\n", True), ("привіт\r\n".encode(), True), (b"\x1b[0;32mok\x1b[0m\n", True),
    (b"\x00\x01\x02\x03\xff\xfe", False), (b"", False), (b"\xAA", False),
])
def test_looks_like_text(data, expected):
    assert looks_like_text(data) is expected


@pytest.mark.parametrize("spec", [
    basic(), basic(checksum="crc16-ccitt"), basic(tail="0D 0A"),
    FrameSpec(header="AA", length="u16", fields=[Field("a", "u16")], checksum="sum8"),
    FrameSpec(tail="00 00 80 7F", any_floats=True),
    FrameSpec(tail="0A", fields=[Field("a", "u8")]),
])
def test_random_bytes_never_raise_or_grow_the_buffer(spec):
    rng = random.Random(11)
    d = FrameDecoder(spec)
    for _ in range(300):
        d.feed(bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 400))))
        assert len(d._buf) <= MAX_PAYLOAD + 64
    assert not d._other


# ── VOFA+ JustFloat ───────────────────────────────────────────────────────────

def justfloat(*values):
    return struct.pack(f"<{len(values)}f", *values) + TAIL


def test_justfloat_as_documented():
    d = FrameDecoder(PRESETS["VOFA+ JustFloat"])
    items = d.feed(TAIL + justfloat(1.5, -2.25, 3.0, 100.0))
    assert lines_of(items) == ["ch1:1.5 ch2:-2.25 ch3:3 ch4:100"]
    assert items[-1][1] == justfloat(1.5, -2.25, 3.0, 100.0)


def test_justfloat_doesnt_trust_what_comes_before_the_first_tail():
    d = FrameDecoder(PRESETS["VOFA+ JustFloat"])
    frames = justfloat(1.0, 2.0) * 3
    assert lines_of(d.feed(frames[3:])) == ["ch1:1 ch2:2", "ch1:1 ch2:2"]


def test_justfloat_tail_bytes_across_two_floats_dont_end_the_frame():
    # 0.0 ends in 00 00, and this float starts 80 7F: together they read as a tail.
    across = struct.unpack("<f", b"\x80\x7F\x20\x41")[0]
    d = FrameDecoder(PRESETS["VOFA+ JustFloat"])
    lines = lines_of(d.feed(TAIL + justfloat(0.0, across, 5.0)))
    assert len(lines) == 1 and lines[0].startswith("ch1:0 ch2:") and lines[0].endswith("ch3:5")


def test_justfloat_chunks_and_channel_count_changes():
    data = TAIL + justfloat(1.0) + justfloat(2.0, 3.0) + justfloat(4.0)
    d = FrameDecoder(PRESETS["VOFA+ JustFloat"])
    got = []
    for i in range(len(data)):
        got += lines_of(d.feed(data[i:i + 1]))
    assert got == ["ch1:1", "ch1:2 ch2:3", "ch1:4"]


def test_justfloat_recovers_after_losing_a_byte():
    d = FrameDecoder(PRESETS["VOFA+ JustFloat"])
    good = justfloat(1.0, 2.0)
    damaged = good[:5] + good[6:]
    lines = lines_of(d.feed(TAIL + good + damaged + good + good + good))
    assert lines[0] == "ch1:1 ch2:2" and lines[-1] == "ch1:1 ch2:2"


def test_justfloat_lost_sync_doesnt_wait_for_ever():
    d = FrameDecoder(PRESETS["VOFA+ JustFloat"])
    d.feed(TAIL)
    d.feed(b"\x01\x02\x03\x04" * (MAX_ANY_FLOATS + 2))
    assert not d._synced
    assert lines_of(d.feed(TAIL + justfloat(7.0))) == ["ch1:7"]


def test_nan_and_infinity_are_left_out_but_the_rest_stays():
    d = FrameDecoder(FrameSpec(header="7E", fields=[Field("a", "f32"), Field("b", "f32")]))
    data = b"\x7E" + struct.pack("<ff", math.nan, 2.0)
    assert lines_of(d.feed(data)) == ["b:2"]
    assert d.non_finite == 1 and "a" not in d.last


# ── SerialPlot ────────────────────────────────────────────────────────────────

def test_serialplot_framed_format():
    # From its framedreader.cpp: sync, size in bytes of the payload, interleaved
    # samples, then the sum of the payload bytes alone.
    payload = struct.pack("<HHHH", 1, 2, 3, 4)
    data = b"\xAA\xBB" + bytes([len(payload)]) + payload + bytes([sum(payload) & 0xFF])
    d = FrameDecoder(PRESETS["SerialPlot"])
    assert lines_of(d.feed(data)) == ["ch1:1 ch2:2", "ch1:3 ch2:4"]
    assert d.bad_checksum == 0


# ── Definitions ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("spec, problem", [
    (FrameSpec(fields=[Field("a")]), "header or a tail"),
    (FrameSpec(header="ZZ", fields=[Field("a")]), "Not hex"),
    (FrameSpec(header="AA"), "at least one field"),
    (FrameSpec(header="AA", fields=[Field("", "skip")]), "at least one field"),
    (FrameSpec(header="AA", fields=[Field("a"), Field("a")]), "same name"),
    (FrameSpec(header="AA", fields=[Field("has space")]), "no spaces"),
    (FrameSpec(header="AA", fields=[Field("a", "f128")]), "Unknown type"),
    (FrameSpec(header="AA", fields=[Field("a", "u8", 0.0)]), "other than 0"),
    (FrameSpec(tail="0A", length="u8", fields=[Field("a")]), "needs a header"),
    (FrameSpec(header="AA", fields=[Field("a")], checksum="md5"), "Unknown checksum"),
    (FrameSpec(any_floats=True), "header or a tail"),
    (FrameSpec(header="AA", any_floats=True), "needs a tail"),
    (FrameSpec(header="AA", tail="0A", any_floats=True), "can't have"),
])
def test_invalid_definitions(spec, problem):
    assert problem in spec.validate()


def test_presets_are_valid_and_survive_a_round_trip():
    for spec in PRESETS.values():
        assert spec.validate() is None
        assert FrameSpec.from_dict(spec.to_dict()) == spec


@pytest.mark.parametrize("data", [None, "x", [], {"fields": "nope"}, {"fields": [1, {"scale": "x"}]},
                                  {"length_extra": "many"}])
def test_rubbish_from_a_file_gives_a_definition_that_is_merely_invalid(data):
    assert FrameSpec.from_dict(data).validate() is not None


def test_frame_size():
    assert basic(tail="0A", checksum="crc16-ccitt").frame_size == 2 + 4 + 2 + 1
    assert FrameSpec(header="AA", length="u8", fields=[Field("a")]).frame_size is None


def test_trying_a_sample_counts_a_first_justfloat_frame():
    d = decode_sample(PRESETS["VOFA+ JustFloat"], justfloat(1.0, 2.0))
    assert d.frames == 1 and d.last == {"ch1": 1.0, "ch2": 2.0}
