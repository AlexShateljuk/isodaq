"""
core/data_parser.py — Line-by-line numeric extractor for serial data.

Each ChannelConfig defines:
  key    — token to search for (dot-notation ok: snap.pv_v)
  unit   — optional unit suffix to pick among multiple values after the same key
           e.g. key="pv" unit="mV"  →  "pv=43608mV 847mA"  →  43608
                key="pv" unit="mA"  →  same line             →  847
  prefix — optional substring the line must contain before the key is tried
           e.g. prefix="a1617:" filters out unrelated log sources
  scale  — multiply extracted number  (e.g. 0.001 converts mV → V)
  offset — add after scaling

Supported value formats (auto-detected):
  key: value          →  snap.pv_v: 25691
  key: 0xHEX          →  vbat_v: 0x5B64
  key=valueUNIT ...   →  pv=43608mV 847mA  (unit selects which token)
  key=value           →  rem_cap=500
  JSON object in line →  [ts]{"key": value, ...}
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

# Matches: optional minus + (hex OR decimal/float) + optional unit letters
_VALUE_WITH_UNIT = re.compile(
    r'^(-?(?:0[xX][0-9a-fA-F]+|\d+(?:\.\d+)?))[a-zA-Z%°]*$'
)
_VALUE_IN_LINE = r'-?(?:0[xX][0-9a-fA-F]+|\d+(?:\.\d+)?)[a-zA-Z%°]*'

# Segment boundary: pipe separator OR next "word=" / "word:" pattern
_SEG_END = re.compile(r'\s*\||\s+[\w.][\w.]*\s*[=:]')


def _to_float(s: str) -> float | None:
    """Parse a raw value token (decimal, hex, with/without unit suffix)."""
    m = _VALUE_WITH_UNIT.match(s.strip())
    if not m:
        return None
    raw = m.group(1)
    if raw.lower().startswith('0x'):
        return float(int(raw, 16))
    return float(raw)


@dataclass
class ChannelConfig:
    name: str
    key: str
    unit: str = ""
    prefix: str = ""
    scale: float = 1.0
    offset: float = 0.0
    enabled: bool = True
    show_chart: bool = False
    show_indicator: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name, "key": self.key,
            "unit": self.unit, "prefix": self.prefix,
            "scale": self.scale, "offset": self.offset,
            "enabled": self.enabled,
            "show_chart": self.show_chart,
            "show_indicator": self.show_indicator,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ChannelConfig":
        return cls(
            name=str(d.get("name", "")),
            key=str(d.get("key", "")),
            unit=str(d.get("unit", "")),
            prefix=str(d.get("prefix", "")),
            scale=float(d.get("scale", 1.0)),
            offset=float(d.get("offset", 0.0)),
            enabled=bool(d.get("enabled", True)),
            show_chart=bool(d.get("show_chart", False)),
            show_indicator=bool(d.get("show_indicator", False)),
        )


class DataParser:
    def __init__(self):
        self._channels: list[ChannelConfig] = []

    # ── Channel management ────────────────────────────────────────────────────

    def get_channels(self) -> list[ChannelConfig]:
        return list(self._channels)

    def add_channel(self, ch: ChannelConfig) -> None:
        self._channels.append(ch)

    def update_channel(self, idx: int, ch: ChannelConfig) -> None:
        self._channels[idx] = ch

    def remove_channel(self, idx: int) -> None:
        del self._channels[idx]

    # ── Parsing ───────────────────────────────────────────────────────────────

    def parse(self, line: str) -> dict[str, float]:
        """Extract all enabled channels from one serial line.

        Returns {channel_name: scaled_value}. Empty dict if nothing matched.
        """
        if not self._channels:
            return {}
        json_obj = _try_json(line)
        result: dict[str, float] = {}
        for ch in self._channels:
            if not ch.enabled:
                continue
            if ch.prefix and ch.prefix not in line:
                continue
            raw = None
            if json_obj is not None:
                raw = _from_json(json_obj, ch.key)
            if raw is None:
                raw = _from_kv(line, ch.key, ch.unit)
            if raw is not None:
                result[ch.name] = raw * ch.scale + ch.offset
        return result

    # ── Persistence ───────────────────────────────────────────────────────────

    def to_dict_list(self) -> list[dict]:
        return [ch.to_dict() for ch in self._channels]

    def from_dict_list(self, data: list[dict]) -> None:
        self._channels = [ChannelConfig.from_dict(d) for d in data]


# ── Extraction helpers ────────────────────────────────────────────────────────

def _try_json(line: str) -> dict | None:
    start = line.find('{')
    if start == -1:
        return None
    try:
        return json.loads(line[start:])
    except Exception:
        return None


def _from_json(obj: dict, key: str) -> float | None:
    """Navigate dot-notation key inside a parsed JSON dict."""
    val: object = obj
    for part in key.split('.'):
        if not isinstance(val, dict) or part not in val:
            return None
        val = val[part]
    try:
        return float(val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _from_kv(line: str, key: str, unit: str = "") -> float | None:
    """Extract a numeric value for *key* from an arbitrary line.

    Steps:
      1. Find  key=  or  key:  (with word-boundary lookbehind)
      2. Clip the segment to the next pipe or next key= boundary
      3a. If unit given: find the token ending with that unit in the segment
      3b. If no unit:    take the first numeric token in the segment
    """
    ek = re.escape(key)
    m = re.search(rf'(?<![.\w]){ek}\s*[=:]\s*', line)
    if not m:
        return None

    # Clip segment at first field boundary after the match
    tail = line[m.end():]
    end_m = _SEG_END.search(tail)
    seg = tail[:end_m.start()] if end_m else tail

    if unit:
        eu = re.escape(unit)
        tok = re.search(rf'(-?\d+(?:\.\d+)?){eu}(?![a-zA-Z%°])', seg)
        return float(tok.group(1)) if tok else None

    tok = re.search(rf'({_VALUE_IN_LINE})', seg)
    return _to_float(tok.group(1)) if tok else None


def parse_single(line: str, key: str,
                 scale: float = 1.0, offset: float = 0.0,
                 unit: str = "", prefix: str = "") -> float | None:
    """Convenience wrapper used by the Test button in the parse panel."""
    if prefix and prefix not in line:
        return None
    json_obj = _try_json(line)
    raw = None
    if json_obj is not None:
        raw = _from_json(json_obj, key)
    if raw is None:
        raw = _from_kv(line, key, unit)
    if raw is not None:
        return raw * scale + offset
    return None
