"""
ui/trigger_events_panel.py — Session log of trigger match events.

Columns: Time | Trigger | Line | [one column per active parsed channel]
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

_BASE_COLS = ["Time", "Trigger", "Line"]


class TriggerEventsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._ch_names: list[str] = []
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Toolbar
        bar = QWidget()
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

        self._table = QTableWidget(0, len(_BASE_COLS))
        self._table.setHorizontalHeaderLabels(_BASE_COLS)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setWordWrap(False)
        self._table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        lay.addWidget(self._table)

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

    def add_event(self, ts: str, trigger_name: str, line: str,
                  parsed: dict[str, float]) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)
        for col, text in enumerate([ts, trigger_name, line]):
            item = QTableWidgetItem(text)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, col, item)
        for i, ch in enumerate(self._ch_names):
            text = f"{parsed[ch]:.5g}" if ch in parsed else ""
            item = QTableWidgetItem(text)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, len(_BASE_COLS) + i, item)
        self._table.scrollToBottom()
        self._count_lbl.setText(f"{row + 1} events")

    def clear(self) -> None:
        self._table.setRowCount(0)
        self._count_lbl.setText("0 events")
