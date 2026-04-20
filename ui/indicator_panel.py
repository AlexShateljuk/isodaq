"""
ui/indicator_panel.py — Live value indicator grid.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame, QGridLayout, QLabel, QVBoxLayout, QWidget,
)

COLS = 3


class _IndicatorCard(QFrame):
    def __init__(self, name: str, color: str = "#3ecf8e", parent=None):
        super().__init__(parent)
        self.ch_name = name
        self.setObjectName("indicatorCard")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(3)

        name_lbl = QLabel(name)
        name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_lbl.setStyleSheet("font-size:9px;color:#6b7280;font-family:'JetBrains Mono';")
        lay.addWidget(name_lbl)

        self._val = QLabel("—")
        self._val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._val.setStyleSheet(
            f"font-family:'JetBrains Mono';font-size:17px;"
            f"font-weight:700;color:{color};")
        lay.addWidget(self._val)

    def update_value(self, value: float) -> None:
        self._val.setText(f"{value:.5g}")


class IndicatorPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._cards: dict[str, _IndicatorCard] = {}
        self._colors: dict[str, str] = {}
        self._build()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(0)

        self._grid = QGridLayout()
        self._grid.setSpacing(6)
        outer.addLayout(self._grid)
        outer.addStretch()

    # ── Channel management ─────────────────────────────────────────────────────

    def add_indicator(self, name: str, color: str = "#3ecf8e") -> None:
        if name in self._cards:
            return
        self._colors[name] = color
        idx = len(self._cards)
        row, col = divmod(idx, COLS)
        card = _IndicatorCard(name, color)
        self._cards[name] = card
        self._grid.addWidget(card, row, col)

    def remove_indicator(self, name: str) -> None:
        card = self._cards.pop(name, None)
        self._colors.pop(name, None)
        if not card:
            return
        self._grid.removeWidget(card)
        card.deleteLater()
        self._reflow()

    def _reflow(self) -> None:
        for i, (name, card) in enumerate(self._cards.items()):
            row, col = divmod(i, COLS)
            self._grid.addWidget(card, row, col)

    # ── Data update ────────────────────────────────────────────────────────────

    def update(self, data: dict[str, float]) -> None:
        for name, card in self._cards.items():
            if name in data:
                card.update_value(data[name])
