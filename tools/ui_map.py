#!/usr/bin/env python3
"""
tools/ui_map.py — render an annotated map of the main window.

Draws a numbered box around each key UI region and a legend listing the
region name + the widget objectName you'd reference when asking for a change.
A shared vocabulary picture so we point at the same thing.

Usage:
  QT_QPA_PLATFORM=offscreen python3 tools/ui_map.py   # writes ui_map.png
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication, QWidget          # noqa: E402
from PyQt6.QtGui import QPainter, QPen, QColor, QFont       # noqa: E402
from PyQt6.QtCore import QPoint, QRect, Qt                  # noqa: E402

from ui.themes import build_stylesheet                      # noqa: E402
from ui.main_window import MainWindow                       # noqa: E402


def _rect_in_window(win: QWidget, w: QWidget) -> QRect | None:
    if w is None or not w.isVisible():
        return None
    tl = w.mapTo(win, QPoint(0, 0))
    return QRect(tl, w.size())


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyleSheet(build_stylesheet("vscode"))
    w = MainWindow()
    w.resize(1440, 900)
    w.show()
    app.processEvents()

    # (label, widget, objectName-to-reference)
    items = [
        ("Port / connection bar", w.findChild(QWidget, "portBar"), "portBar"),
        ("Terminal (log output)", w._terminal, "terminal"),
        ("Find bar (Ctrl+F)",     w._search_bar, "searchBar"),
        ("Parser strip",          w._parser_strip_w, "parserField (fields)"),
        ("Command input",         w._cmd_edit, "cmdEdit"),
        ("Tab strip",             w._tabs.tabBar(), "QTabBar"),
        ("Tab page (chart/etc.)", w._chart_panel, "—"),
        ("Right sidebar",         w.findChild(QWidget, "sidebar"), "sidebar"),
    ]
    # open the find bar so it shows in the map
    w._open_search()
    app.processEvents()

    pm = w.grab()
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    num_font = QFont("Helvetica", 15, QFont.Weight.Bold)

    legend = []
    n = 0
    for label, widget, ref in items:
        r = _rect_in_window(w, widget)
        if r is None or r.width() < 4 or r.height() < 4:
            continue
        n += 1
        painter.setPen(QPen(QColor("#ff4d4f"), 2))
        painter.drawRect(r)
        # number badge at the top-left corner of the region
        badge = QRect(r.left() + 2, r.top() + 2, 26, 22)
        painter.fillRect(badge, QColor("#ff4d4f"))
        painter.setPen(QColor("#ffffff"))
        painter.setFont(num_font)
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, str(n))
        legend.append((n, label, ref))

    # legend panel (bottom-right)
    lp_w, lp_h = 360, 26 + 20 * len(legend)
    lp = QRect(pm.width() - lp_w - 16, pm.height() - lp_h - 16, lp_w, lp_h)
    painter.fillRect(lp, QColor(20, 20, 24, 235))
    painter.setPen(QColor("#ffffff"))
    painter.setFont(QFont("Helvetica", 12, QFont.Weight.Bold))
    painter.drawText(lp.left() + 12, lp.top() + 20, "UI regions")
    painter.setFont(QFont("Helvetica", 11))
    y = lp.top() + 44
    for num, label, ref in legend:
        painter.setPen(QColor("#ff8a8c"))
        painter.drawText(lp.left() + 12, y, f"{num}")
        painter.setPen(QColor("#e6e6e6"))
        painter.drawText(lp.left() + 34, y, f"{label}")
        painter.setPen(QColor("#8a8f98"))
        painter.drawText(lp.left() + 200, y, ref)
        y += 20

    painter.end()
    out = ROOT / "ui_map.png"
    pm.save(str(out))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
