"""
ui/analytics_panel.py — Per-trigger hit analytics (cumulative step chart + CSV export).

Each registered trigger gets a step-function curve: X = elapsed seconds,
Y = cumulative hit count.  The staircase shape makes burst periods and quiet
periods immediately visible.
"""
from __future__ import annotations

import csv
import time
from collections import deque

import pyqtgraph as pg
import pyqtgraph.exporters
from PyQt6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

MAX_HITS = 10_000


class AnalyticsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._t0 = time.monotonic()
        self._series: dict[str, dict] = {}   # name → {color, hits_t, count, curve}
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
        self._pi.getAxis("left").setLabel("Cumulative hits",
                                          **{"color": "#6a6a7a", "font-size": "9px"})
        self._plot.setMouseEnabled(x=True, y=True)
        lay.addWidget(self._plot)

        # Legend + buttons bar
        self._legend_w = QWidget()
        self._legend_w.setObjectName("sectionHeader")
        self._legend_lay = QHBoxLayout(self._legend_w)
        self._legend_lay.setContentsMargins(8, 4, 8, 4)
        self._legend_lay.setSpacing(12)
        self._legend_lay.addStretch()

        for label, slot in (("PNG", self._export_png),
                             ("CSV", self._export_csv),
                             ("Clear", self._clear)):
            btn = QPushButton(label)
            btn.setObjectName("iconBtn")
            btn.setFixedHeight(20)
            btn.setFixedWidth(36 if label != "Clear" else 44)
            btn.clicked.connect(slot)
            self._legend_lay.addWidget(btn)

        lay.addWidget(self._legend_w)

    # ── Theme ──────────────────────────────────────────────────────────────────

    def apply_theme(self, c: dict) -> None:
        self._plot.setBackground(c["bg"])
        for axis in ("bottom", "left"):
            self._pi.getAxis(axis).setPen(pg.mkPen(c["bg4"]))
            self._pi.getAxis(axis).setTextPen(pg.mkPen(c["fg_dim"]))
        self._pi.getAxis("bottom").setLabel(
            "Time", units="s", **{"color": c["fg_dim"], "font-size": "9px"})
        self._pi.getAxis("left").setLabel(
            "Cumulative hits", **{"color": c["fg_dim"], "font-size": "9px"})

    # ── Trigger registration ───────────────────────────────────────────────────

    def sync_triggers(self, triggers) -> None:
        """Align registered series with current trigger list (add/remove only)."""
        current = {t.name for t in triggers}
        for name in list(self._series):
            if name not in current:
                self._remove_series(name)
        for t in triggers:
            if t.name not in self._series:
                self._add_series(t.name, t.color)

    def _add_series(self, name: str, color: str) -> None:
        pen = pg.mkPen(color=color, width=1.5)
        curve = self._plot.plot([], [], pen=pen)
        self._series[name] = {
            "color": color,
            "hits_t": deque(maxlen=MAX_HITS),
            "count":  0,
            "curve":  curve,
        }
        lbl = QLabel(f"▲ {name}")
        lbl.setObjectName(f"anlbl_{name}")
        lbl.setStyleSheet(
            f"color:{color};font-size:10px;font-family:'JetBrains Mono';")
        self._legend_lay.insertWidget(self._legend_lay.count() - 4, lbl)

    def _remove_series(self, name: str) -> None:
        s = self._series.pop(name, None)
        if not s:
            return
        self._plot.removeItem(s["curve"])
        for i in range(self._legend_lay.count()):
            item = self._legend_lay.itemAt(i)
            w = item.widget() if item else None
            if w and w.objectName() == f"anlbl_{name}":
                w.deleteLater()
                break

    # ── Hit recording ──────────────────────────────────────────────────────────

    def record_hit(self, name: str) -> None:
        s = self._series.get(name)
        if s is None:
            return
        s["hits_t"].append(time.monotonic() - self._t0)
        s["count"] += 1
        self._redraw(name, s)

    def _redraw(self, name: str, s: dict) -> None:
        times = list(s["hits_t"])
        if not times:
            s["curve"].setData([], [])
            return
        now = time.monotonic() - self._t0
        # Build staircase: (0,0) → step at each hit → flat tail to now
        xs = [0.0]
        ys = [0]
        for i, t in enumerate(times, start=1):
            xs.extend([t, t])
            ys.extend([i - 1, i])
        xs.append(now)
        ys.append(len(times))
        s["curve"].setData(xs, ys)

    # ── Clear ──────────────────────────────────────────────────────────────────

    def _clear(self) -> None:
        self._t0 = time.monotonic()
        for name, s in self._series.items():
            s["hits_t"].clear()
            s["count"] = 0
            s["curve"].setData([], [])

    # ── Export ─────────────────────────────────────────────────────────────────

    def _export_png(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export analytics as PNG", "analytics.png", "PNG image (*.png)")
        if not path:
            return
        exp = pyqtgraph.exporters.ImageExporter(self._pi)
        exp.parameters()["width"] = 1920
        exp.export(path)

    def _export_csv(self) -> None:
        if not self._series:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export analytics as CSV", "analytics.csv", "CSV file (*.csv)")
        if not path:
            return
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["time_s", "trigger"])
            rows = []
            for name, s in self._series.items():
                for t in s["hits_t"]:
                    rows.append((t, name))
            rows.sort(key=lambda r: r[0])
            for t, name in rows:
                writer.writerow([f"{t:.4f}", name])
