"""
core/controls.py — user-defined controls that send commands to a device.

Four kinds (CMP10 / CMP11), shown in the Controls tool window:

  button  — sends ``command``; can be bound to a function key
  toggle  — sends ``command`` when switched on, ``command_off`` when switched off
  slider  — sends ``command`` with ``{value}`` filled in, e.g. ``SET PWM={value}``
  number  — the same, from a typed number

Commands use the same escapes as a trigger reply (``\\r \\n \\t \\0 \\xHH``) and get
the command bar's line ending unless ``line_ending`` is off.

Definitions are plain data: loading a file can't run anything, and a stored
slider or toggle position is only ever sent when the user moves it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from core.triggers import render_reply

KINDS = ("button", "toggle", "slider", "number")
#: Function keys a control may use; the rest belong to the app (F4 detach, F5 flash).
FREE_KEYS = ("F1", "F2", "F3", "F6", "F7", "F8", "F9", "F10", "F11", "F12")
#: A slider is an integer range in Qt, and more positions than this can't be aimed at.
MAX_SLIDER_STEPS = 1_000_000


def _decimals(step: float) -> int:
    try:
        exponent = Decimal(str(step)).normalize().as_tuple().exponent
    except InvalidOperation:
        return 0
    return max(0, -exponent)


@dataclass
class Control:
    kind: str
    label: str
    command: str = ""
    command_off: str = ""            # toggle only
    minimum: float = 0.0             # slider / number
    maximum: float = 100.0
    step: float = 1.0
    value: float = 0.0               # last position (toggle: non-zero = on)
    key: str = ""                    # button / toggle only
    line_ending: bool = True

    @property
    def decimals(self) -> int:
        return _decimals(self.step)

    def format_value(self, value: float) -> str:
        return f"{value:.{self.decimals}f}"

    def snap(self, value: float) -> float:
        """*value* clamped to the range, on a step from the minimum."""
        value = min(self.maximum, max(self.minimum, value))
        ticks = round((value - self.minimum) / self.step)
        return round(min(self.maximum, self.minimum + ticks * self.step), self.decimals)

    def validate(self) -> str | None:
        """Why this definition can't be used, or ``None`` if it can."""
        if self.kind not in KINDS:
            return f"Unknown control type: {self.kind}"
        if not self.label.strip():
            return "Enter a name."
        if not self.command.strip():
            return "Enter the command to send."
        # JSON files can hold NaN and Infinity, which no range check would catch.
        if not all(map(math.isfinite, (self.minimum, self.maximum, self.step, self.value))):
            return "Min, Max and Step must be numbers."
        if self.kind == "toggle" and not self.command_off.strip():
            return "Enter the command to send when switched off."
        if self.kind in ("slider", "number"):
            if "{value}" not in self.command:
                return "The command needs {value} where the number goes."
            if self.maximum <= self.minimum:
                return "Max must be greater than Min."
            if self.step <= 0:
                return "Step must be greater than 0."
            if self.kind == "slider" and (self.maximum - self.minimum) / self.step > MAX_SLIDER_STEPS:
                return "That is too many positions for a slider: use a larger Step."
        return None

    def render(self, value: float | None = None, on: bool = True) -> bytes:
        """The bytes to send, without the line ending."""
        template = self.command if on else self.command_off
        if value is not None:
            template = template.replace("{value}", self.format_value(value))
        return render_reply(template)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "label": self.label, "command": self.command,
                "command_off": self.command_off, "minimum": self.minimum,
                "maximum": self.maximum, "step": self.step, "value": self.value,
                "key": self.key, "line_ending": self.line_ending}

    @classmethod
    def from_dict(cls, d: dict) -> Control:
        return cls(kind=str(d.get("kind", "button")), label=str(d.get("label", "")),
                   command=str(d.get("command", "")), command_off=str(d.get("command_off", "")),
                   minimum=float(d.get("minimum", 0.0)), maximum=float(d.get("maximum", 100.0)),
                   step=float(d.get("step", 1.0)), value=float(d.get("value", 0.0)),
                   key=str(d.get("key", "")), line_ending=bool(d.get("line_ending", True)))


def load_controls(data: object) -> list[Control]:
    """Controls from parsed JSON. Hand-edited or foreign files are expected:
    anything malformed or invalid is skipped, and a key the app doesn't offer
    is dropped."""
    out: list[Control] = []
    if not isinstance(data, list):
        return out
    used: set[str] = set()
    for d in data:
        try:
            c = Control.from_dict(d)
        except (AttributeError, TypeError, ValueError):
            continue
        if c.validate() is not None:
            continue
        if c.key not in FREE_KEYS or c.key in used or c.kind not in ("button", "toggle"):
            c.key = ""
        used.add(c.key)
        if c.kind in ("slider", "number"):
            c.value = c.snap(c.value)
        out.append(c)
    return out
