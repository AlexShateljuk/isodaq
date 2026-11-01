"""Port list entries with the OS's description of each port (CMP4).

The combo's item *text* stays the bare device name (``/dev/cu.usbserial-0001``,
``COM5``), because the rest of the app reads ``currentText()``. The description
("CP2102 USB to UART Bridge Controller") and USB identity are stored as item
data and drawn dimmed to the right in the drop-down by :class:`PortDelegate`;
they are also the item's tooltip.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontMetrics, QPalette
from PyQt6.QtWidgets import QComboBox, QStyle, QStyledItemDelegate

DESC_ROLE = Qt.ItemDataRole.UserRole + 1


def describe(info: dict) -> str:
    """Tooltip text: description plus USB VID:PID and serial number."""
    parts = [info.get("description") or ""]
    if info.get("vid") is not None:
        usb = f"USB {info['vid']:04X}:{info['pid'] or 0:04X}"
        if info.get("serial"):
            usb += f"  SN {info['serial']}"
        parts.append(usb)
    return "\n".join(p for p in parts if p)


def fill(combo: QComboBox, infos: list[dict], empty_text: str) -> None:
    """Replace the combo's items with *infos* (from ``SerialReader.port_info``)."""
    combo.clear()
    if not infos:
        combo.addItem(empty_text)
        return
    for i, info in enumerate(infos):
        combo.addItem(info["device"])
        combo.setItemData(i, info.get("description") or "", DESC_ROLE)
        tip = describe(info)
        if tip:
            combo.setItemData(i, f"{info['device']}\n{tip}", Qt.ItemDataRole.ToolTipRole)


class PortDelegate(QStyledItemDelegate):
    """Draws ``device   description`` with the description dimmed and elided."""

    def paint(self, painter, option, index):
        desc = index.data(DESC_ROLE) or ""
        self.initStyleOption(option, index)
        device = option.text
        option.text = ""
        style = option.widget.style() if option.widget else None
        if style is not None:
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, option, painter,
                              option.widget)
        rect = option.rect.adjusted(8, 0, -8, 0)
        # The combo's own (stylesheet) font, so rows match the closed combo.
        font = self.parent().font() if self.parent() is not None else option.font
        painter.save()
        painter.setFont(font)
        fm = painter.fontMetrics()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        fg = option.palette.color(QPalette.ColorRole.HighlightedText if selected
                                  else QPalette.ColorRole.Text)
        painter.setPen(fg)
        dev_w = fm.horizontalAdvance(device)
        painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         device)
        if not desc:
            painter.restore()
            return
        dim = fg
        dim.setAlpha(140)
        painter.setPen(dim)
        gap = fm.horizontalAdvance("    ")
        desc_rect = rect.adjusted(dev_w + gap, 0, 0, 0)
        painter.drawText(desc_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         fm.elidedText(desc, Qt.TextElideMode.ElideRight, desc_rect.width()))
        painter.restore()

    def sizeHint(self, option, index):            # pylint: disable=invalid-name
        size = super().sizeHint(option, index)
        fm = QFontMetrics(self.parent().font()) if self.parent() is not None else option.fontMetrics
        desc = index.data(DESC_ROLE) or ""
        text = index.data() + ("    " + desc if desc else "")
        size.setWidth(fm.horizontalAdvance(text) + 16)
        return size
