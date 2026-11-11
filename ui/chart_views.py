"""ui/chart_views.py — the other ways of looking at channels (CMP13): X against Y,
spectrum, histogram, bars, gauges and a compass. Each draws from the chart's own
buffers on a timer, and only while it is on screen."""
from __future__ import annotations

import math

import pyqtgraph as pg
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QScrollArea,
                             QSizePolicy, QVBoxLayout, QWidget)

from core.i18n import tr
from core.signal_math import align_xy, histogram, spectrum

_MONO = "'JetBrains Mono','SF Mono','Consolas',monospace"
#: Points drawn in the X-Y view: enough for a long track, cheap to redraw.
MAX_XY_POINTS = 5000
CHART_ACCENT = "#3ecf8e"


def _number(v: float) -> str:
    return f"{v:.5g}"


class AnalysisView(QWidget):
    """A view with a row of channel pickers above what it draws."""

    #: Pickers this view needs, as their labels.
    PICKERS: tuple[str, ...] = ()

    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self.panel = panel
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.bar = QHBoxLayout()
        self.bar.setContentsMargins(8, 4, 8, 4)
        self.bar.setSpacing(6)
        self.pickers: list[QComboBox] = []
        for label in self.PICKERS:
            lbl = QLabel(tr(label))
            lbl.setObjectName("dimLabel")
            self.bar.addWidget(lbl)
            combo = QComboBox()
            combo.setMinimumWidth(90)
            self.bar.addWidget(combo)
            self.pickers.append(combo)
        self.info = QLabel("")
        self.info.setObjectName("dimLabel")
        self.info.setTextFormat(Qt.TextFormat.PlainText)
        self.bar.addWidget(self.info, 1)
        root.addLayout(self.bar)
        self.root = root

    def channels_changed(self, names: list[str]) -> None:
        """Keep each picker's choice if the channel is still there. A new picker takes
        a channel the others don't, so X against Y doesn't start as X against X."""
        for i, combo in enumerate(self.pickers):
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(names)
            if current in names:
                combo.setCurrentText(current)
            elif names:
                combo.setCurrentIndex(min(i, len(names) - 1))
            combo.blockSignals(False)

    def refresh(self) -> None:
        raise NotImplementedError

    def apply_theme(self, c: dict) -> None:
        pass

    def picked(self, i: int = 0) -> str:
        return self.pickers[i].currentText()


class _PlotView(AnalysisView):
    def __init__(self, panel, parent=None):
        super().__init__(panel, parent)
        self.canvas = pg.GraphicsLayoutWidget()
        self.plot = self.canvas.addPlot(row=0, col=0)
        self.plot.showGrid(x=True, y=True, alpha=0.15)
        self.root.addWidget(self.canvas, 1)
        self.apply_theme(panel.theme)

    def apply_theme(self, c: dict) -> None:
        self.canvas.setBackground(c["bg"])
        for axis in ("bottom", "left"):
            self.plot.getAxis(axis).setPen(pg.mkPen(c["bg4"]))
            self.plot.getAxis(axis).setTextPen(pg.mkPen(c["fg_dim"]))


class XYView(_PlotView):
    PICKERS = ("X", "Y")

    def __init__(self, panel, parent=None):
        super().__init__(panel, parent)
        self.equal = QCheckBox(tr("Same scale"))
        self.equal.setToolTip(tr("Use the same scale on both axes, for a track such as "
                                 "longitude against latitude"))
        self.equal.toggled.connect(self._on_equal)
        self.bar.insertWidget(self.bar.count() - 1, self.equal)
        self.trail = self.plot.plot([], [], pen=pg.mkPen(CHART_ACCENT, width=1.2))
        self.head = pg.ScatterPlotItem(size=9, brush=pg.mkBrush(CHART_ACCENT), pen=None)
        self.plot.addItem(self.head)

    def _on_equal(self, on: bool) -> None:
        self.plot.setAspectLocked(on)
        self.plot.enableAutoRange()

    def refresh(self) -> None:
        tx, vx = self.panel.samples(self.picked(0))
        ty, vy = self.panel.samples(self.picked(1))
        x, y = align_xy(tx, vx, ty, vy)
        x, y = x[-MAX_XY_POINTS:], y[-MAX_XY_POINTS:]
        self.trail.setData(x, y)
        self.head.setData(x[-1:], y[-1:])
        self.info.setText(f"{self.picked(0)}={_number(x[-1])}  {self.picked(1)}={_number(y[-1])}"
                          f"  ·  {len(x):,} {tr('points')}" if len(x) else "")


class SpectrumView(_PlotView):
    PICKERS = ("Channel",)

    def __init__(self, panel, parent=None):
        super().__init__(panel, parent)
        self.curve = self.plot.plot([], [], pen=pg.mkPen(CHART_ACCENT, width=1.2))
        self.plot.getAxis("bottom").setLabel(tr("Frequency"), units="Hz")
        self.plot.getAxis("left").setLabel(tr("Amplitude"))
        self.info.setToolTip(tr("Assumes the device sends at a steady rate: the sample rate is "
                                "the average over these samples, not each sample's own time."))

    def refresh(self) -> None:
        t, v = self.panel.samples(self.picked())
        result = spectrum(t, v)
        if result is None:
            self.curve.setData([], [])
            self.info.setText(tr("Needs more samples") if self.picked() else "")
            return
        self.curve.setData(result.freq, result.amplitude)
        self.info.setText(tr("Peak {f} Hz, amplitude {a}  ·  about {r} samples/s, {n} samples").format(
            f=_number(result.peak_freq), a=_number(result.peak_amplitude),
            r=_number(result.sample_rate), n=f"{min(len(v), len(result.freq) * 2):,}"))


class HistogramView(_PlotView):
    PICKERS = ("Channel",)

    def __init__(self, panel, parent=None):
        super().__init__(panel, parent)
        self.bars = pg.BarGraphItem(x0=[], x1=[], height=[], brush=CHART_ACCENT, pen=None)
        self.plot.addItem(self.bars)
        self.plot.getAxis("left").setLabel(tr("Samples"))

    def refresh(self) -> None:
        _t, v = self.panel.samples(self.picked())
        result = histogram(v)
        if result is None:
            self.bars.setOpts(x0=[], x1=[], height=[])
            self.info.setText("")
            return
        self.bars.setOpts(x0=result.edges[:-1], x1=result.edges[1:], height=result.counts)
        self.info.setText(tr("Mean {m}, spread {s}, from {lo} to {hi}  ·  {n} samples").format(
            m=_number(result.mean), s=_number(result.std), lo=_number(result.low),
            hi=_number(result.high), n=f"{len(v):,}"))


class BarsView(_PlotView):
    """Every channel's latest value, side by side."""

    def __init__(self, panel, parent=None):
        super().__init__(panel, parent)
        self.bars = pg.BarGraphItem(x=[], height=[], width=0.7, brushes=[], pen=None)
        self.plot.addItem(self.bars)
        self.plot.showGrid(x=False, y=True, alpha=0.15)
        self.plot.getAxis("bottom").setHeight(44)     # room for the name and the value

    def refresh(self) -> None:
        latest = self.panel.latest_values()
        names = list(latest)
        self.bars.setOpts(x=list(range(len(names))), height=[latest[n] for n in names],
                          brushes=[self.panel.channel_color(n) for n in names])
        self.plot.getAxis("bottom").setTicks(
            [[(i, f"{n}\n{_number(latest[n])}") for i, n in enumerate(names)]])


class _Dial(QWidget):
    """A round dial drawn by hand: pyqtgraph has none, and it stays cheap."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(120, 120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.colors = {"bg4": "#3e3e42", "fg": "#d4d4d4", "fg_dim": "#6a6a7a"}

    def _square(self) -> QRectF:
        side = min(self.width(), self.height()) - 12
        return QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)


class Gauge(_Dial):
    SWEEP = 270.0                    # degrees, open at the bottom

    def __init__(self, name: str, color: str, parent=None):
        super().__init__(parent)
        self.name = name
        self.color = color
        self.value = 0.0
        self.low = 0.0
        self.high = 1.0

    def set_reading(self, value: float, low: float, high: float) -> None:
        self.value, self.low, self.high = value, low, high
        self.update()

    @property
    def fraction(self) -> float:
        if self.high <= self.low:
            return 0.5
        return max(0.0, min(1.0, (self.value - self.low) / (self.high - self.low)))

    def paintEvent(self, _event) -> None:   # noqa: N802  (Qt naming)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self._square()
        thick = max(6.0, rect.width() * 0.09)
        rect.adjust(thick / 2, thick / 2, -thick / 2, -thick / 2)
        start = int((225) * 16)              # Qt angles: counter-clockwise from 3 o'clock
        pen = QPen(QColor(self.colors["bg4"]), thick, cap=Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(rect, start, int(-self.SWEEP * 16))
        pen.setColor(QColor(self.color))
        p.setPen(pen)
        p.drawArc(rect, start, int(-self.SWEEP * self.fraction * 16))
        p.setPen(QColor(self.colors["fg"]))
        font = QFont("JetBrains Mono")
        font.setPixelSize(max(11, int(rect.width() * 0.17)))
        p.setFont(font)
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, _number(self.value))
        font.setPixelSize(max(9, int(rect.width() * 0.09)))
        p.setFont(font)
        p.setPen(QColor(self.colors["fg_dim"]))
        lower = QRectF(rect.left(), rect.center().y() + rect.height() * 0.13, rect.width(),
                       rect.height() * 0.2)
        p.drawText(lower, Qt.AlignmentFlag.AlignCenter, self.name)
        bottom = QRectF(rect.left(), rect.bottom() - rect.height() * 0.14, rect.width(),
                        rect.height() * 0.2)
        p.drawText(bottom, Qt.AlignmentFlag.AlignCenter,
                   f"{_number(self.low)} … {_number(self.high)}")
        p.end()


class GaugesView(AnalysisView):
    """A gauge per channel. Its range is what the channel has done in the time window,
    so it needs no setting up, and it shows where the value sits in that range."""

    COLUMNS = 3

    def __init__(self, panel, parent=None):
        super().__init__(panel, parent)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.holder = QWidget()
        self.grid = QGridLayout(self.holder)
        self.grid.setContentsMargins(6, 6, 6, 6)
        self.grid.setSpacing(6)
        scroll.setWidget(self.holder)
        self.root.addWidget(scroll, 1)
        self.gauges: dict[str, Gauge] = {}
        self._colors = panel.theme

    def channels_changed(self, names: list[str]) -> None:
        for name in list(self.gauges):
            if name not in names:
                gauge = self.gauges.pop(name)
                self.grid.removeWidget(gauge)
                gauge.setParent(None)
                gauge.deleteLater()
        for name in names:
            if name not in self.gauges:
                self.gauges[name] = Gauge(name, self.panel.channel_color(name))
                self.gauges[name].colors = self._colors
        for i, name in enumerate(names):
            self.grid.addWidget(self.gauges[name], i // self.COLUMNS, i % self.COLUMNS)

    def apply_theme(self, c: dict) -> None:
        self._colors = c
        for gauge in self.gauges.values():
            gauge.colors = c
            gauge.update()

    def refresh(self) -> None:
        for name, gauge in self.gauges.items():
            _t, v = self.panel.samples(name)
            if len(v):
                gauge.set_reading(float(v[-1]), float(v.min()), float(v.max()))


class CompassDial(_Dial):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.heading = 0.0
        self.color = CHART_ACCENT

    def set_heading(self, degrees: float) -> None:
        self.heading = degrees % 360
        self.update()

    def paintEvent(self, _event) -> None:   # noqa: N802  (Qt naming)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self._square()
        centre = rect.center()
        radius = rect.width() / 2
        p.setPen(QPen(QColor(self.colors["bg4"]), 2))
        p.drawEllipse(centre, radius - 1, radius - 1)
        for mark in range(0, 360, 30):
            angle = math.radians(mark)
            inner = radius * (0.86 if mark % 90 else 0.8)
            p.drawLine(QPointF(centre.x() + math.sin(angle) * inner,
                               centre.y() - math.cos(angle) * inner),
                       QPointF(centre.x() + math.sin(angle) * (radius - 2),
                               centre.y() - math.cos(angle) * (radius - 2)))
        font = QFont("JetBrains Mono")
        font.setPixelSize(max(10, int(radius * 0.14)))
        p.setFont(font)
        p.setPen(QColor(self.colors["fg_dim"]))
        for text, mark in (("N", 0), ("E", 90), ("S", 180), ("W", 270)):
            angle = math.radians(mark)
            box = QRectF(0, 0, radius * 0.3, radius * 0.3)
            box.moveCenter(QPointF(centre.x() + math.sin(angle) * radius * 0.65,
                                   centre.y() - math.cos(angle) * radius * 0.65))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        angle = math.radians(self.heading)
        p.setPen(QPen(QColor(self.color), max(3.0, radius * 0.03),
                      cap=Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(centre.x() - math.sin(angle) * radius * 0.18,
                           centre.y() + math.cos(angle) * radius * 0.18),
                   QPointF(centre.x() + math.sin(angle) * radius * 0.5,
                           centre.y() - math.cos(angle) * radius * 0.5))
        p.end()


class CompassView(AnalysisView):
    PICKERS = ("Heading",)

    def __init__(self, panel, parent=None):
        super().__init__(panel, parent)
        self.dial = CompassDial()
        self.dial.colors = panel.theme
        self.root.addWidget(self.dial, 1)

    def apply_theme(self, c: dict) -> None:
        self.dial.colors = c
        self.dial.update()

    def refresh(self) -> None:
        _t, v = self.panel.samples(self.picked())
        if len(v):
            self.dial.set_heading(float(v[-1]))
            self.info.setText(tr("{deg}°  ·  degrees, clockwise from north").format(
                deg=f"{self.dial.heading:.1f}"))


VIEWS = {"xy": XYView, "spectrum": SpectrumView, "histogram": HistogramView, "bars": BarsView,
         "gauges": GaugesView, "compass": CompassView}
