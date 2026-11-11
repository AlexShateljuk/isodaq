"""
ui/chart_panel.py — Real-time scrolling chart panel (pyqtgraph).
"""
from __future__ import annotations

import csv
import time
from collections import deque

import numpy as np
import pyqtgraph as pg
import pyqtgraph.exporters
from PyQt6.QtCore import QEvent, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel, QPushButton,
                             QStackedWidget, QVBoxLayout, QWidget)

from core.i18n import tr
from core.signal_math import normalise
from ui.chart_views import VIEWS

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
#: Quick Plot adds at most this many series on its own (CMP5).
MAX_AUTO_CHANNELS = 16
#: The View choices in the chart footer: over time (CH1), or another view (CMP13).
TIME_VIEWS = ("shared", "rows", "normalised")
VIEW_LABELS = (("shared", "Over time"), ("rows", "Separate rows"), ("normalised", "Normalised"),
               ("xy", "X against Y"), ("spectrum", "Spectrum"), ("histogram", "Histogram"),
               ("bars", "Bars"), ("gauges", "Gauges"), ("compass", "Compass"))
ANALYSIS_REFRESH_MS = 200
LEFT_AXIS_WIDTH = 58
DARK = {"bg": "#1e1e1e", "bg4": "#3e3e42", "fg": "#d4d4d4", "fg_dim": "#6a6a7a"}


_MONO = "'JetBrains Mono','SF Mono','Consolas',monospace"


class ChartPanel(QWidget):
    #: Legend → "Keep as Parsing channel" on a Quick Plot series.
    keep_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._channels: dict[str, dict] = {}
        self._color_idx = 0
        self._t0 = time.monotonic()
        self._dirty = False
        self.window_sec = WINDOW_SEC     # visible time span (0 = whole buffer)
        self.max_points = MAX_POINTS     # samples kept per channel
        self.paused = False              # freeze the view; samples keep buffering
        # Quick Plot (CMP5): series added automatically from data-only lines.
        self.quick_enabled = True
        self._auto: set[str] = set()       # names added by Quick Plot
        self._hidden: set[str] = set()     # auto names the user removed
        self.view = "shared"
        self.theme = dict(DARK)
        self._rows: list = []              # extra plots for Separate rows, reused
        self._cross: dict = {}             # plot → its crosshair line
        self._analysis: dict = {}          # view name → its widget, made when first used
        self._build()
        self._analysis_timer = QTimer(self, interval=ANALYSIS_REFRESH_MS,
                                      timeout=self._refresh_analysis)
        self._redraw_timer = QTimer(self, interval=REDRAW_MS, timeout=self._redraw)
        self._redraw_timer.start()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._canvas = pg.GraphicsLayoutWidget()
        self._canvas.setBackground(DARK["bg"])
        self._pi = self._canvas.addPlot(row=0, col=0)
        self._plot = self._pi
        self._style_plot(self._pi)
        self._pi.setMouseEnabled(x=True, y=True)
        self._canvas.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self._canvas.installEventFilter(self)
        self._canvas.setMouseTracking(True)
        self._readout = QLabel(self._canvas)
        self._readout.setObjectName("chartReadout")
        self._readout.setTextFormat(Qt.TextFormat.RichText)
        self._readout.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._readout.hide()
        self._style_readout()

        # Empty-state overlay — hidden once first channel is added
        self._empty_label = QLabel(
            "No data to plot yet\n\n"
            "Lines of numbers are plotted automatically:\n"
            "12.5, 40, -3     temp:21.5 hum:40     >temp:21.5 (Teleplot)\n\n"
            "or add a channel in the Parsing panel")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._style_empty_label("#6a6a7a")
        self._empty_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        from PyQt6.QtWidgets import QStackedLayout
        stack = QWidget()
        sl = QStackedLayout(stack)
        sl.setStackingMode(QStackedLayout.StackingMode.StackAll)
        self._views = QStackedWidget()
        self._views.addWidget(self._canvas)
        sl.addWidget(self._views)
        sl.addWidget(self._empty_label)
        sl.setCurrentWidget(self._empty_label)
        lay.addWidget(stack)

        self._legend_w = QWidget()
        self._legend_w.setObjectName("sectionHeader")
        self._legend_lay = QHBoxLayout(self._legend_w)
        self._legend_lay.setContentsMargins(8, 4, 8, 4)
        self._legend_lay.setSpacing(12)
        self._legend_lay.addStretch()

        self._auto_btn = QPushButton("Auto")
        self._auto_btn.setCheckable(True)
        self._auto_btn.setChecked(True)
        self._auto_btn.setToolTip(
            "Quick Plot: plot data lines without setting up channels —\n"
            "12.5, 40, -3   ·   temp:21.5 hum:40   ·   Teleplot >temp:21.5\n"
            "Right-click a series in the legend to keep or hide it.")
        self._auto_btn.toggled.connect(self.set_quick_enabled)

        self._view_combo = QComboBox()
        for key, label in VIEW_LABELS:
            self._view_combo.addItem(tr(label), key)
        self._view_combo.setToolTip(tr(
            "How to show the channels. Separate rows gives each one its own Y axis; "
            "Normalised overlays them, each scaled to its own range."))
        self._view_combo.currentIndexChanged.connect(
            lambda _i: self.set_view(self._view_combo.currentData()))

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
        for w, width in ((self._auto_btn, 40), (self._view_combo, 126), (self._win_combo, 74),
                         (self._pts_combo, 84),
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

    def _style_empty_label(self, color: str) -> None:
        self._empty_label.setStyleSheet(
            f"color: {color}; font-size: 13px; line-height: 1.8;"
            "font-family: 'IBM Plex Sans', 'SF Pro Text', system-ui, sans-serif;"
        )

    def _style_plot(self, plot) -> None:
        c = self.theme
        plot.showGrid(x=True, y=True, alpha=0.15)
        for axis in ("bottom", "left"):
            plot.getAxis(axis).setPen(pg.mkPen(c["bg4"]))
            plot.getAxis(axis).setTextPen(pg.mkPen(c["fg_dim"]))
        plot.getAxis("bottom").setLabel("Time", units="s",
                                        **{"color": c["fg_dim"], "font-size": "9px"})

    def _style_readout(self) -> None:
        c = self.theme
        self._readout.setStyleSheet(
            f"background:{c['bg']};border:1px solid {c['bg4']};border-radius:3px;"
            f"padding:3px 6px;color:{c['fg']};font-size:10px;font-family:{_MONO};")

    def apply_theme(self, c: dict) -> None:
        self.theme = dict(c)
        self._style_empty_label(c["fg_dim"])
        self._canvas.setBackground(c["bg"])
        for plot in self._plots():
            self._style_plot(plot)
        self._style_readout()
        for view in self._analysis.values():
            view.apply_theme(self.theme)
        if self.view == "rows":
            self._arrange()

    # ── Channel management ─────────────────────────────────────────────────────

    def add_channel(self, name: str, color: str = "", auto: bool = False) -> None:
        if name in self._channels:
            if not auto and name in self._auto:      # a Parsing channel took it over
                self._auto.discard(name)
                self._style_legend(name)
            return
        if auto:
            self._auto.add(name)
        else:
            self._hidden.discard(name)
        if not color:
            color = CHART_COLORS[self._color_idx % len(CHART_COLORS)]
            self._color_idx += 1
        pen = pg.mkPen(color=color, width=1.5)
        curve = pg.PlotDataItem([], [], pen=pen)
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
        lbl = QLabel()
        lbl.setObjectName(f"legend_{name}")
        lbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        lbl.customContextMenuRequested.connect(
            lambda pos, n=name, w=lbl: self._legend_menu(n, w.mapToGlobal(pos)))
        self._channels[name]["label"] = lbl
        self._style_legend(name)
        # Legend entries sit left of the stretch; view controls stay right.
        self._legend_lay.insertWidget(len(self._channels) - 1, lbl)
        self._empty_label.setVisible(False)
        self._channels_changed()

    def _style_legend(self, name: str) -> None:
        ch = self._channels[name]
        auto = name in self._auto
        # Quick Plot series: hollow marker + italic until kept.
        ch["label"].setText(f"{'△' if auto else '▲'} {name}")
        ch["label"].setToolTip(
            "Quick Plot series — right-click to keep it as a Parsing channel or hide it"
            if auto else "")
        ch["label"].setStyleSheet(
            f"color:{ch['color']};font-size:10px;"
            f"font-style:{'italic' if auto else 'normal'};"
            "font-family:'JetBrains Mono','SF Mono','Consolas',monospace;"
        )

    def _legend_menu(self, name: str, pos) -> None:
        if name not in self._auto:
            return
        from PyQt6.QtWidgets import QMenu

        from core.quick_plot import keepable_key
        menu = QMenu(self)
        keep = menu.addAction("Keep as Parsing channel")
        keep.setEnabled(keepable_key(name))
        hide = menu.addAction("Hide this series")
        chosen = menu.exec(pos)
        if chosen is keep:
            self.keep_requested.emit(name)
        elif chosen is hide:
            self.remove_channel(name)

    # ── Quick Plot (CMP5) ──────────────────────────────────────────────────────

    def quick_update(self, data: dict[str, float]) -> None:
        """Plot values from a data-only line, adding series as they appear."""
        fresh = [n for n in data if n not in self._channels and n not in self._hidden]
        for name in fresh:
            if len(self._auto) >= MAX_AUTO_CHANNELS:
                break
            self.add_channel(name, auto=True)
        self.update(data)

    def set_quick_enabled(self, on: bool) -> None:
        """Off removes the automatic series (Parsing channels stay)."""
        self.quick_enabled = on
        if self._auto_btn.isChecked() != on:
            self._auto_btn.setChecked(on)
        if not on:
            for name in list(self._auto):
                self.remove_channel(name)
            self._hidden.clear()

    def remove_channel(self, name: str) -> None:
        ch = self._channels.pop(name, None)
        if not ch:
            return
        if name in self._auto:
            self._auto.discard(name)
            if self.quick_enabled:
                self._hidden.add(name)      # don't re-add on the next line
        ch["plot"].removeItem(ch["curve"])
        if not self._channels:
            self._empty_label.setVisible(True)
        self._channels_changed()
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
        if not self._dirty or self.paused or not self.isVisible() or self.view in VIEWS:
            return
        self._draw()
        t = time.monotonic() - self._t0
        if self.window_sec:
            start = max(0.0, t - self.window_sec)
        else:
            firsts = [ch["buf_t"][0] for ch in self._channels.values() if ch["buf_t"]]
            start = min(firsts, default=0.0)
        self._plot.setXRange(start, t + 0.5, padding=0)

    def _draw(self) -> None:
        self._dirty = False
        t = time.monotonic() - self._t0
        for ch in self._channels.values():
            if not ch.pop("dirty", False):
                continue
            if self.view == "normalised":
                self._draw_normalised(ch, t)
            else:
                ch.pop("range", None)
                ch["curve"].setData(list(ch["buf_t"]), list(ch["buf_v"]))

    def _draw_normalised(self, ch: dict, now: float) -> None:
        """Scale to what is in the time window, and remember the range so the readout
        can still give the real value."""
        ts = np.array(ch["buf_t"], dtype=float)
        vs = np.array(ch["buf_v"], dtype=float)
        visible = vs[ts >= now - self.window_sec] if self.window_sec else vs
        scaled, low, high = normalise(vs, visible)
        ch["range"] = (low, high)
        ch["curve"].setData(ts, scaled)

    # ── Views (CH1, CMP13) ─────────────────────────────────────────────────────

    def _plots(self) -> list:
        return [self._pi] + self._rows

    def set_view(self, view: str) -> None:
        if view not in dict(VIEW_LABELS):
            return
        self.view = view
        self._view_combo.blockSignals(True)
        self._view_combo.setCurrentIndex(self._view_combo.findData(view))
        self._view_combo.blockSignals(False)
        over_time = view in TIME_VIEWS
        self._ylock_btn.setEnabled(view in ("shared", "rows"))
        self._hide_crosshair()
        if over_time:
            self._analysis_timer.stop()
            self._views.setCurrentWidget(self._canvas)
            self._arrange()
            for ch in self._channels.values():
                ch["dirty"] = True
            self._draw()
            self._sync_y_range()
            return
        widget = self._analysis.get(view)
        if widget is None:
            widget = self._analysis[view] = VIEWS[view](self)
            self._views.addWidget(widget)
        widget.channels_changed(list(self._channels))
        self._views.setCurrentWidget(widget)
        self._analysis_timer.start()
        self._refresh_analysis()

    def _refresh_analysis(self) -> None:
        widget = self._analysis.get(self.view)
        if widget is not None and self.isVisible() and not self.paused:
            widget.refresh()

    def _channels_changed(self) -> None:
        self._arrange()
        for view in self._analysis.values():
            view.channels_changed(list(self._channels))

    def _arrange(self) -> None:
        """Put each curve on the right plot for the view: all on one, or a row each."""
        for ch in self._channels.values():
            if ch.get("plot") is not None:
                ch["plot"].removeItem(ch["curve"])
        self._canvas.clear()
        names = list(self._channels)
        separate = self.view == "rows" and len(names) > 1
        wanted = len(names) if separate else 1
        while len(self._rows) < wanted - 1:
            plot = pg.PlotItem()
            self._style_plot(plot)
            plot.setXLink(self._pi)
            self._rows.append(plot)
        plots = self._plots()[:wanted]
        for row, plot in enumerate(plots):
            self._canvas.addItem(plot, row=row, col=0)
            plot.showAxis("bottom", row == wanted - 1)
            left = plot.getAxis("left")
            left.setWidth(LEFT_AXIS_WIDTH if separate else None)
            if separate:
                ch = self._channels[names[row]]
                left.setLabel(names[row], **{"color": ch["color"], "font-size": "9px"})
            else:
                left.setLabel("")
                left.showLabel(False)
        for i, name in enumerate(names):
            ch = self._channels[name]
            ch["plot"] = plots[i] if separate else self._pi
            ch["plot"].addItem(ch["curve"])
        axis = self._pi.getAxis("left")
        axis.setTicks([[(0, tr("min")), (0.5, tr("mid")), (1, tr("max"))]]
                      if self.view == "normalised" else None)

    def _sync_y_range(self) -> None:
        if self.view == "normalised":
            self._pi.setYRange(-0.05, 1.05, padding=0)
        elif self._ylock_btn.isChecked():
            self.set_y_locked(True)
        else:
            self.set_y_locked(False)

    def samples(self, name: str) -> tuple[np.ndarray, np.ndarray]:
        """``(times, values)`` of *name* within the time window, for the other views."""
        ch = self._channels.get(name)
        if ch is None or not ch["buf_t"]:
            return np.array([]), np.array([])
        ts = np.array(ch["buf_t"], dtype=float)
        vs = np.array(ch["buf_v"], dtype=float)
        if self.window_sec:
            keep = ts >= ts[-1] - self.window_sec
            return ts[keep], vs[keep]
        return ts, vs

    def latest_values(self) -> dict[str, float]:
        return {n: ch["buf_v"][-1] for n, ch in self._channels.items() if ch["buf_v"]}

    def channel_color(self, name: str) -> str:
        ch = self._channels.get(name)
        return ch["color"] if ch else DARK["fg"]

    # ── Crosshair and readout (CH1) ────────────────────────────────────────────

    def values_at(self, t: float) -> list[tuple[str, str, float]]:
        """``(name, colour, real value)`` of each channel's sample nearest time *t*."""
        out = []
        for name, ch in self._channels.items():
            xs = ch["curve"].xData
            ys = ch["curve"].yData
            if xs is None or len(xs) == 0:
                continue
            i = int(np.searchsorted(xs, t))
            if i >= len(xs) or (i > 0 and t - xs[i - 1] < xs[i] - t):
                i -= 1
            value = float(ys[i])
            if "range" in ch:                # the curve holds 0–1, not the real value
                low, high = ch["range"]
                value = low if high == low else low + value * (high - low)
            out.append((name, ch["color"], value))
        return out

    def _on_mouse_moved(self, pos) -> None:
        if self.view not in TIME_VIEWS or not self._channels:
            return
        plot = next((p for p in self._plots()[:max(1, len(self._channels))]
                     if p.scene() is not None and p.vb.sceneBoundingRect().contains(pos)), None)
        if plot is None:
            self._hide_crosshair()
            return
        t = plot.vb.mapSceneToView(pos).x()
        self.show_readout(t, pos)

    def show_readout(self, t: float, pos=None) -> None:
        for plot in self._plots():
            line = self._cross.get(plot)
            if plot.scene() is None:
                if line is not None:
                    line.hide()
                continue
            if line is None:
                line = self._cross[plot] = pg.InfiniteLine(
                    angle=90, movable=False, pen=pg.mkPen(self.theme["fg_dim"], width=1))
                plot.addItem(line, ignoreBounds=True)
            line.setPos(t)
            line.show()
        rows = [f"<span style='color:{self.theme['fg_dim']}'>{t:.3f} s</span>"]
        rows += [f"<span style='color:{color}'>{name}</span> {value:.6g}"
                 for name, color, value in self.values_at(t)]
        self._readout.setText("<br>".join(rows))
        self._readout.adjustSize()
        if pos is not None:
            x, y = int(pos.x()) + 14, int(pos.y()) + 10
            if x + self._readout.width() > self._canvas.width():
                x = int(pos.x()) - self._readout.width() - 14
            if y + self._readout.height() > self._canvas.height():
                y = max(0, self._canvas.height() - self._readout.height())
            self._readout.move(x, y)
        self._readout.show()

    def _hide_crosshair(self) -> None:
        self._readout.hide()
        for line in self._cross.values():
            line.hide()

    def eventFilter(self, obj, event):       # noqa: N802  (Qt naming)
        if obj is self._canvas and event.type() == QEvent.Type.Leave:
            self._hide_crosshair()
        return super().eventFilter(obj, event)

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
            # Zooming while paused turned Y auto-scale off; going live turns it back on.
            if self.view in TIME_VIEWS:
                self._sync_y_range()
            self._mark_all_dirty()

    def set_y_locked(self, locked: bool) -> None:
        for plot in self._plots():
            if locked:
                lo, hi = plot.viewRange()[1]
                plot.setYRange(lo, hi, padding=0)   # also turns Y auto-range off
            else:
                plot.enableAutoRange(axis="y")

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
        exp = pyqtgraph.exporters.ImageExporter(self._canvas.ci)
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
