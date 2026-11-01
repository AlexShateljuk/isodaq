"""
core/quick_plot.py — zero-configuration plotting (CMP5) + Teleplot input (CMP6).

Recognises lines that are *only* data and returns ``{name: value}`` without any
channel having been set up:

  12.5, 40, -3              → ch1=12.5 ch2=40 ch3=-3    (Arduino Serial Plotter)
  temp:21.5 hum:40%         → temp=21.5 hum=40          (Arduino label:value)
  temp=21.5, rpm=1500       → temp=21.5 rpm=1500
  >temp:21.5                → temp=21.5                 (Teleplot)
  >temp:1627551892437:21.5  → temp=21.5                 (Teleplot, own timestamp)
  >temp:21.5§°C             → temp=21.5                 (Teleplot unit)

Deliberately strict: the *whole* line must be data, so ordinary log lines
("Free heap: 12345 bytes", "I (123) wifi: connected") are never plotted.
Teleplot text (``|t``), no-plot (``|np``), XY (``|xy``) and 3D lines are ignored.
"""
from __future__ import annotations

import re

#: More values than this on one line is a table or dump, not telemetry.
MAX_VALUES = 16

_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_NUMBERS_ONLY = re.compile(rf"\s*{_NUM}(?:\s*[,;\t ]\s*{_NUM})*\s*[,;]?\s*")
_NUM_TOKEN = re.compile(_NUM)
_PAIR = re.compile(rf"([A-Za-z_][\w.\-]*)[:=]({_NUM})[A-Za-z%°/]*")
_PAIR_SEP = re.compile(r"\s*([:=])\s*")
# Cheap gate: the first token must already be a pair (rejects log lines fast).
_FIRST_PAIR = re.compile(rf"\s*[A-Za-z_][\w.\-]*\s*[:=]\s*{_NUM}[A-Za-z%°/]*(?:[,;\s|]|$)")
_SPLIT = re.compile(r"[,;\s|]+")
_TELEPLOT_POINT = re.compile(rf"(?:{_NUM}:)?({_NUM})")
_DATA_START = frozenset("+-.0123456789")


def quick_parse(line: str) -> dict[str, float] | None:
    """``{name: value}`` if *line* is a pure data line, else ``None``."""
    if not line:
        return None
    c = line[0]
    if c == ">":
        return teleplot_values(line)
    if c in _DATA_START or c in " \t":
        if _NUMBERS_ONLY.fullmatch(line):
            vals = _NUM_TOKEN.findall(line)
            if 0 < len(vals) <= MAX_VALUES:
                return {f"ch{i + 1}": float(v) for i, v in enumerate(vals)}
        return None
    if ":" not in line and "=" not in line or not _FIRST_PAIR.match(line):
        return None
    tokens = [t for t in _SPLIT.split(_PAIR_SEP.sub(r"\1", line.strip())) if t]
    if not 0 < len(tokens) <= MAX_VALUES:
        return None
    out: dict[str, float] = {}
    for tok in tokens:
        m = _PAIR.fullmatch(tok)
        if m is None:
            return None
        out[m.group(1)] = float(m.group(2))
    return out


def teleplot_values(line: str) -> dict[str, float] | None:
    """``>name:[timestamp:]value[;…][§unit][|flags]`` → ``{name: last value}``."""
    body, _, flags = line[1:].partition("|")
    if flags and flags.strip() not in ("", "clr"):
        return None                      # text / xy / np / 3D shapes — not a series
    name, sep, rest = body.partition(":")
    name = name.strip()
    if not sep or not name or name.startswith("3D"):
        return None
    rest = rest.split("§", 1)[0].strip()
    value = None
    for point in rest.split(";"):
        m = _TELEPLOT_POINT.fullmatch(point.strip())
        if m is None:
            return None
        value = float(m.group(1))
    return None if value is None else {name: value}


def keepable_key(name: str) -> bool:
    """Whether an auto channel can become a Parsing channel (``key=name``) —
    true for named values; positional ``chN`` columns have no key in the line."""
    return not re.fullmatch(r"ch\d+", name)
