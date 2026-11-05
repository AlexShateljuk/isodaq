"""ui/control_panel.py — the Controls tool window: buttons, toggles, sliders and
number boxes that send commands to the active connection (CMP10 / CMP11)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QLineEdit, QMenu, QMessageBox, QPushButton, QScrollArea, QSlider,
                             QVBoxLayout, QWidget)

from core.controls import FREE_KEYS, Control, load_controls
from core.i18n import tr
from core.stream_reader import _CONTROL_TO_DOT
from ui.number_box import NumberBox
from ui.themes import tint_titlebar

#: A dragged slider sends at most this often; the final position always goes out.
SLIDER_THROTTLE_MS = 100
BUTTON_MIN_WIDTH = 120


class ControlEditor(QDialog):
    """Add or edit one control."""

    KIND_NAMES = (("button", "Button"), ("toggle", "Toggle"),
                  ("slider", "Slider"), ("number", "Number"))

    def __init__(self, control: Control | None, used_keys: set[str], parent=None):
        super().__init__(parent)
        tint_titlebar(self)
        self.setWindowTitle(tr("Edit control") if control else tr("Add control"))
        self.setMinimumWidth(440)
        self.setModal(True)
        c = control or Control(kind="button", label="")
        self.result_control: Control | None = None

        form = QFormLayout(self)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setContentsMargins(16, 16, 16, 12)
        form.setSpacing(10)

        self._kind = QComboBox()
        for kind, name in self.KIND_NAMES:
            self._kind.addItem(tr(name), kind)
        self._kind.setCurrentIndex(max(0, self._kind.findData(c.kind)))
        self._kind.currentIndexChanged.connect(self._sync)
        self._label = QLineEdit(c.label)
        self._label.setPlaceholderText(tr("e.g. Reset, Fan, PWM"))
        self._command = QLineEdit(c.command)
        self._off_lbl = QLabel(tr("Command when off"))
        self._off = QLineEdit(c.command_off)
        self._off.setPlaceholderText("FAN OFF")

        self._range_lbl = QLabel(tr("Min · Max · Step"))
        self._range = QWidget()
        rng = QHBoxLayout(self._range)
        rng.setContentsMargins(0, 0, 0, 0)
        rng.setSpacing(6)
        self._min = self._number(c.minimum)
        self._max = self._number(c.maximum)
        self._step = self._number(c.step)
        for w in (self._min, self._max, self._step):
            rng.addWidget(w)

        self._key_lbl = QLabel(tr("Shortcut"))
        self._key = QComboBox()
        self._key.addItem(tr("None"), "")
        for key in FREE_KEYS:
            if key == c.key or key not in used_keys:
                self._key.addItem(key, key)
        self._key.setCurrentIndex(max(0, self._key.findData(c.key)))

        self._eol = QCheckBox(tr("Add the command bar's line ending"))
        self._eol.setChecked(c.line_ending)
        self._error = QLabel("")
        self._error.setObjectName("dimLabel")
        self._error.setWordWrap(True)
        self._error.setTextFormat(Qt.TextFormat.PlainText)

        form.addRow(tr("Type"), self._kind)
        form.addRow(tr("Name"), self._label)
        form.addRow(tr("Command"), self._command)
        form.addRow(self._off_lbl, self._off)
        form.addRow(self._range_lbl, self._range)
        form.addRow(self._key_lbl, self._key)
        form.addRow("", self._eol)
        hint = QLabel(tr("Escapes: \\r \\n \\t \\0 \\xHH (any byte)."))
        hint.setObjectName("dimLabel")
        form.addRow("", hint)
        form.addRow("", self._error)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self._value = c.value
        self._sync()

    @staticmethod
    def _number(value: float) -> NumberBox:
        box = NumberBox()
        box.setRange(-1e9, 1e9)
        box.setDecimals(4)
        box.setValue(value)
        return box

    def _sync(self) -> None:
        kind = self._kind.currentData()
        for w in (self._off_lbl, self._off):
            w.setVisible(kind == "toggle")
        for w in (self._range_lbl, self._range):
            w.setVisible(kind in ("slider", "number"))
        for w in (self._key_lbl, self._key):
            w.setVisible(kind in ("button", "toggle"))
        self._command.setPlaceholderText({
            "button": "AT+RESET", "toggle": "FAN ON",
        }.get(kind, "SET PWM={value}"))

    def _accept(self) -> None:
        kind = self._kind.currentData()
        c = Control(kind=kind, label=self._label.text().strip(), command=self._command.text(),
                    command_off=self._off.text() if kind == "toggle" else "",
                    minimum=self._min.value(), maximum=self._max.value(), step=self._step.value(),
                    value=self._value,
                    key=self._key.currentData() if kind in ("button", "toggle") else "",
                    line_ending=self._eol.isChecked())
        err = c.validate()
        if err:
            self._error.setText(tr(err))
            return
        if kind in ("slider", "number"):
            c.value = c.snap(c.value)
        self.result_control = c
        self.accept()


class _SliderRow(QWidget):
    """Label · slider · value. Sends while dragging, throttled, and always the
    final position, so a device on a slow link isn't flooded."""

    def __init__(self, control: Control, send: Callable[[Control, float], bool], parent=None):
        super().__init__(parent)
        self.control = control
        self._send = send
        self._last_sent: float | None = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(QLabel(control.label))
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, round((control.maximum - control.minimum) / control.step))
        self._slider.setValue(round((control.value - control.minimum) / control.step))
        self._slider.setToolTip(control.command)
        lay.addWidget(self._slider, 1)
        self._value_lbl = QLabel(control.format_value(control.value))
        self._value_lbl.setMinimumWidth(48)
        self._value_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self._value_lbl)
        self._timer = QTimer(self, singleShot=True, interval=SLIDER_THROTTLE_MS)
        self._timer.timeout.connect(self._flush)
        self._slider.valueChanged.connect(self._on_changed)
        self._slider.sliderReleased.connect(self._flush)

    def _on_changed(self, ticks: int) -> None:
        c = self.control
        c.value = c.snap(c.minimum + ticks * c.step)
        self._value_lbl.setText(c.format_value(c.value))
        if not self._timer.isActive():
            self._timer.start()

    def _flush(self) -> None:
        self._timer.stop()
        if self.control.value != self._last_sent:
            self._last_sent = self.control.value
            self._send(self.control, self.control.value)


class _NumberRow(QWidget):
    """Label · number box · Send (Enter sends too)."""

    def __init__(self, control: Control, send: Callable[[Control, float], bool], parent=None):
        super().__init__(parent)
        self.control = control
        self._send = send
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(QLabel(control.label))
        lay.addStretch()
        self._box = NumberBox()
        self._box.setRange(control.minimum, control.maximum)
        self._box.setDecimals(control.decimals)
        self._box.setSingleStep(control.step)
        self._box.setValue(control.value)
        self._box.setToolTip(control.command)
        self._box.setMinimumWidth(96)
        self._box.setKeyboardTracking(False)
        self._box.lineEdit().returnPressed.connect(self.submit)
        lay.addWidget(self._box)
        btn = QPushButton(tr("Send"))
        btn.setFixedHeight(22)
        btn.clicked.connect(self.submit)
        lay.addWidget(btn)

    def submit(self) -> None:
        c = self.control
        c.value = c.snap(self._box.value())
        self._box.setValue(c.value)
        self._send(c, c.value)


class ControlPanel(QWidget):
    """*send* takes ``(bytes, text shown in the terminal, add line ending)`` and
    returns whether they went out."""

    def __init__(self, send: Callable[[bytes, str, bool], bool], parent=None):
        super().__init__(parent)
        self._send_bytes = send
        self.controls: list[Control] = []
        self._toggles: dict[int, QCheckBox] = {}    # id(control) → its checkbox
        self._columns = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(scroll)
        self._body = QWidget()
        scroll.setWidget(self._body)
        self._lay = QVBoxLayout(self._body)
        self._lay.setContentsMargins(10, 8, 10, 8)
        self._lay.setSpacing(8)
        self._rebuild()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _clear(self) -> None:
        def wipe(layout) -> None:
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.setParent(None)
                    widget.deleteLater()
                elif item.layout() is not None:
                    wipe(item.layout())
        wipe(self._lay)

    def _fit_columns(self) -> int:
        return max(1, self.width() // BUTTON_MIN_WIDTH)

    def _rebuild(self) -> None:
        self._clear()
        self._toggles.clear()
        self._columns = self._fit_columns()
        if not self.controls:
            empty = QLabel(tr(
                "Buttons, toggles, sliders and number boxes that send commands to "
                "the active connection.\n\nUse + Add to create one, for example a "
                "slider that sends SET PWM={value}."))
            empty.setObjectName("dimLabel")
            empty.setWordWrap(True)
            self._lay.addWidget(empty)
            self._lay.addStretch()
            return
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        buttons = [c for c in self.controls if c.kind == "button"]
        for i, c in enumerate(buttons):
            btn = QPushButton(f"{c.label}   {c.key}" if c.key else c.label)
            btn.setFixedHeight(24)
            # The macOS layout rect leaves room for a focus ring, and the rows
            # overlap at this height (the same issue as themes._LayoutRectFix).
            btn.setAttribute(Qt.WidgetAttribute.WA_LayoutUsesWidgetRect)
            btn.setToolTip(c.command)
            btn.clicked.connect(lambda _=False, ctl=c: self.press(ctl))
            self._with_menu(btn, c)
            grid.addWidget(btn, i // self._columns, i % self._columns)
        self._lay.addLayout(grid)
        for c in self.controls:
            if c.kind == "toggle":
                row = QCheckBox(f"{c.label}   {c.key}" if c.key else c.label)
                row.setChecked(bool(c.value))
                row.setToolTip(f"{c.command}  /  {c.command_off}")
                row.clicked.connect(lambda on, ctl=c, w=row: self._on_toggle(ctl, w, on))
                self._toggles[id(c)] = row
            elif c.kind == "slider":
                row = _SliderRow(c, self._send_value)
            elif c.kind == "number":
                row = _NumberRow(c, self._send_value)
            else:
                continue
            self._with_menu(row, c)
            self._lay.addWidget(row)
        self._lay.addStretch()

    def resizeEvent(self, event):            # noqa: N802  (Qt naming)
        super().resizeEvent(event)
        if self._fit_columns() != self._columns:
            self._rebuild()

    def _with_menu(self, widget: QWidget, control: Control) -> None:
        widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        widget.customContextMenuRequested.connect(
            lambda pos, c=control, w=widget: self._menu(c, w.mapToGlobal(pos)))

    def _menu(self, control: Control, pos) -> None:
        menu = QMenu(self)
        edit = menu.addAction(tr("Edit…"))
        earlier = menu.addAction(tr("Move up"))
        later = menu.addAction(tr("Move down"))
        menu.addSeparator()
        delete = menu.addAction(tr("Delete"))
        chosen = menu.exec(pos)
        if chosen is edit:
            self.edit(control)
        elif chosen is earlier:
            self._move(control, -1)
        elif chosen is later:
            self._move(control, +1)
        elif chosen is delete:
            self.controls.remove(control)
            self._rebuild()

    def _move(self, control: Control, delta: int) -> None:
        """Swap with the neighbouring control of the same section (buttons are
        laid out apart from the rows, so an unrelated swap wouldn't show)."""
        same = [c for c in self.controls if (c.kind == "button") == (control.kind == "button")]
        j = same.index(control) + delta
        if 0 <= j < len(same):
            a, b = self.controls.index(control), self.controls.index(same[j])
            self.controls[a], self.controls[b] = self.controls[b], self.controls[a]
            self._rebuild()

    # ── Sending ───────────────────────────────────────────────────────────────

    @staticmethod
    def _shown(data: bytes) -> str:
        text = data.rstrip(b"\r\n").decode("utf-8", errors="replace")
        return text.translate(_CONTROL_TO_DOT)

    def _send(self, control: Control, data: bytes) -> bool:
        return self._send_bytes(data, self._shown(data), control.line_ending)

    def press(self, control: Control) -> None:
        self._send(control, control.render())

    def _on_toggle(self, control: Control, box: QCheckBox, on: bool) -> None:
        if self._send(control, control.render(on=on)):
            control.value = 1.0 if on else 0.0
        else:                                # shows what the device was last told
            box.setChecked(not on)

    def _send_value(self, control: Control, value: float) -> bool:
        return self._send(control, control.render(value))

    def fire_key(self, key: str) -> None:
        for c in self.controls:
            if c.key == key:
                if c.kind == "toggle":
                    self._toggles[id(c)].click()
                else:
                    self.press(c)
                return

    def run_commands(self) -> list[tuple[str, Callable[[], None]]]:
        """``(label, action)`` for the command palette."""
        out = []
        for c in self.controls:
            if c.kind == "button":
                out.append((c.label, lambda ctl=c: self.press(ctl)))
            elif c.kind == "toggle":
                out.append((c.label, self._toggles[id(c)].click))
        return out

    # ── Editing ───────────────────────────────────────────────────────────────

    def _used_keys(self, excluding: Control | None = None) -> set[str]:
        return {c.key for c in self.controls if c.key and c is not excluding}

    def add(self) -> None:
        dlg = ControlEditor(None, self._used_keys(), self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_control:
            self.controls.append(dlg.result_control)
            self._rebuild()

    def edit(self, control: Control) -> None:
        dlg = ControlEditor(control, self._used_keys(control), self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_control:
            self.controls[self.controls.index(control)] = dlg.result_control
            self._rebuild()

    # ── Persistence ───────────────────────────────────────────────────────────

    def to_dict_list(self) -> list[dict]:
        return [c.to_dict() for c in self.controls]

    def from_dict_list(self, data: object) -> None:
        self.controls = load_controls(data)
        self._rebuild()

    def export_file(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, tr("Export controls"), "controls.json",
                                              "JSON (*.json)")
        if path:
            Path(path).write_text(json.dumps(self.to_dict_list(), indent=2), encoding="utf-8")

    def import_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("Import controls"), "", "JSON (*.json)")
        if path:
            self.import_path(path)

    def import_path(self, path: str) -> None:
        """Add a file's controls to the current ones (nothing is replaced). A
        shortcut already in use stays with the existing control."""
        try:
            new = load_controls(json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, tr("Import controls"),
                                tr("Could not read the file:\n{err}").format(err=e))
            return
        used = self._used_keys()
        for c in new:
            if c.key in used:
                c.key = ""
        self.controls.extend(new)
        self._rebuild()
