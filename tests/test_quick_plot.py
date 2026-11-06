"""Quick Plot line recognition (CMP5) and Teleplot input (CMP6)."""
import pytest

from core.data_parser import ChannelConfig, DataParser
from core.quick_plot import keepable_key, quick_parse


@pytest.mark.parametrize("line, expected", [
    ("12.5, 40, -3", {"ch1": 12.5, "ch2": 40.0, "ch3": -3.0}),
    ("12.5 40\t-3", {"ch1": 12.5, "ch2": 40.0, "ch3": -3.0}),
    ("42", {"ch1": 42.0}),
    ("1e3,2;", {"ch1": 1000.0, "ch2": 2.0}),
    ("temp:21.5 hum:40%", {"temp": 21.5, "hum": 40.0}),
    ("temp: 21.5, hum: 40", {"temp": 21.5, "hum": 40.0}),
    ("temp=21.5, rpm=1500", {"temp": 21.5, "rpm": 1500.0}),
    ("v=3.3V i=120mA", {"v": 3.3, "i": 120.0}),
    (">temp:21.5", {"temp": 21.5}),
    (">temp:1627551892437:21.5", {"temp": 21.5}),
    (">temp:21.5§°C", {"temp": 21.5}),
    (">temp:1:2;3:4", {"temp": 4.0}),
    ("sensors/temp: 21.5", {"sensors/temp": 21.5}),
])
def test_data_lines(line, expected):
    assert quick_parse(line) == expected


@pytest.mark.parametrize("line", [
    "", " ", "Free heap: 12345 bytes", "I (123) wifi: connected", "12:30:45",
    "2024-01-01", "a,b", "temp=1 junk", "T=12:30", "P0 SEQ=0000001 temp=40.00",
    ">state:Running|t", ">traj:1:1;2:2|xy", ">x:1|np", ">3D|cube:S:cube", ">:log",
    ",".join(["1"] * 17),                          # more than MAX_VALUES
])
def test_non_data_lines(line):
    assert quick_parse(line) is None


def test_keepable_key():
    assert keepable_key("temp")
    assert not keepable_key("ch3")


def test_parser_reads_teleplot_value_not_timestamp():
    p = DataParser()
    p.add_channel(ChannelConfig(name="rpm", key="rpm"))
    assert p.parse(">rpm:1627551892437:1500§rpm") == {"rpm": 1500.0}
    assert p.parse(">rpm:1200") == {"rpm": 1200.0}
    assert p.parse("rpm=900") == {"rpm": 900.0}
    assert p.channel_names() == {"rpm"}
