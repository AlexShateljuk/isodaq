"""Checksums (CMP9) against published check values and real frames."""
import pytest

from core.checksum import ALGORITHMS, FORMATS, parse_hex, star_mismatch, tail_for
from core.triggers import Trigger, TriggerEngine, parse_trigger_line

NMEA = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"


@pytest.mark.parametrize("name, expected", [
    ("sum8", 0xDD), ("xor8", 0x31), ("lrc8", 0x23),
    ("crc8", 0xF4), ("crc8-maxim", 0xA1),
    ("crc16-modbus", 0x4B37), ("crc16-ccitt", 0x29B1), ("crc16-xmodem", 0x31C3),
    ("crc32", 0xCBF43926),
])
def test_check_values(name, expected):
    assert ALGORITHMS[name].compute(b"123456789") == expected


def test_modbus_frame_goes_low_byte_first():
    frame = bytes.fromhex("010300000002")
    assert ALGORITHMS["crc16-modbus"].low_first
    assert tail_for("crc16-modbus", "low_first", frame) == bytes.fromhex("C40B")


def test_byte_orders_and_text_forms():
    assert tail_for("crc16-ccitt", "high_first", b"123456789") == b"\x29\xB1"
    assert tail_for("crc16-ccitt", "low_first", b"123456789") == b"\xB1\x29"
    assert tail_for("crc16-ccitt", "hex", b"123456789") == b"29B1"
    assert tail_for("crc16-ccitt", "star", b"123456789") == b"*29B1"
    assert tail_for("crc32", "high_first", b"123456789") == bytes.fromhex("CBF43926")


def test_text_forms_keep_leading_zeros():
    assert tail_for("xor8", "hex", b"\x05") == b"05"
    assert tail_for("xor8", "star", b"\x05") == b"*05"


def test_star_form_matches_a_real_nmea_sentence():
    body = NMEA.rsplit("*", 1)[0].encode()
    assert tail_for("xor8", "star", body) == b"*47"


@pytest.mark.parametrize("name", ALGORITHMS)
@pytest.mark.parametrize("fmt", FORMATS)
def test_every_combination_gives_the_right_size(name, fmt):
    tail = tail_for(name, fmt, b"abc")
    size = ALGORITHMS[name].size
    assert len(tail) == {"hex": size * 2, "star": size * 2 + 1}.get(fmt, size)


def test_star_mismatch_only_on_a_wrong_checksum():
    assert not star_mismatch("xor8", NMEA)
    assert not star_mismatch("xor8", NMEA + "\r")              # line-ending residue
    assert star_mismatch("xor8", NMEA.replace("545.4", "545.5"))
    assert star_mismatch("xor8", NMEA[:-2] + "48")


@pytest.mark.parametrize("line", [
    "I (123) wifi: connected", "", "*", "no checksum here*", "a*XY", "a*123", "2*3 = 6",
    "bad utf8 �*00",
])
def test_lines_without_a_checksum_are_not_mismatches(line):
    assert not star_mismatch("xor8", line)


def test_star_mismatch_accepts_lowercase_hex():
    assert not star_mismatch("xor8", "$AB*03")
    assert not star_mismatch("xor8", "$A\x7f*3e")
    assert not star_mismatch("crc16-ccitt", "123456789*29b1")


@pytest.mark.parametrize("text", ["01 03 0a", "0x01 0x03 0x0A", "01,03,0A", "01030A", "01;03;0A"])
def test_parse_hex_forms(text):
    assert parse_hex(text) == b"\x01\x03\x0A"


@pytest.mark.parametrize("text", ["", "0x", "1", "GG", "01 0", "hello"])
def test_parse_hex_refuses_what_isnt_bytes(text):
    with pytest.raises(ValueError):
        parse_hex(text)


# ── As a trigger ──────────────────────────────────────────────────────────────

def test_checksum_trigger_fires_on_the_bad_line_only():
    engine = TriggerEngine()
    assert engine.add_trigger(Trigger("bad nmea", "xor8", type="checksum")) is None
    hits = []
    engine.on_match(lambda t, line, ts: hits.append(line))
    engine.check(NMEA, "t")
    engine.check("ordinary log line", "t")
    bad = NMEA.replace("545.4", "545.5")
    engine.check(bad, "t")
    assert hits == [bad]


def test_checksum_trigger_refuses_an_unknown_algorithm():
    assert "Unknown checksum" in Trigger("x", "md5", type="checksum").compile()


def test_checksum_trigger_prefix_and_file_round_trip():
    assert parse_trigger_line("[checksum] crc16-ccitt") == ("checksum", "crc16-ccitt")
    engine = TriggerEngine()
    engine.add_trigger(Trigger("x", "xor8", type="checksum"))
    again = TriggerEngine()
    again.from_dict_list(engine.to_dict_list())
    assert again.get_triggers()[0].type == "checksum"
    assert again.get_triggers()[0].matches(NMEA[:-2] + "48")
