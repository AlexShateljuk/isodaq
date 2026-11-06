"""ui/frame_button.py — how the command bar builds a frame (CMP9).

A button by the Send button with a menu: type text or hex bytes, and which
checksum to append. It stays lit while anything but plain text is selected, since
it changes the bytes that go out.
"""
from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup
from PyQt6.QtWidgets import QMenu, QPushButton

from core.checksum import ALGORITHMS, FORMATS, parse_hex, tail_for
from core.i18n import tr


class FrameButton(QPushButton):
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("frameBtn")
        self.setCheckable(True)
        self.setToolTip(tr("How commands are sent: text or hex bytes, with or without a "
                           "checksum.\nThe checksum also goes on repeated commands, controls "
                           "and trigger replies, but not on macros or file sends."))
        self.hex_input = False
        self.algorithm = ""
        self.format = "high_first"
        self._input_actions: dict[bool, QAction] = {}
        self._algo_actions: dict[str, QAction] = {}
        self._format_actions: dict[str, QAction] = {}
        self._build_menu()
        self._refresh()

    def nextCheckState(self) -> None:   # noqa: N802  (Qt naming)
        """A click opens the menu; the lit state only follows the settings."""

    def _build_menu(self) -> None:
        menu = QMenu(self)
        names = {"high_first": tr("Bytes, high byte first"),
                 "low_first": tr("Bytes, low byte first"),
                 "hex": tr("Hex text"),
                 "star": tr("* and hex text (NMEA)")}

        def section(title: str) -> QActionGroup:
            label = menu.addAction(title)
            label.setEnabled(False)
            return QActionGroup(self)

        def choice(group: QActionGroup, text: str, on_pick) -> QAction:
            action = QAction(text, self, checkable=True)
            action.triggered.connect(lambda _=False: on_pick())
            group.addAction(action)
            menu.addAction(action)
            return action

        group = section(tr("Type commands as"))
        self._input_actions[False] = choice(group, tr("Text"), lambda: self._set_hex(False))
        self._input_actions[True] = choice(group, tr("Hex bytes"), lambda: self._set_hex(True))
        menu.addSeparator()
        group = section(tr("Append checksum"))
        self._algo_actions[""] = choice(group, tr("None"), lambda: self._pick_algorithm(""))
        for key, algo in ALGORITHMS.items():
            self._algo_actions[key] = choice(group, algo.label,
                                             lambda k=key: self._pick_algorithm(k))
        menu.addSeparator()
        group = section(tr("Checksum as"))
        for key in FORMATS:
            self._format_actions[key] = choice(group, names[key],
                                               lambda k=key: self._pick_format(k))
        self.setMenu(menu)

    def _set_hex(self, on: bool) -> None:
        self.hex_input = on
        self._refresh()
        self.changed.emit()

    def _pick_algorithm(self, key: str) -> None:
        self.algorithm = key
        # Each checksum has a usual byte order: Modbus low first, the rest high first.
        if key and self.format in ("high_first", "low_first"):
            self.format = "low_first" if ALGORITHMS[key].low_first else "high_first"
        self._refresh()
        self.changed.emit()

    def _pick_format(self, key: str) -> None:
        self.format = key
        self._refresh()
        self.changed.emit()

    def _refresh(self) -> None:
        self._input_actions[self.hex_input].setChecked(True)
        self._algo_actions[self.algorithm].setChecked(True)
        self._format_actions[self.format].setChecked(True)
        for action in self._format_actions.values():
            action.setEnabled(bool(self.algorithm))
        parts = ([tr("Hex")] if self.hex_input else []) + (
            [ALGORITHMS[self.algorithm].label] if self.algorithm else [])
        self.setText(" + ".join(parts) or tr("Text"))
        self.setChecked(bool(parts))

    # ── Building frames ───────────────────────────────────────────────────────

    def command_bytes(self, text: str) -> bytes:
        """What *text* in the command field means. Raises ``ValueError`` for bad hex."""
        return parse_hex(text) if self.hex_input else text.encode()

    def tail(self, data: bytes) -> bytes:
        """The checksum for *data*, as it goes on the wire; empty if none is chosen."""
        return tail_for(self.algorithm, self.format, data) if self.algorithm else b""

    def describe(self, tail: bytes) -> str:
        """*tail* for the terminal: as text if it is text, otherwise byte by byte."""
        return tail.decode() if self.format in ("hex", "star") else tail.hex(" ").upper()

    # ── Settings ──────────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {"hex": self.hex_input, "algorithm": self.algorithm, "format": self.format}

    def from_dict(self, data: object) -> None:
        if not isinstance(data, dict):
            return
        self.hex_input = bool(data.get("hex", False))
        self.algorithm = data.get("algorithm") if data.get("algorithm") in ALGORITHMS else ""
        self.format = data.get("format") if data.get("format") in FORMATS else "high_first"
        self._refresh()
        self.changed.emit()
