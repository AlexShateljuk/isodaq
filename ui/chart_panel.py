"""
ui/chart_panel.py — Real-time scrolling chart panel (pyqtgraph).
"""
from __future__ import annotations

import time
from collections import deque

import pyqtgraph as pg
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

CHART_COLORS = [
    "#3ecf8e", "#ff7b54", "#60a5fa", "#f59e0b",
    "#a78bfa", "#f472b6", "#34d399", "#fb923c",
]
WINDOW_SEC = 60
MAX_POINTS = 20_000


class ChartPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._channels: dict[str, dict] = {}
        self._color_idx = 0
        self._t0 = time.monotonic()
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._plot = pg.PlotWidget(background="#1a1d2e")
        pi = self._plot.getPlotItem()
        pi.showGrid(x=True, y=True, alpha=0.15)
        for axis in ("bottom", "left"):
            pi.getAxis(axis).setPen(pg.mkPen("#3e4460"))
            pi.getAxis(axis).setTextPen(pg.mkPen("#6b7280"))
        pi.getAxis("bottom").setLabel("Time", units="s",
                                      **{"color": "#6b7280", "font-size": "9px"})
        self._plot.setMouseEnabled(x=True, y=True)
        lay.addWidget(self._plot)

        self._legend_w = QWidget()
        self._legend_w.setObjectName("sectionHeader")
        self._legend_lay = QHBoxLayout(self._legend_w)
        self._legend_lay.setContentsMargins(8, 4, 8, 4)
        self._legend_lay.setSpacing(12)
        self._legend_lay.addStretch()
        lay.addWidget(self._legend_w)

    # ── Channel management ─────────────────────────────────────────────────────

    def add_channel(self, name: str, color: str = "") -> None:
        if name in self._channels:
            return
        if not color:
            color = CHART_COLORS[self._color_idx % len(CHART_COLORS)]
            self._color_idx += 1
        pen = pg.mkPen(color=color, width=1.5)
        curve = self._plot.plot([], [], pen=pen)
        self._channels[name] = {
            "color": color,
            "buf_t": deque(maxlen=MAX_POINTS),
            "buf_v": deque(maxlen=MAX_POINTS),
            "curve": curve,
        }
        lbl = QLabel(f"▲ {name}")
        lbl.setObjectName(f"legend_{name}")
        lbl.setStyleSheet(f"color:{color};font-size:10px;font-family:'JetBrains Mono';")
        self._legend_lay.insertWidget(self._legend_lay.count() - 1, lbl)

    def remove_channel(self, name: str) -> None:
        ch = self._channels.pop(name, None)
        if not ch:
            return
        self._plot.removeItem(ch["curve"])
        for i in range(self._legend_lay.count()):
            item = self._legend_lay.itemAt(i)
            w = item.widget() if item else None
            if w and w.objectName() == f"legend_{name}":
                w.deleteLater()
                break

    # ── Data update ────────────────────────────────────────────────────────────

    def update(self, data: dict[str, float]) -> None:
        if not self._channels:
            return
        t = time.monotonic() - self._t0
        for name, ch in self._channels.items():
            if name in data:
                ch["buf_t"].append(t)
                ch["buf_v"].append(data[name])
                ch["curve"].setData(list(ch["buf_t"]), list(ch["buf_v"]))
        self._plot.setXRange(max(0.0, t - WINDOW_SEC), t + 0.5, padding=0)

    def clear(self) -> None:
        self._t0 = time.monotonic()
        for ch in self._channels.values():
            ch["buf_t"].clear()
            ch["buf_v"].clear()
            ch["curve"].setData([], [])
