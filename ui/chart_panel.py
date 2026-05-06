"""
ui/chart_panel.py — Real-time scrolling chart panel (pyqtgraph).
"""
from __future__ import annotations

import csv
import time
from collections import deque

import pyqtgraph as pg
import pyqtgraph.exporters
from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

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

        self._plot = pg.PlotWidget(background="#1e1e1e")
        self._pi = self._plot.getPlotItem()
        self._pi.showGrid(x=True, y=True, alpha=0.15)
        for axis in ("bottom", "left"):
            self._pi.getAxis(axis).setPen(pg.mkPen("#3e3e42"))
            self._pi.getAxis(axis).setTextPen(pg.mkPen("#6a6a7a"))
        self._pi.getAxis("bottom").setLabel("Time", units="s",
                                            **{"color": "#6a6a7a", "font-size": "9px"})
        self._plot.setMouseEnabled(x=True, y=True)
        lay.addWidget(self._plot)

        self._legend_w = QWidget()
        self._legend_w.setObjectName("sectionHeader")
        self._legend_lay = QHBoxLayout(self._legend_w)
        self._legend_lay.setContentsMargins(8, 4, 8, 4)
        self._legend_lay.setSpacing(12)
        self._legend_lay.addStretch()

        for label, slot in (("PNG", self._export_png), ("CSV", self._export_csv)):
            btn = QPushButton(label)
            btn.setObjectName("iconBtn")
            btn.setFixedHeight(20)
            btn.setFixedWidth(36)
            btn.clicked.connect(slot)
            self._legend_lay.addWidget(btn)

        lay.addWidget(self._legend_w)

    def apply_theme(self, c: dict) -> None:
        self._plot.setBackground(c["bg"])
        for axis in ("bottom", "left"):
            self._pi.getAxis(axis).setPen(pg.mkPen(c["bg4"]))
            self._pi.getAxis(axis).setTextPen(pg.mkPen(c["fg_dim"]))
        self._pi.getAxis("bottom").setLabel("Time", units="s",
                                            **{"color": c["fg_dim"], "font-size": "9px"})

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

    # ── Export ─────────────────────────────────────────────────────────────────

    def _export_png(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export chart as PNG", "chart.png", "PNG image (*.png)")
        if not path:
            return
        exp = pyqtgraph.exporters.ImageExporter(self._pi)
        exp.parameters()["width"] = 1920
        exp.export(path)

    def _export_csv(self) -> None:
        if not self._channels:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export chart data as CSV", "chart.csv", "CSV file (*.csv)")
        if not path:
            return
        names = list(self._channels.keys())
        # Build a unified time index: all timestamps from all channels, sorted
        all_t = sorted({t for ch in self._channels.values() for t in ch["buf_t"]})
        # Per-channel lookup: t -> value
        lookups = {
            name: dict(zip(ch["buf_t"], ch["buf_v"]))
            for name, ch in self._channels.items()
        }
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["time_s"] + names)
            for t in all_t:
                row = [f"{t:.4f}"] + [
                    f"{lookups[n][t]:.6g}" if t in lookups[n] else ""
                    for n in names
                ]
                writer.writerow(row)
