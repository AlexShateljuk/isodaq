"""
ui/trigger_events_panel.py — Session log of trigger match events.

Columns: Time | Trigger | Line | [one column per active parsed channel]
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

_BASE_COLS = ["Time", "Trigger", "Line"]
_LINE_ID_ROLE = Qt.ItemDataRole.UserRole + 1


class TriggerEventsPanel(QWidget):
    jump_to_line = pyqtSignal(int)   # emitted with the terminal line id on double-click
    count_changed = pyqtSignal(int)  # row count, for the tool-stripe badge

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ch_names: list[str] = []
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Toolbar — hidden when the panel is embedded in a ToolDock, which
        # already provides a title row (see set_embedded).
        bar = QWidget()
        self._bar = bar
        bar.setObjectName("sectionHeader")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(8, 6, 8, 6)
        bl.setSpacing(6)
        lbl = QLabel("TRIGGER EVENTS")
        lbl.setObjectName("sectionTitle")
        bl.addWidget(lbl)
        bl.addStretch()
        self._count_lbl = QLabel("0 events")
        self._count_lbl.setObjectName("sectionTitle")
        bl.addWidget(self._count_lbl)
        clear_btn = QPushButton("Clear")
        clear_btn.setFixedHeight(20)
        clear_btn.clicked.connect(self.clear)
        bl.addWidget(clear_btn)
        lay.addWidget(bar)
        self.count = 0

        self._table = QTableWidget(0, len(_BASE_COLS))
        self._table.setHorizontalHeaderLabels(_BASE_COLS)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        # ResizeToContents re-measured *every* row on each insert (O(rows) per
        # event, ~8 ms at a few thousand rows); a sample sizes the columns.
        hh.setResizeContentsPrecision(64)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(22)   # IDE row density
        # One scroll-to-bottom per paint, not per event.
        self._scroll_timer = QTimer(self, singleShot=True, interval=50,
                                    timeout=self._table.scrollToBottom)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setWordWrap(False)
        self._table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self._table.setToolTip("Double-click a row to jump to that line in the terminal")
        self._table.cellDoubleClicked.connect(self._on_row_double_clicked)
        lay.addWidget(self._table)

    def _on_row_double_clicked(self, row: int, _col: int) -> None:
        item = self._table.item(row, 0)
        if item is None:
            return
        line_id = item.data(_LINE_ID_ROLE)
        self.jump_to_line.emit(int(line_id) if line_id is not None else -1)

    # ── Channel registration ───────────────────────────────────────────────────

    def register_channel(self, name: str) -> None:
        """Add a parsed-channel column (idempotent)."""
        if name in self._ch_names:
            return
        self._ch_names.append(name)
        n = len(_BASE_COLS) + len(self._ch_names)
        self._table.setColumnCount(n)
        self._table.setHorizontalHeaderLabels(
            _BASE_COLS + self._ch_names)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(n - 1, QHeaderView.ResizeMode.ResizeToContents)

    def unregister_channel(self, name: str) -> None:
        if name not in self._ch_names:
            return
        col = len(_BASE_COLS) + self._ch_names.index(name)
        self._ch_names.remove(name)
        self._table.removeColumn(col)
        self._table.setHorizontalHeaderLabels(
            _BASE_COLS + self._ch_names)

    # ── Event logging ─────────────────────────────────────────────────────────

    #: Oldest rows are dropped past this, so a trigger storm can't grow the
    #: table (and the GUI cost of every insert) without bound.
    MAX_ROWS = 5000

    def add_event(self, ts: str, trigger_name: str, line: str,
                  parsed: dict[str, float], line_id: int = -1) -> None:
        if self._table.rowCount() >= self.MAX_ROWS:
            self._table.removeRow(0)
        row = self._table.rowCount()
        self._table.insertRow(row)
        for col, text in enumerate([ts, trigger_name, line]):
            item = QTableWidgetItem(text)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if col == 0:
                item.setData(_LINE_ID_ROLE, int(line_id))
            self._table.setItem(row, col, item)
        for i, ch in enumerate(self._ch_names):
            text = f"{parsed[ch]:.5g}" if ch in parsed else ""
            item = QTableWidgetItem(text)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, len(_BASE_COLS) + i, item)
        if not self._scroll_timer.isActive():
            self._scroll_timer.start()
        self.count += 1   # total seen, even after old rows are dropped
        self._count_lbl.setText(f"{self.count} events")
        self.count_changed.emit(self.count)

    def clear(self) -> None:
        self._table.setRowCount(0)
        self._count_lbl.setText("0 events")
        self.count = 0
        self.count_changed.emit(0)

    def set_embedded(self, embedded: bool = True) -> None:
        """Hide the panel's own title row when it lives inside a ToolDock,
        which already shows the title + actions."""
        self._bar.setVisible(not embedded)
