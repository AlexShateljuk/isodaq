"""Unit tests for core.data_parser — value extraction across the supported formats."""
from pytest import approx

from core.data_parser import (
    ChannelConfig,
    DataParser,
    _to_float,
    parse_single,
)


# ── _to_float ────────────────────────────────────────────────────────────────────

def test_to_float_decimal():
    assert _to_float("42") == 42.0
    assert _to_float("-3.5") == -3.5


def test_to_float_hex():
    assert _to_float("0x5B64") == float(0x5B64)


def test_to_float_with_unit_suffix():
    assert _to_float("847mA") == 847.0


def test_to_float_rejects_garbage():
    assert _to_float("abc") is None
    assert _to_float("") is None


# ── parse_single (the Test button path) ──────────────────────────────────────────

def test_parse_single_kv_colon():
    assert parse_single("snap.pv_v: 25691", "snap.pv_v") == 25691.0


def test_parse_single_equals():
    assert parse_single("rem_cap=500", "rem_cap") == 500.0


def test_parse_single_hex():
    assert parse_single("vbat_v: 0x5B64", "vbat_v") == float(0x5B64)


def test_parse_single_unit_selects_token():
    line = "pv=43608mV 847mA"
    assert parse_single(line, "pv", unit="mV") == 43608.0
    assert parse_single(line, "pv", unit="mA") == 847.0


def test_parse_single_scale_and_offset():
    # 43608 mV → V
    assert parse_single("pv=43608mV", "pv", unit="mV", scale=0.001) == approx(43.608)
    assert parse_single("x=10", "x", scale=2.0, offset=5.0) == 25.0


def test_parse_single_prefix_gate():
    assert parse_single("a1617: pv=5", "pv", prefix="a1617:") == 5.0
    assert parse_single("other: pv=5", "pv", prefix="a1617:") is None


def test_parse_single_no_match_returns_none():
    assert parse_single("nothing here", "pv") is None


def test_key_word_boundary_not_substring():
    # "pv" must not match inside "spvz"
    assert parse_single("spvz=9 pv=7", "pv") == 7.0


# ── DataParser.parse (multi-channel) ─────────────────────────────────────────────

def test_parse_multiple_channels():
    p = DataParser()
    p.add_channel(ChannelConfig(name="Voltage", key="pv", unit="mV", scale=0.001))
    p.add_channel(ChannelConfig(name="Current", key="pv", unit="mA"))
    out = p.parse("pv=43608mV 847mA")
    assert out == approx({"Voltage": 43.608, "Current": 847.0})


def test_parse_skips_disabled_channel():
    p = DataParser()
    p.add_channel(ChannelConfig(name="A", key="x", enabled=False))
    assert p.parse("x=1") == {}


def test_parse_json_object_dot_notation():
    p = DataParser()
    p.add_channel(ChannelConfig(name="Temp", key="sensor.temp"))
    out = p.parse('[12:00]{"sensor": {"temp": 25.5}}')
    assert out == {"Temp": 25.5}


def test_parse_empty_when_no_channels():
    assert DataParser().parse("pv=1") == {}


# ── snippet (exec path) ──────────────────────────────────────────────────────────

def test_snippet_returns_dict_merged():
    p = DataParser()
    assert p.set_snippet("return {'custom': len(line)}") is None
    out = p.parse("hello")
    assert out == {"custom": 5.0}


def test_snippet_values_win_on_name_collision():
    p = DataParser()
    p.add_channel(ChannelConfig(name="v", key="v"))
    p.set_snippet("return {'v': 999}")
    assert p.parse("v=1")["v"] == 999.0


def test_snippet_syntax_error_reported():
    p = DataParser()
    err = p.set_snippet("return (")
    assert err is not None and "Syntax error" in err


def test_snippet_runtime_error_swallowed():
    p = DataParser()
    p.set_snippet("return {'x': 1/0}")   # ZeroDivisionError at call time
    assert p.parse("anything") == {}     # must not raise


def test_snippet_non_numeric_values_dropped():
    p = DataParser()
    p.set_snippet("return {'a': 'not a number', 'b': 3}")
    assert p.parse("x") == {"b": 3.0}


# ── ChannelConfig round-trip ─────────────────────────────────────────────────────

def test_channel_config_roundtrip():
    ch = ChannelConfig(name="V", key="pv", unit="mV", prefix="a:", scale=0.001,
                       offset=1.0, enabled=True, show_chart=True, show_indicator=True)
    ch2 = ChannelConfig.from_dict(ch.to_dict())
    assert ch2 == ch
