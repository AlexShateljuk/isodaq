"""
ui/indicator_panel.py — Live value indicator grid with per-card colour thresholds.

Threshold model:
  Each card holds a sorted list of (min_value, color) pairs.
  On each update the highest entry where value >= min_value determines the color.
  If no threshold matches the base color (set at creation) is used.
  Double-click any card to open the threshold editor.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QColorDialog, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

COLS = 3


class _ThresholdRow(QWidget):
    def __init__(self, value: float, color: str, parent=None):
        super().__init__(parent)
        self._color = color
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        ge_lbl = QLabel("≥")
        ge_lbl.setFixedWidth(14)
        lay.addWidget(ge_lbl)

        self._spin = QDoubleSpinBox()
        self._spin.setRange(-1e9, 1e9)
        self._spin.setDecimals(3)
        self._spin.setValue(value)
        self._spin.setFixedWidth(100)
        lay.addWidget(self._spin)

        self._col_btn = QPushButton()
        self._col_btn.setFixedSize(22, 22)
        self._col_btn.clicked.connect(self._pick)
        self._refresh_btn()
        lay.addWidget(self._col_btn)

        del_btn = QPushButton("×")
        del_btn.setObjectName("delBtn")
        del_btn.setFixedSize(20, 20)
        del_btn.clicked.connect(self.deleteLater)
        lay.addWidget(del_btn)

    def _pick(self):
        c = QColorDialog.getColor(QColor(self._color), self, "Threshold colour")
        if c.isValid():
            self._color = c.name()
            self._refresh_btn()

    def _refresh_btn(self):
        self._col_btn.setStyleSheet(
            f"background:{self._color};border-radius:3px;"
            f"border:1px solid rgba(255,255,255,.2);")

    def value(self) -> float:
        return self._spin.value()

    def color(self) -> str:
        return self._color


class _ThresholdDialog(QDialog):
    def __init__(self, channel: str,
                 thresholds: list[tuple[float, str]],
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Thresholds — {channel}")
        self.setMinimumWidth(310)
        self.setModal(True)
        self._rows: list[_ThresholdRow] = []
        self._build(thresholds)

    def _build(self, thresholds: list[tuple[float, str]]):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 10)
        root.setSpacing(8)

        hint = QLabel("Color changes when value is ≥ threshold.\n"
                       "Highest matching threshold wins.")
        hint.setObjectName("dimLabel")
        hint.setWordWrap(True)
        root.addWidget(hint)

        # Scrollable row container
        self._rows_w = QWidget()
        self._rows_lay = QVBoxLayout(self._rows_w)
        self._rows_lay.setContentsMargins(0, 0, 0, 0)
        self._rows_lay.setSpacing(4)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._rows_w)
        scroll.setMaximumHeight(220)
        root.addWidget(scroll)

        for val, col in thresholds:
            self._add_row(val, col)

        add_btn = QPushButton("+ Add threshold")
        add_btn.setObjectName("add")
        add_btn.clicked.connect(lambda: self._add_row(0.0, "#f59e0b"))
        root.addWidget(add_btn)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        btns.button(QDialogButtonBox.StandardButton.Ok).setObjectName("save")
        root.addWidget(btns)

    def _add_row(self, value: float, color: str):
        row = _ThresholdRow(value, color, self._rows_w)
        self._rows_lay.addWidget(row)
        self._rows.append(row)
        row.destroyed.connect(lambda _, r=row: self._rows.remove(r)
                               if r in self._rows else None)

    def result_thresholds(self) -> list[tuple[float, str]]:
        pairs = [(r.value(), r.color()) for r in self._rows
                 if r.parent() is not None]
        return sorted(pairs, key=lambda x: x[0])


class _IndicatorCard(QFrame):
    def __init__(self, name: str, color: str = "#3ecf8e", parent=None):
        super().__init__(parent)
        self.ch_name  = name
        self._base_color = color
        self._thresholds: list[tuple[float, str]] = []
        self.setObjectName("indicatorCard")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(3)

        name_lbl = QLabel(name)
        name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_lbl.setStyleSheet(
            "font-size:9px;color:#6b7280;font-family:'JetBrains Mono';")
        lay.addWidget(name_lbl)

        self._val = QLabel("—")
        self._val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._set_color(color)
        lay.addWidget(self._val)

        self.setToolTip("Double-click to configure thresholds")

    def _set_color(self, color: str):
        self._val.setStyleSheet(
            f"font-family:'JetBrains Mono';font-size:17px;"
            f"font-weight:700;color:{color};")

    def _active_color(self, value: float) -> str:
        # Iterate thresholds descending — first match wins
        for threshold, color in reversed(self._thresholds):
            if value >= threshold:
                return color
        return self._base_color

    def update_value(self, value: float) -> None:
        self._val.setText(f"{value:.5g}")
        self._set_color(self._active_color(value))

    def set_thresholds(self, thresholds: list[tuple[float, str]]) -> None:
        self._thresholds = sorted(thresholds, key=lambda x: x[0])

    def get_thresholds(self) -> list[tuple[float, str]]:
        return list(self._thresholds)

    def mouseDoubleClickEvent(self, event):
        from ui.themes import tint_titlebar
        dlg = _ThresholdDialog(self.ch_name, self._thresholds, self)
        tint_titlebar(dlg)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.set_thresholds(dlg.result_thresholds())
        event.accept()


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

    # ── Threshold persistence ──────────────────────────────────────────────────

    def get_thresholds(self) -> dict[str, list[tuple[float, str]]]:
        return {name: card.get_thresholds()
                for name, card in self._cards.items()}

    def set_thresholds(self,
                       data: dict[str, list[list]]) -> None:
        for name, pairs in data.items():
            if name in self._cards:
                self._cards[name].set_thresholds(
                    [(float(v), str(c)) for v, c in pairs])

    # ── Data update ────────────────────────────────────────────────────────────

    def update(self, data: dict[str, float]) -> None:
        for name, card in self._cards.items():
            if name in data:
                card.update_value(data[name])
