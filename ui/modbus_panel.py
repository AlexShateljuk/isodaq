"""ui/modbus_panel.py — the Modbus tool window: one connection's register map."""
from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QHBoxLayout,
                             QHeaderView, QLabel, QMessageBox, QPushButton, QSpinBox,
                             QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from core.i18n import tr
from core.modbus import TABLES, Point, load_points
from core.modbus_reader import DEFAULT_POLL_MS, MIN_POLL_MS

COL_NAME, COL_UNIT, COL_TABLE, COL_ADDR, COL_FMT, COL_SCALE, COL_VALUE, COL_DEL = range(8)
FORMAT_NAMES = {"u16": "u16", "i16": "i16", "u32": "u32", "i32": "i32", "f32": "f32",
                "u32s": "u32 swapped", "i32s": "i32 swapped", "f32s": "f32 swapped"}


class ModbusPanel(QWidget):
    #: The map, poll rate or frame display changed.
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(self._dim(tr("Poll every")))
        self.poll = QSpinBox()
        self.poll.setRange(MIN_POLL_MS, 3_600_000)
        self.poll.setSingleStep(100)
        self.poll.setSuffix(" ms")
        self.poll.setValue(DEFAULT_POLL_MS)
        self.poll.setMinimumWidth(110)
        self.poll.valueChanged.connect(lambda _v: self.changed.emit())
        bar.addWidget(self.poll)
        self.frames = QCheckBox(tr("Show frames"))
        self.frames.setToolTip(tr("Show each request and answer in the terminal, as hex"))
        self.frames.toggled.connect(lambda _on: self.changed.emit())
        bar.addWidget(self.frames)
        self.status = QLabel("")
        self.status.setObjectName("dimLabel")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        bar.addWidget(self.status, 1)
        root.addLayout(bar)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels([
            tr("Name"), tr("Unit"), tr("Table"), tr("Address"), tr("Format"), tr("Scale"),
            tr("Value"), ""])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        for col, width in ((COL_UNIT, 60), (COL_TABLE, 110), (COL_ADDR, 80), (COL_FMT, 130),
                           (COL_SCALE, 80), (COL_VALUE, 110), (COL_DEL, 30)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(col, width)
        self.table.itemChanged.connect(self._on_item_changed)
        root.addWidget(self.table, 1)

        self.hint = QLabel(tr(
            "Addresses count from 0: holding register 40001 in a device manual is address "
            "0. To write, send: write NAME VALUE"))
        self.hint.setObjectName("dimLabel")
        self.hint.setWordWrap(True)
        root.addWidget(self.hint)

    @staticmethod
    def _dim(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("dimLabel")
        return lbl

    # ── Rows ──────────────────────────────────────────────────────────────────

    def add_point(self, point: Point | None = None) -> None:
        if point is None:
            point = self._next_point()
        self._loading = True
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, COL_NAME, QTableWidgetItem(point.name))
        self.table.setItem(row, COL_UNIT, QTableWidgetItem(str(point.unit)))
        self.table.setItem(row, COL_ADDR, QTableWidgetItem(str(point.address)))
        self.table.setItem(row, COL_SCALE, QTableWidgetItem(f"{point.scale:g}"))
        value = QTableWidgetItem("—")
        value.setFlags(Qt.ItemFlag.ItemIsEnabled)
        value.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.table.setItem(row, COL_VALUE, value)

        tables = QComboBox()
        tables.addItems(list(TABLES))
        tables.setCurrentText(point.table)
        tables.currentTextChanged.connect(self._on_edited)
        self.table.setCellWidget(row, COL_TABLE, tables)
        formats = QComboBox()
        for code, label in FORMAT_NAMES.items():
            formats.addItem(label, code)
        formats.setCurrentIndex(max(0, formats.findData(point.fmt)))
        formats.setEnabled(not point.is_bit)
        formats.currentIndexChanged.connect(self._on_edited)
        self.table.setCellWidget(row, COL_FMT, formats)
        delete = QPushButton("×")
        delete.setObjectName("delBtn")
        delete.clicked.connect(lambda _=False, b=delete: self._delete_row_of(b))
        self.table.setCellWidget(row, COL_DEL, delete)
        self._loading = False
        self._on_edited()

    def _next_point(self) -> Point:
        """A sensible new row: the address after the last one, a free name."""
        points = self.points()
        last = points[-1] if points else None
        names = {p.name for p in points}
        n = len(points) + 1
        while f"reg{n}" in names:
            n += 1
        if last is None:
            return Point(f"reg{n}")
        return Point(f"reg{n}", last.unit, last.table, min(last.address + last.size, 65535),
                     last.fmt, last.scale)

    def _delete_row_of(self, button: QPushButton) -> None:
        for row in range(self.table.rowCount()):
            if self.table.cellWidget(row, COL_DEL) is button:
                self.table.removeRow(row)
                self._on_edited()
                return

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if not self._loading and item.column() != COL_VALUE:
            self._on_edited()

    def _on_edited(self, *_args) -> None:
        if self._loading:
            return
        for row in range(self.table.rowCount()):
            bit = self.table.cellWidget(row, COL_TABLE).currentText() in ("coil", "discrete")
            self.table.cellWidget(row, COL_FMT).setEnabled(not bit)
        self._mark_invalid()
        self.changed.emit()

    def _row_point(self, row: int) -> Point | None:
        """The row as a point, or ``None`` if a number field isn't a number."""
        try:
            return Point(name=self.table.item(row, COL_NAME).text().strip(),
                         unit=int(self.table.item(row, COL_UNIT).text()),
                         table=self.table.cellWidget(row, COL_TABLE).currentText(),
                         address=int(self.table.item(row, COL_ADDR).text()),
                         fmt=self.table.cellWidget(row, COL_FMT).currentData(),
                         scale=float(self.table.item(row, COL_SCALE).text()))
        except ValueError:
            return None

    def _mark_invalid(self) -> None:
        """Say why a row isn't being polled, and give the row a tooltip with it."""
        problems = []
        seen = set()
        self._loading = True
        for row in range(self.table.rowCount()):
            p = self._row_point(row)
            why = "Unit, address and scale must be numbers." if p is None else p.validate()
            if p is not None and why is None and p.name in seen:
                why = f"The name {p.name} is used twice."
            if p is not None:
                seen.add(p.name)
            value = self.table.item(row, COL_VALUE)
            value.setToolTip(tr(why) if why else "")
            if why:
                value.setText(tr("invalid"))
                problems.append(f"{row + 1}: {tr(why)}")
            elif value.text() == tr("invalid"):
                value.setText("—")
        self._loading = False
        if problems:
            self.status.setText(tr("Not polled, row ") + problems[0])

    def points(self) -> list[Point]:
        """The rows that can be polled."""
        out, seen = [], set()
        for row in range(self.table.rowCount()):
            p = self._row_point(row)
            if p is not None and p.validate() is None and p.name not in seen:
                seen.add(p.name)
                out.append(p)
        return out

    def update_values(self, values: dict[str, float]) -> None:
        self._loading = True
        for row in range(self.table.rowCount()):
            name = self.table.item(row, COL_NAME).text().strip()
            if name in values:
                self.table.item(row, COL_VALUE).setText(f"{values[name]:.10g}")
        self._loading = False

    # ── Persistence ───────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {"poll_ms": self.poll.value(), "points": [p.to_dict() for p in self.points()]}

    def from_dict(self, data: object) -> None:
        if not isinstance(data, dict):
            return
        self.table.setRowCount(0)
        for p in load_points(data.get("points")):
            self.add_point(p)
        try:
            self.poll.setValue(int(data.get("poll_ms", DEFAULT_POLL_MS)))
        except (TypeError, ValueError):
            pass

    def export_file(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, tr("Export register map"), "registers.json",
                                              "JSON (*.json)")
        if path:
            Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    def import_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("Import register map"), "", "JSON (*.json)")
        if not path:
            return
        try:
            self.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, tr("Import register map"),
                                tr("Could not read the file:\n{err}").format(err=e))
