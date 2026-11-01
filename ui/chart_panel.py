"""
ui/chart_panel.py — Real-time scrolling chart panel (pyqtgraph).
"""
from __future__ import annotations

import csv
import time
from collections import deque

import pyqtgraph as pg
import pyqtgraph.exporters
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel, QPushButton,
                             QVBoxLayout, QWidget)

CHART_COLORS = [
    "#3ecf8e", "#ff7b54", "#60a5fa", "#f59e0b",
    "#a78bfa", "#f472b6", "#34d399", "#fb923c",
]
WINDOW_SEC = 60
MAX_POINTS = 20_000
#: Choices in the chart footer (CMP14). Window 0 = everything in the buffer.
WINDOW_CHOICES = [("10 s", 10), ("30 s", 30), ("1 min", 60), ("5 min", 300),
                  ("15 min", 900), ("All", 0)]
POINT_CHOICES = [("2k pts", 2_000), ("20k pts", 20_000), ("100k pts", 100_000),
                 ("500k pts", 500_000)]
#: Curves are redrawn at most this often; samples are buffered in between.
#: Redrawing per sample capped the whole app at a few hundred lines/s.
REDRAW_MS = 33


class ChartPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._channels: dict[str, dict] = {}
        self._color_idx = 0
        self._t0 = time.monotonic()
        self._dirty = False
        self.window_sec = WINDOW_SEC     # visible time span (0 = whole buffer)
        self.max_points = MAX_POINTS     # samples kept per channel
        self.paused = False              # freeze the view; samples keep buffering
        self._build()
        self._redraw_timer = QTimer(self, interval=REDRAW_MS, timeout=self._redraw)
        self._redraw_timer.start()

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

        # Empty-state overlay — hidden once first channel is added
        self._empty_label = QLabel("No channels yet\nAdd a channel in the Parsing panel\nor connect a device and send data")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet(
            "color: rgba(255,255,255,0.18); font-size: 13px; line-height: 1.8;"
            "font-family: 'IBM Plex Sans', 'SF Pro Text', system-ui, sans-serif;"
        )
        self._empty_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        from PyQt6.QtWidgets import QStackedLayout
        stack = QWidget()
        sl = QStackedLayout(stack)
        sl.setStackingMode(QStackedLayout.StackingMode.StackAll)
        sl.addWidget(self._plot)
        sl.addWidget(self._empty_label)
        sl.setCurrentWidget(self._empty_label)
        lay.addWidget(stack)

        self._legend_w = QWidget()
        self._legend_w.setObjectName("sectionHeader")
        self._legend_lay = QHBoxLayout(self._legend_w)
        self._legend_lay.setContentsMargins(8, 4, 8, 4)
        self._legend_lay.setSpacing(12)
        self._legend_lay.addStretch()

        # View controls (CMP14): time window, buffer size, pause, Y lock.
        self._win_combo = QComboBox()
        for label, sec in WINDOW_CHOICES:
            self._win_combo.addItem(label, sec)
        self._win_combo.setCurrentIndex([s for _, s in WINDOW_CHOICES].index(WINDOW_SEC))
        self._win_combo.setToolTip("Visible time window")
        self._win_combo.currentIndexChanged.connect(
            lambda _i: self.set_window(self._win_combo.currentData()))
        self._pts_combo = QComboBox()
        for label, n in POINT_CHOICES:
            self._pts_combo.addItem(label, n)
        self._pts_combo.setCurrentIndex([n for _, n in POINT_CHOICES].index(MAX_POINTS))
        self._pts_combo.setToolTip("Samples kept per channel (older ones are dropped)")
        self._pts_combo.currentIndexChanged.connect(
            lambda _i: self.set_max_points(self._pts_combo.currentData()))
        self._pause_btn = QPushButton("❚❚")
        self._pause_btn.setCheckable(True)
        self._pause_btn.setToolTip("Pause the view (data keeps recording) — "
                                   "pan and zoom freely while paused")
        self._pause_btn.toggled.connect(self.set_paused)
        self._ylock_btn = QPushButton("Y🔒")
        self._ylock_btn.setCheckable(True)
        self._ylock_btn.setToolTip("Lock the Y range at its current span (off = auto-scale)")
        self._ylock_btn.toggled.connect(self.set_y_locked)
        for w, width in ((self._win_combo, 74), (self._pts_combo, 84),
                         (self._pause_btn, 30), (self._ylock_btn, 36)):
            w.setFixedHeight(20)
            w.setFixedWidth(width)
            if isinstance(w, QPushButton):
                w.setObjectName("iconBtn")
            self._legend_lay.addWidget(w)

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
        # Draw only what is on screen, peak-decimated to the pixel width — keeps
        # a full 20k-point buffer cheap to repaint.
        curve.setClipToView(True)
        curve.setDownsampling(auto=True, method="peak")
        self._channels[name] = {
            "color": color,
            "buf_t": deque(maxlen=self.max_points),
            "buf_v": deque(maxlen=self.max_points),
            "curve": curve,
        }
        lbl = QLabel(f"▲ {name}")
        lbl.setObjectName(f"legend_{name}")
        lbl.setStyleSheet(
            f"color:{color};font-size:10px;"
            "font-family:'JetBrains Mono','SF Mono','Consolas',monospace;"
        )
        # Legend entries sit left of the stretch; view controls stay right.
        self._legend_lay.insertWidget(len(self._channels) - 1, lbl)
        self._empty_label.setVisible(False)

    def remove_channel(self, name: str) -> None:
        ch = self._channels.pop(name, None)
        if not ch:
            return
        self._plot.removeItem(ch["curve"])
        if not self._channels:
            self._empty_label.setVisible(True)
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
                ch["dirty"] = True
                self._dirty = True

    def _redraw(self) -> None:
        """Push buffered samples to the curves (timer-driven). Skipped while
        the chart is hidden — a background connection or closed dock costs
        nothing; it catches up the next time it is shown."""
        if not self._dirty or self.paused or not self.isVisible():
            return
        self._dirty = False
        for ch in self._channels.values():
            if ch.pop("dirty", False):
                ch["curve"].setData(list(ch["buf_t"]), list(ch["buf_v"]))
        t = time.monotonic() - self._t0
        if self.window_sec:
            start = max(0.0, t - self.window_sec)
        else:
            firsts = [ch["buf_t"][0] for ch in self._channels.values() if ch["buf_t"]]
            start = min(firsts, default=0.0)
        self._plot.setXRange(start, t + 0.5, padding=0)

    # ── View controls (CMP14) ──────────────────────────────────────────────────

    def set_window(self, sec: int) -> None:
        self.window_sec = int(sec)
        self._mark_all_dirty()

    def set_max_points(self, n: int) -> None:
        """Resize every channel's buffer, keeping the newest samples."""
        self.max_points = int(n)
        for ch in self._channels.values():
            ch["buf_t"] = deque(ch["buf_t"], maxlen=self.max_points)
            ch["buf_v"] = deque(ch["buf_v"], maxlen=self.max_points)
        self._mark_all_dirty()

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        if self._pause_btn.isChecked() != paused:
            self._pause_btn.setChecked(paused)
        self._pause_btn.setText("▶" if paused else "❚❚")
        if not paused:
            self._mark_all_dirty()

    def set_y_locked(self, locked: bool) -> None:
        if locked:
            lo, hi = self._pi.viewRange()[1]
            self._pi.setYRange(lo, hi, padding=0)   # also turns Y auto-range off
        else:
            self._pi.enableAutoRange(axis="y")

    def _mark_all_dirty(self) -> None:
        for ch in self._channels.values():
            ch["dirty"] = True
        self._dirty = True
        self._redraw()

    def showEvent(self, event):              # noqa: N802  (Qt naming)
        super().showEvent(event)
        self._redraw()

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
