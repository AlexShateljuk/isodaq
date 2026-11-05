"""ui/number_box.py — a number field for engineering values."""
from __future__ import annotations

from PyQt6.QtCore import QLocale
from PyQt6.QtWidgets import QAbstractSpinBox, QDoubleSpinBox


class NumberBox(QDoubleSpinBox):
    """Always a dot and no padding zeros: with a comma locale a threshold of 50
    read ``50,000``. No step buttons (the theme can't draw their arrows); the
    arrow keys and the wheel still step."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLocale(QLocale.c())
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.setGroupSeparatorShown(False)

    def textFromValue(self, value: float) -> str:   # noqa: N802  (Qt naming)
        text = f"{value:.{self.decimals()}f}"
        return text.rstrip("0").rstrip(".") if "." in text else text
