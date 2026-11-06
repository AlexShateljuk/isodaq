"""ui/frame_panel.py — the Binary frames tool window: one connection's frame layout."""
from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QGridLayout,
                             QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
                             QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from core.binary_frames import (COVERAGE, LENGTHS, PRESETS, TYPES, Field, FrameDecoder, FrameSpec,
                                decode_sample)
from core.checksum import ALGORITHMS, parse_hex
from core.i18n import tr
from ui.number_box import NumberBox

COL_NAME, COL_TYPE, COL_SCALE, COL_VALUE, COL_DEL = range(5)
REFRESH_MS = 500


class FramePanel(QWidget):
    #: The decoder changed: readers need the new one.
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.decoder: FrameDecoder | None = None
        self._loading = False
        self._last_frames = 0
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.enabled = QCheckBox(tr("Decode binary frames"))
        self.enabled.setToolTip(tr("Turn the bytes from this device into channels, using the "
                                   "layout below"))
        self.enabled.toggled.connect(self._rebuild)
        bar.addWidget(self.enabled)
        self.preset = QComboBox()
        self.preset.addItem(tr("Own layout"), "")
        for name in PRESETS:
            self.preset.addItem(name, name)
        self.preset.activated.connect(self._apply_preset)
        bar.addWidget(self.preset)
        self.status = QLabel("")
        self.status.setObjectName("dimLabel")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        bar.addWidget(self.status, 1)
        root.addLayout(bar)

        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)
        self.header = self._hex_field(tr("e.g. AA 55"))
        self.tail = self._hex_field(tr("e.g. 0D 0A"))
        self.byte_order = self._combo(((tr("Little-endian"), False), (tr("Big-endian"), True)))
        grid.addWidget(self._dim(tr("Header")), 0, 0)
        grid.addWidget(self.header, 0, 1)
        grid.addWidget(self._dim(tr("Tail")), 0, 2)
        grid.addWidget(self.tail, 0, 3)
        grid.addWidget(self._dim(tr("Values")), 0, 4)
        grid.addWidget(self.byte_order, 0, 5)
        self.any_floats = QCheckBox(tr("Any number of f32 (ch1, ch2, …)"))
        self.any_floats.toggled.connect(self._on_edited)
        grid.addWidget(self.any_floats, 0, 6)

        self.length = self._combo(((tr("None"), ""), ("u8", "u8"), ("u16", "u16")))
        self.length_extra = NumberBox()
        self.length_extra.setDecimals(0)
        self.length_extra.setRange(0, 255)
        self.length_extra.setFixedSize(52, 24)
        self.length_extra.setToolTip(tr("If the device's length also counts other bytes, such as "
                                        "the header or checksum: how many"))
        self.length_extra.valueChanged.connect(self._on_edited)
        self.checksum = self._combo(((tr("None"), ""),) + tuple(
            (a.label, key) for key, a in ALGORITHMS.items()))
        self.checksum_over = self._combo(((tr("Whole frame before it"), COVERAGE[0]),
                                          (tr("After the header"), COVERAGE[1]),
                                          (tr("Fields only"), COVERAGE[2])))
        self.checksum_order = self._combo(((tr("High byte first"), False),
                                           (tr("Low byte first"), True)))
        grid.addWidget(self._dim(tr("Length")), 1, 0)
        grid.addWidget(self.length, 1, 1)
        grid.addWidget(self._dim(tr("Counts extra")), 1, 2)
        grid.addWidget(self.length_extra, 1, 3)
        grid.addWidget(self._dim(tr("Checksum")), 1, 4)
        grid.addWidget(self.checksum, 1, 5)
        checks = QHBoxLayout()
        checks.setSpacing(6)
        checks.addWidget(self.checksum_over)
        checks.addWidget(self.checksum_order)
        checks.addStretch()
        grid.addLayout(checks, 1, 6)
        grid.setColumnStretch(6, 1)
        root.addLayout(grid)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([tr("Name"), tr("Type"), tr("Scale"), tr("Value"), ""])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        for col, width in ((COL_TYPE, 90), (COL_SCALE, 80), (COL_VALUE, 120), (COL_DEL, 30)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(col, width)
        self.table.itemChanged.connect(self._on_item_changed)
        root.addWidget(self.table, 1)

        foot = QHBoxLayout()
        foot.setSpacing(6)
        self.show_other = QCheckBox(tr("Show other text"))
        self.show_other.setChecked(True)
        self.show_other.setToolTip(tr("Show text that comes between frames, such as a boot log"))
        self.show_other.toggled.connect(self._rebuild)
        foot.addWidget(self.show_other)
        foot.addSpacing(10)
        self.sample = QLineEdit()
        self.sample.setPlaceholderText(tr("Try a frame: paste its bytes in hex"))
        self.sample.returnPressed.connect(self.try_sample)
        foot.addWidget(self.sample, 1)
        try_btn = QPushButton(tr("Try"))
        try_btn.clicked.connect(self.try_sample)
        foot.addWidget(try_btn)
        self.sample_result = QLabel("")
        self.sample_result.setObjectName("dimLabel")
        self.sample_result.setTextFormat(Qt.TextFormat.PlainText)
        foot.addWidget(self.sample_result, 1)
        root.addLayout(foot)

        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self._refresh)
        self._on_edited()

    @staticmethod
    def _dim(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("dimLabel")
        return lbl

    def _hex_field(self, example: str) -> QLineEdit:
        edit = QLineEdit()
        edit.setPlaceholderText(example)
        edit.setMaximumWidth(150)
        edit.textChanged.connect(self._on_edited)
        return edit

    def _combo(self, items) -> QComboBox:
        combo = QComboBox()
        for label, data in items:
            combo.addItem(label, data)
        combo.currentIndexChanged.connect(self._on_edited)
        return combo

    # ── Rows ──────────────────────────────────────────────────────────────────

    def add_field(self, item: Field | None = None) -> None:
        if item is None:
            item = self._next_field()
        was_loading, self._loading = self._loading, True
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, COL_NAME, QTableWidgetItem(item.name))
        self.table.setItem(row, COL_SCALE, QTableWidgetItem(f"{item.scale:g}"))
        types = QComboBox()
        types.addItems(list(TYPES))
        types.setCurrentText(item.type)
        types.currentTextChanged.connect(self._on_edited)
        self.table.setCellWidget(row, COL_TYPE, types)
        value = QTableWidgetItem("—")
        value.setFlags(Qt.ItemFlag.ItemIsEnabled)
        value.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.table.setItem(row, COL_VALUE, value)
        delete = QPushButton("×")
        delete.setObjectName("delBtn")
        delete.clicked.connect(lambda _=False, b=delete: self._delete_row_of(b))
        self.table.setCellWidget(row, COL_DEL, delete)
        self._loading = was_loading
        self._on_edited()

    def _next_field(self) -> Field:
        names = {self.table.item(r, COL_NAME).text() for r in range(self.table.rowCount())}
        n = self.table.rowCount() + 1
        while f"ch{n}" in names:
            n += 1
        last = self.table.cellWidget(self.table.rowCount() - 1, COL_TYPE) if names else None
        return Field(f"ch{n}", last.currentText() if last is not None else "f32")

    def _delete_row_of(self, button: QPushButton) -> None:
        for row in range(self.table.rowCount()):
            if self.table.cellWidget(row, COL_DEL) is button:
                self.table.removeRow(row)
                self._on_edited()
                return

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if not self._loading and item.column() != COL_VALUE:
            self._on_edited()

    def _apply_preset(self) -> None:
        name = self.preset.currentData()
        if name:
            self.set_spec(PRESETS[name])
            self.preset.setCurrentIndex(self.preset.findData(name))

    def _on_edited(self, *_args) -> None:
        if self._loading:
            return
        floats = self.any_floats.isChecked()
        self.table.setEnabled(not floats)
        for widget in (self.header, self.length, self.length_extra, self.checksum):
            widget.setEnabled(not floats)
        has_checksum = bool(self.checksum.currentData()) and not floats
        self.checksum_over.setEnabled(has_checksum)
        self.checksum_order.setEnabled(has_checksum)
        self.length_extra.setEnabled(bool(self.length.currentData()) and not floats)
        self.preset.setCurrentIndex(0)
        self._rebuild()

    # ── Definition ────────────────────────────────────────────────────────────

    def spec(self) -> FrameSpec:
        fields = []
        for row in range(self.table.rowCount()):
            try:
                scale = float(self.table.item(row, COL_SCALE).text())
            except ValueError:
                scale = 0.0                  # validate() says what's wrong
            fields.append(Field(self.table.item(row, COL_NAME).text().strip(),
                                self.table.cellWidget(row, COL_TYPE).currentText(), scale))
        return FrameSpec(header=self.header.text().strip(), tail=self.tail.text().strip(),
                         fields=fields, big_endian=self.byte_order.currentData(),
                         any_floats=self.any_floats.isChecked(),
                         length=self.length.currentData(),
                         length_extra=int(self.length_extra.value()),
                         checksum=self.checksum.currentData(),
                         checksum_over=self.checksum_over.currentData(),
                         checksum_low_first=self.checksum_order.currentData())

    def set_spec(self, spec: FrameSpec) -> None:
        self._loading = True
        self.header.setText(spec.header)
        self.tail.setText(spec.tail)
        self.byte_order.setCurrentIndex(self.byte_order.findData(spec.big_endian))
        self.any_floats.setChecked(spec.any_floats)
        self.length.setCurrentIndex(max(0, self.length.findData(spec.length if spec.length in LENGTHS else "")))
        self.length_extra.setValue(spec.length_extra)
        self.checksum.setCurrentIndex(max(0, self.checksum.findData(spec.checksum)))
        self.checksum_over.setCurrentIndex(max(0, self.checksum_over.findData(spec.checksum_over)))
        self.checksum_order.setCurrentIndex(self.checksum_order.findData(spec.checksum_low_first))
        self.table.setRowCount(0)
        for item in spec.fields:
            self.add_field(item)
        self._loading = False
        self._on_edited()

    # ── Decoding ──────────────────────────────────────────────────────────────

    def _rebuild(self, *_args) -> None:
        """A new decoder for what the panel now says; ``None`` if off or invalid."""
        if self._loading:
            return
        spec = self.spec()
        problem = spec.validate()
        decoding = self.enabled.isChecked() and problem is None
        self.decoder = FrameDecoder(spec, self.show_other.isChecked()) if decoding else None
        self._last_frames = 0
        if decoding:
            self._timer.start()
        else:
            self._timer.stop()
        if problem and self.enabled.isChecked():
            self.status.setText(tr("Not decoding: ") + tr(problem))
        elif problem or not decoding:
            self.status.setText("")
        else:
            self._show_counts()
        self.changed.emit()

    def _refresh(self) -> None:
        decoder = self.decoder
        if decoder is None:
            return
        self._show_counts()
        self.update_values(decoder.last)

    def _show_counts(self) -> None:
        d = self.decoder
        rate = (d.frames - self._last_frames) * 1000 // REFRESH_MS
        self._last_frames = d.frames
        parts = [tr("{n} frames, {rate}/s").format(n=f"{d.frames:,}", rate=f"{rate:,}")]
        if d.bad_checksum:
            parts.append(tr("{n} bad checksum").format(n=f"{d.bad_checksum:,}"))
        if d.bad_length:
            parts.append(tr("{n} bad length or tail").format(n=f"{d.bad_length:,}"))
        if d.other_bytes:
            parts.append(tr("{n} other bytes").format(n=f"{d.other_bytes:,}"))
        self.status.setText("  ·  ".join(parts))

    def update_values(self, values: dict) -> None:
        self._loading = True
        for row in range(self.table.rowCount()):
            name = self.table.item(row, COL_NAME).text().strip()
            if name in values:
                self.table.item(row, COL_VALUE).setText(f"{values[name]:.10g}")
        self._loading = False

    def try_sample(self) -> None:
        """Decode pasted bytes, to try a layout out without a device."""
        spec = self.spec()
        problem = spec.validate()
        if problem:
            self.sample_result.setText(tr(problem))
            return
        try:
            data = parse_hex(self.sample.text())
        except ValueError as e:
            self.sample_result.setText(str(e))
            return
        d = decode_sample(spec, data)
        if d.frames:
            self.sample_result.setText("  ".join(f"{k}={v:.10g}" for k, v in d.last.items()))
        elif d.bad_checksum:
            self.sample_result.setText(tr("The checksum doesn't match."))
        elif d.bad_length:
            self.sample_result.setText(tr("The length or tail doesn't match."))
        else:
            size = spec.frame_size
            self.sample_result.setText(
                tr("Not a whole frame: {n} bytes, and this layout has {size}.").format(
                    n=len(data), size=size) if size else tr("Not a whole frame."))

    # ── Persistence ───────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {"enabled": self.enabled.isChecked(), "show_other": self.show_other.isChecked(),
                "spec": self.spec().to_dict()}

    def from_dict(self, data: object) -> None:
        if not isinstance(data, dict):
            return
        self._loading = True
        self.enabled.setChecked(bool(data.get("enabled", False)))
        self.show_other.setChecked(bool(data.get("show_other", True)))
        self._loading = False
        self.set_spec(FrameSpec.from_dict(data.get("spec")))

    def export_file(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, tr("Export frame layout"), "frame.json",
                                              "JSON (*.json)")
        if path:
            Path(path).write_text(json.dumps(self.spec().to_dict(), indent=2), encoding="utf-8")

    def import_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("Import frame layout"), "", "JSON (*.json)")
        if not path:
            return
        try:
            self.set_spec(FrameSpec.from_dict(json.loads(Path(path).read_text(encoding="utf-8"))))
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, tr("Import frame layout"),
                                tr("Could not read the file:\n{err}").format(err=e))
