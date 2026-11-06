"""
core/binary_frames.py — decode binary frames from a byte stream into channels (CMP15).

No Qt. A frame is laid out as::

    header | length | fields… | checksum | tail

Every part but the fields is optional, though a frame needs a header or a tail to
be found at all. Each decoded frame becomes an ordinary ``name:value`` line, as
Modbus values do, so plotting, triggers, logging and publishing work on it.

Two formats are presets, checked against their own documentation and source:

  VOFA+ JustFloat  any number of little-endian f32, then ``00 00 80 7F``
  SerialPlot       sync word, 1- or 2-byte payload size, samples, Sum-8 of the payload

The stream can start mid-frame, and text such as a boot log can come between
frames, so anything that isn't a frame is passed on as bytes for the caller to show
as text. A frame that fails a check isn't lost track of: decoding carries on one
byte later, so a false header inside the data costs nothing.
"""
from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass, field

from core.checksum import ALGORITHMS, parse_hex

#: type → (struct code, size). ``skip`` is one byte to ignore.
TYPES: dict[str, tuple[str, int]] = {
    "u8": ("B", 1), "i8": ("b", 1), "u16": ("H", 2), "i16": ("h", 2),
    "u32": ("I", 4), "i32": ("i", 4), "u64": ("Q", 8), "i64": ("q", 8),
    "f32": ("f", 4), "f64": ("d", 8), "skip": ("x", 1),
}
LENGTHS = {"": 0, "u8": 1, "u16": 2}
COVERAGE = ("frame", "after_header", "payload")
#: A longer payload is taken to be a false match rather than waited for.
MAX_PAYLOAD = 4096
MAX_ANY_FLOATS = 64
#: Pairs per terminal line. Quick Plot treats a much longer line as a table, not data.
VALUES_PER_LINE = 8

_NAME = re.compile(r"[A-Za-z_][\w.\-/]*")
_TEXT_BYTES = frozenset(range(0x20, 0x7F)) | {0x09, 0x0A, 0x0D, 0x1B}


def _hex(text: str) -> bytes:
    return parse_hex(text) if text.strip() else b""


@dataclass
class Field:
    name: str
    type: str = "f32"
    scale: float = 1.0

    def validate(self) -> str | None:
        if self.type not in TYPES:
            return f"Unknown type: {self.type}"
        if self.type != "skip" and not _NAME.fullmatch(self.name):
            return "A name starts with a letter, and has no spaces."
        if not math.isfinite(self.scale) or self.scale == 0:
            return "Scale must be a number other than 0."
        return None

    def to_dict(self) -> dict:
        return {"name": self.name, "type": self.type, "scale": self.scale}

    @classmethod
    def from_dict(cls, d: dict) -> Field:
        return cls(str(d.get("name", "")), str(d.get("type", "f32")), float(d.get("scale", 1.0)))


@dataclass
class FrameSpec:
    header: str = ""
    tail: str = ""
    fields: list[Field] = field(default_factory=list)
    big_endian: bool = False
    any_floats: bool = False         # any number of f32, named ch1, ch2, … (JustFloat)
    length: str = ""                 # "", "u8" or "u16"; counts the payload's bytes …
    length_extra: int = 0            # … plus this many, for devices that count more
    checksum: str = ""
    checksum_over: str = "frame"
    checksum_low_first: bool = False

    def validate(self) -> str | None:
        try:
            header, tail = _hex(self.header), _hex(self.tail)
        except ValueError as e:
            return str(e)
        if not header and not tail:
            return "A frame needs a header or a tail, to find where it starts."
        if self.checksum and self.checksum not in ALGORITHMS:
            return f"Unknown checksum: {self.checksum}"
        if self.checksum_over not in COVERAGE:
            return f"Unknown checksum range: {self.checksum_over}"
        if self.length not in LENGTHS:
            return f"Unknown length type: {self.length}"
        if self.any_floats:
            if not tail:
                return "Any number of floats needs a tail, to find where they end."
            if self.length or header or self.checksum:
                return "Any number of floats can't have a header, a length or a checksum."
            return None
        if self.length and not header:
            return "A frame with a length needs a header."
        named = [f for f in self.fields if f.type != "skip"]
        if not named:
            return "Add at least one field."
        for f in self.fields:
            problem = f.validate()
            if problem:
                return f"{f.name or f.type}: {problem}"
        if len({f.name for f in named}) != len(named):
            return "Two fields have the same name."
        return None

    @property
    def payload_size(self) -> int:
        return sum(TYPES[f.type][1] for f in self.fields)

    @property
    def frame_size(self) -> int | None:
        """Bytes per frame, if that is fixed."""
        if self.any_floats or self.length:
            return None
        checksum = ALGORITHMS[self.checksum].size if self.checksum else 0
        return len(_hex(self.header)) + self.payload_size + checksum + len(_hex(self.tail))

    def to_dict(self) -> dict:
        return {"header": self.header, "tail": self.tail,
                "fields": [f.to_dict() for f in self.fields],
                "big_endian": self.big_endian, "any_floats": self.any_floats,
                "length": self.length, "length_extra": self.length_extra,
                "checksum": self.checksum, "checksum_over": self.checksum_over,
                "checksum_low_first": self.checksum_low_first}

    @classmethod
    def from_dict(cls, d: object) -> FrameSpec:
        """A spec from a settings or imported file, which may hold anything."""
        if not isinstance(d, dict):
            return cls()
        fields = []
        for item in d.get("fields") or []:
            try:
                fields.append(Field.from_dict(item))
            except (AttributeError, TypeError, ValueError):
                continue
        try:
            extra = int(d.get("length_extra", 0))
        except (TypeError, ValueError):
            extra = 0
        return cls(str(d.get("header", "")), str(d.get("tail", "")), fields,
                   bool(d.get("big_endian", False)), bool(d.get("any_floats", False)),
                   str(d.get("length", "")), extra, str(d.get("checksum", "")),
                   str(d.get("checksum_over", "frame")), bool(d.get("checksum_low_first", False)))


PRESETS: dict[str, FrameSpec] = {
    "VOFA+ JustFloat": FrameSpec(tail="00 00 80 7F", any_floats=True),
    "SerialPlot": FrameSpec(header="AA BB", length="u8", checksum="sum8",
                            checksum_over="payload",
                            fields=[Field("ch1", "u16"), Field("ch2", "u16")]),
}


def looks_like_text(data: bytes) -> bool:
    """Whether bytes between frames are worth showing. Binary that failed a check
    isn't, and would only fill the terminal with noise."""
    if not data:
        return False
    if sum(b in _TEXT_BYTES for b in data) >= len(data) * 0.8:
        return True
    try:
        decoded = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return not any(ord(c) < 0x20 and ord(c) not in _TEXT_BYTES for c in decoded)


class FrameDecoder:
    """Stream decoder for one :class:`FrameSpec`, which must be valid. Used by one
    thread; the GUI only reads the counters and :attr:`last`."""

    def __init__(self, spec: FrameSpec, show_other: bool = True):
        self.spec = spec
        self.show_other = show_other
        self.frames = 0
        self.bad_checksum = 0
        self.bad_length = 0
        self.other_bytes = 0
        self.non_finite = 0
        self.last: dict[str, float] = {}
        self._buf = bytearray()
        self._other = bytearray()
        self._out: list = []
        self._synced = False             # any_floats: a frame start is known
        self._expected = 0               # any_floats: payload size last time
        self._order = ">" if spec.big_endian else "<"
        self._byteorder = "big" if spec.big_endian else "little"
        self._header = _hex(spec.header)
        self._tail = _hex(spec.tail)
        self._length_size = LENGTHS.get(spec.length, 0)
        algo = ALGORITHMS.get(spec.checksum)
        self._checksum = algo.compute if algo else None
        self._checksum_size = algo.size if algo else 0
        self._checksum_order = "little" if spec.checksum_low_first else "big"
        self._payload = spec.payload_size
        self._struct = struct.Struct(self._order + "".join(TYPES[f.type][0] for f in spec.fields))
        named = [f for f in spec.fields if f.type != "skip"]
        self._names = [f.name for f in named]
        self._scales = [f.scale for f in named] if any(f.scale != 1 for f in named) else None
        self._templates = [self._template(f) for f in named]
        self._any_layouts: dict[int, tuple] = {}

    @staticmethod
    def _template(f: Field) -> str:
        if f.scale != 1:
            spec = ".10g"
        elif f.type == "f32":
            spec = ".7g"
        elif f.type == "f64":
            spec = ".16g"
        else:
            spec = "d"
        return f"{f.name}:{{:{spec}}}"

    # ── Stream ────────────────────────────────────────────────────────────────

    def feed(self, chunk: bytes) -> list:
        """Everything *chunk* completes, in order: ``(lines, raw_frame)`` for a frame
        and ``bytes`` for anything else worth showing."""
        self._buf += chunk
        if self.spec.any_floats:
            used = self._scan_any_floats()
        else:
            used = self._scan_fixed()
        del self._buf[:used]
        self._flush_other()
        out, self._out = self._out, []
        return out

    def _skip(self, start: int, end: int) -> None:
        if end > start:
            self.other_bytes += end - start
            if self.show_other:
                self._other += self._buf[start:end]

    def _flush_other(self) -> None:
        if self._other:
            if looks_like_text(bytes(self._other)):
                self._out.append(bytes(self._other))
            self._other.clear()

    def _emit(self, names, values, templates, raw: bytes) -> None:
        self._flush_other()
        items = []
        for name, value, template in zip(names, values, templates):
            if value != value or value in (math.inf, -math.inf):
                self.non_finite += 1       # no way to plot it, and it would spoil the line
                continue
            self.last[name] = value
            items.append(template.format(value))
        self.frames += 1
        lines = [" ".join(items[i:i + VALUES_PER_LINE])
                 for i in range(0, len(items), VALUES_PER_LINE)]
        self._out.append((lines, raw))

    # ── Frames of known fields ────────────────────────────────────────────────

    def _scan_fixed(self) -> int:
        buf, header, tail = self._buf, self._header, self._tail
        pos, size = 0, len(buf)
        # Tail only: the frame is whatever fixed size comes before a tail.
        before_tail = self._payload + self._checksum_size
        while True:
            if header:
                start = buf.find(header, pos)
                if start < 0:
                    keep = max(pos, size - len(header) + 1)
                    self._skip(pos, keep)
                    return keep
            else:
                at = buf.find(tail, pos + before_tail)
                if at < 0:
                    keep = max(pos, size - before_tail - len(tail) + 1)
                    self._skip(pos, keep)
                    return keep
                start = at - before_tail
            self._skip(pos, start)
            pos = start
            used = self._try_frame(start, size)
            if used is None:             # needs more bytes
                return pos
            if used:
                pos += used
            else:                        # not a frame after all
                self._skip(pos, pos + 1)
                pos += 1

    def _try_frame(self, start: int, size: int) -> int | None:
        """Decode the frame at *start*: its size, ``0`` if it isn't one, ``None`` if
        more bytes are needed to tell."""
        buf = self._buf
        at = start + len(self._header)
        payload = self._payload
        if self._length_size:
            if at + self._length_size > size:
                return None
            payload = int.from_bytes(buf[at:at + self._length_size], self._byteorder) \
                - self.spec.length_extra
            at += self._length_size
            # A payload can hold several records (SerialPlot), but only whole ones.
            if payload <= 0 or payload > MAX_PAYLOAD or payload % self._payload:
                self.bad_length += 1
                return 0
        end = at + payload
        total = end + self._checksum_size + len(self._tail) - start
        if start + total > size:
            return None
        if self._tail and not buf.startswith(self._tail, end + self._checksum_size):
            self.bad_length += 1
            return 0
        if self._checksum is not None:
            over = {"frame": start, "after_header": start + len(self._header),
                    "payload": at}[self.spec.checksum_over]
            given = int.from_bytes(buf[end:end + self._checksum_size], self._checksum_order)
            if self._checksum(bytes(buf[over:end])) != given:
                self.bad_checksum += 1
                return 0
        raw = bytes(buf[start:start + total])
        offset = at - start
        shown = raw                      # the bytes go with the first record
        for _record in range(payload // self._payload):
            values = self._struct.unpack_from(raw, offset)
            if self._scales is not None:
                values = [v if s == 1 else v * s for v, s in zip(values, self._scales)]
            self._emit(self._names, values, self._templates, shown)
            shown = b""
            offset += self._payload
        return total

    # ── Any number of floats ──────────────────────────────────────────────────

    def _scan_any_floats(self) -> int:
        buf, tail = self._buf, self._tail
        pos, size = 0, len(buf)
        limit = MAX_ANY_FLOATS * 4
        while True:
            if not self._synced:
                # What comes before the first tail may be the end of a frame, so it
                # can't be trusted. Only its end says where frames start.
                at = buf.find(tail, pos)
                if at < 0:
                    keep = max(pos, size - len(tail) + 1)
                    self._skip(pos, keep)
                    return keep
                self._skip(pos, at + len(tail))
                pos = at + len(tail)
                self._synced = True
                continue
            at = self._next_tail(pos, size, limit)
            if at is None:               # needs more bytes
                return pos
            if at < 0:                   # lost: look for the next tail
                self._synced = False
                continue
            count = (at - pos) // 4
            end = at + len(tail)
            if count:
                layout = self._any_layouts.get(count)
                if layout is None:
                    layout = self._any_layouts[count] = (
                        struct.Struct(f"{self._order}{count}f"),
                        [f"ch{i + 1}" for i in range(count)],
                        [f"ch{i + 1}:{{:.7g}}" for i in range(count)])
                raw = bytes(buf[pos:end])
                self._emit(layout[1], layout[0].unpack_from(raw, 0), layout[2], raw)
                self._expected = at - pos
            pos = end

    def _next_tail(self, pos: int, size: int, limit: int) -> int | None:
        """The tail after the floats at *pos*: its position, ``-1`` if there isn't one
        within the limit, ``None`` if more bytes are needed. Only whole floats are
        looked at, because a tail's bytes can occur across two floats."""
        tail = self._tail
        expected = pos + self._expected
        buf = self._buf
        if self._expected and buf.startswith(tail, expected):
            return expected
        for at in range(pos, min(size - len(tail) + 1, pos + limit + 1), 4):
            if buf.startswith(tail, at):
                return at
        return -1 if size - pos >= limit + len(tail) else None


def decode_sample(spec: FrameSpec, data: bytes) -> FrameDecoder:
    """Decode one pasted frame, for trying a definition out without a device: the
    first frame counts here even if the format normally needs a tail to sync on."""
    decoder = FrameDecoder(spec, show_other=False)
    decoder._synced = True
    decoder.feed(data)
    return decoder
