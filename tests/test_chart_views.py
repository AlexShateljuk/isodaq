"""The chart's views (CH1, CMP13) on the real ChartPanel."""
import math
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")
pytest.importorskip("pyqtgraph")

import numpy as np  # noqa: E402
from PyQt6.QtCore import QPointF  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    app.setApplicationVersion("0.0.0-test")
    yield app


def pump(qapp, sec=0.05):
    end = time.monotonic() + sec
    while time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.002)


@pytest.fixture
def chart(qapp):
    from ui.chart_panel import ChartPanel
    c = ChartPanel()
    c.resize(900, 600)
    c.show()
    yield c
    c.close()


def feed(chart, n=200, names=("rpm", "temp")):
    """Values of very different sizes: the case CH1 exists for."""
    for i in range(n):
        data = {}
        if "rpm" in names:
            data["rpm"] = 2000.0 + 500 * math.sin(i / 10)
        if "temp" in names:
            data["temp"] = 20.0 + math.sin(i / 7)
        chart.quick_update(data)


# ── Over time ─────────────────────────────────────────────────────────────────

def test_shared_is_the_default_and_uses_one_plot(chart, qapp):
    feed(chart)
    pump(qapp)
    assert chart.view == "shared"
    assert all(ch["plot"] is chart._pi for ch in chart._channels.values())
    assert chart._canvas.ci.layout.count() == 1


def test_separate_rows_give_each_channel_its_own_y_range(chart, qapp):
    feed(chart)
    chart.set_view("rows")
    pump(qapp, 0.2)
    rpm, temp = chart._channels["rpm"], chart._channels["temp"]
    assert rpm["plot"] is not temp["plot"]
    assert chart._canvas.ci.layout.count() == 2
    rpm_range = rpm["plot"].viewRange()[1]
    temp_range = temp["plot"].viewRange()[1]
    assert rpm_range[0] > 1000 and temp_range[1] < 30
    assert len(temp["curve"].yData) == 200


def test_rows_share_the_time_axis_and_only_the_last_shows_it(chart, qapp):
    feed(chart)
    chart.set_view("rows")
    pump(qapp)
    first, second = chart._channels["rpm"]["plot"], chart._channels["temp"]["plot"]
    first.setXRange(1.0, 2.0, padding=0)
    pump(qapp)
    assert second.viewRange()[0] == pytest.approx([1.0, 2.0])
    assert not first.getAxis("bottom").isVisible() and second.getAxis("bottom").isVisible()


def test_rows_follow_channels_added_and_removed(chart, qapp):
    feed(chart)
    chart.set_view("rows")
    chart.quick_update({"third": 1.0})
    assert chart._canvas.ci.layout.count() == 3
    chart.remove_channel("rpm")
    assert chart._canvas.ci.layout.count() == 2
    chart.remove_channel("temp")
    chart.remove_channel("third")
    assert chart._canvas.ci.layout.count() == 1
    chart.quick_update({"new": 1.0})         # back from nothing
    assert chart._channels["new"]["plot"] is chart._pi


def test_one_channel_in_rows_is_a_plain_plot(chart, qapp):
    feed(chart, names=("temp",))
    chart.set_view("rows")
    assert chart._canvas.ci.layout.count() == 1


def test_going_back_to_shared_restores_everything(chart, qapp):
    feed(chart)
    chart.set_view("rows")
    chart.set_view("shared")
    pump(qapp, 0.2)
    assert chart._canvas.ci.layout.count() == 1
    assert all(ch["plot"] is chart._pi for ch in chart._channels.values())
    assert all(ch["curve"] in chart._pi.items for ch in chart._channels.values())
    assert chart._pi.viewRange()[1][1] > 2000    # auto-scale again


def test_normalised_overlays_at_0_to_1_and_going_back_gives_real_values(chart, qapp):
    feed(chart)
    chart.set_view("normalised")
    pump(qapp, 0.1)
    for ch in chart._channels.values():
        ys = ch["curve"].yData
        assert ys.min() == pytest.approx(0.0, abs=1e-9) and ys.max() == pytest.approx(1.0, abs=1e-9)
    assert chart._pi.viewRange()[1] == pytest.approx([-0.05, 1.05])
    assert not chart._ylock_btn.isEnabled()
    chart.set_view("shared")
    assert chart._channels["rpm"]["curve"].yData.max() > 2000
    assert "range" not in chart._channels["rpm"]
    assert chart._ylock_btn.isEnabled()


def test_normalised_handles_a_flat_channel(chart, qapp):
    for _ in range(10):
        chart.quick_update({"flat": 5.0})
    chart.set_view("normalised")
    assert set(chart._channels["flat"]["curve"].yData) == {0.5}
    assert chart.values_at(0.0)[0][2] == 5.0


def test_paused_view_still_redraws_for_a_new_view(chart, qapp):
    feed(chart)
    chart.set_paused(True)
    chart.set_view("normalised")
    assert chart._channels["rpm"]["curve"].yData.max() == pytest.approx(1.0)
    chart.set_view("rows")
    assert chart._channels["rpm"]["curve"].yData.max() > 2000


def test_new_samples_reach_the_rows(chart, qapp):
    feed(chart)
    chart.set_view("rows")
    pump(qapp, 0.1)
    chart.quick_update({"rpm": 9999.0, "temp": 1.0})
    pump(qapp, 0.1)
    assert chart._channels["rpm"]["curve"].yData[-1] == 9999.0


def test_y_lock_applies_to_every_row(chart, qapp):
    feed(chart)
    chart.set_view("rows")
    pump(qapp, 0.1)
    chart._ylock_btn.setChecked(True)
    plots = [ch["plot"] for ch in chart._channels.values()]
    before = [p.viewRange()[1] for p in plots]
    chart.quick_update({"rpm": 1e6, "temp": 1e6})
    pump(qapp, 0.1)
    assert [p.viewRange()[1] for p in plots] == before
    chart._ylock_btn.setChecked(False)
    pump(qapp, 0.1)
    assert plots[0].viewRange()[1][1] > 1e5


def test_going_live_turns_y_auto_scale_back_on_after_zooming(chart, qapp):
    feed(chart)
    pump(qapp, 0.1)
    chart.set_paused(True)
    chart._pi.setYRange(0, 1, padding=0)        # as zooming does
    chart.set_paused(False)
    pump(qapp, 0.1)
    assert chart._pi.viewRange()[1][1] > 2000


def test_png_export_of_rows(chart, qapp, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QFileDialog
    feed(chart)
    chart.set_view("rows")
    pump(qapp, 0.1)
    out = tmp_path / "chart.png"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *_a, **_k: (str(out), "")))
    chart._export_png()
    assert out.stat().st_size > 1000


def test_theme_reaches_rows_and_views(chart, qapp):
    c = {"bg": "#ffffff", "bg4": "#cccccc", "fg": "#111111", "fg_dim": "#555555"}
    feed(chart)
    chart.set_view("rows")
    chart.set_view("gauges")
    chart.apply_theme(c)
    assert chart.theme["bg"] == "#ffffff"
    assert chart._analysis["gauges"].gauges["rpm"].colors["fg"] == "#111111"
    assert "#ffffff" in chart._readout.styleSheet()


# ── Readout ───────────────────────────────────────────────────────────────────

def _known(chart):
    for ch, ts, vs in (("a", [1.0, 2.0, 3.0], [10.0, 20.0, 30.0]), ("b", [1.0, 3.0], [100.0, 300.0])):
        chart.add_channel(ch)
        chart._channels[ch]["buf_t"].extend(ts)
        chart._channels[ch]["buf_v"].extend(vs)
        chart._channels[ch]["dirty"] = True
    chart._draw()


@pytest.mark.parametrize("t, a, b", [
    (0.0, 10.0, 100.0), (1.4, 10.0, 100.0), (1.6, 20.0, 100.0), (2.1, 20.0, 300.0), (99.0, 30.0, 300.0),
])
def test_readout_gives_the_nearest_sample(chart, t, a, b):
    _known(chart)
    assert {n: v for n, _c, v in chart.values_at(t)} == {"a": a, "b": b}


def test_readout_gives_real_values_when_normalised(chart):
    _known(chart)
    chart.set_view("normalised")
    assert {n: v for n, _c, v in chart.values_at(3.0)} == {"a": 30.0, "b": 300.0}


def test_readout_skips_a_channel_with_no_data(chart):
    chart.add_channel("empty")
    assert chart.values_at(1.0) == []


def test_hovering_shows_a_crosshair_and_leaving_hides_it(chart, qapp):
    from PyQt6.QtCore import QEvent
    _known(chart)
    chart._pi.setXRange(0, 4, padding=0)
    pump(qapp, 0.1)
    centre = chart._pi.vb.sceneBoundingRect().center()
    chart._on_mouse_moved(centre)
    assert chart._readout.isVisible()
    assert "<br>" in chart._readout.text() and "a</span>" in chart._readout.text()
    line = chart._cross[chart._pi]
    assert line.isVisible() and line.value() == pytest.approx(chart._pi.vb.mapSceneToView(centre).x())
    assert chart._canvas.rect().contains(chart._readout.geometry().topLeft())
    chart.eventFilter(chart._canvas, QEvent(QEvent.Type.Leave))
    assert not chart._readout.isVisible() and not line.isVisible()


def test_hovering_outside_a_plot_hides_it(chart, qapp):
    _known(chart)
    pump(qapp, 0.05)
    chart._on_mouse_moved(chart._pi.vb.sceneBoundingRect().center())
    chart._on_mouse_moved(QPointF(-50, -50))
    assert not chart._readout.isVisible()


def test_crosshair_shows_on_every_row(chart, qapp):
    _known(chart)
    chart.set_view("rows")
    pump(qapp, 0.1)
    chart.show_readout(2.0)
    lines = [chart._cross[ch["plot"]] for ch in chart._channels.values()]
    assert len(set(lines)) == 2 and all(line.value() == 2.0 for line in lines)


# ── Other views ───────────────────────────────────────────────────────────────

def analysis(chart, view, qapp):
    chart.set_view(view)
    pump(qapp, 0.02)
    widget = chart._analysis[view]
    widget.refresh()
    chart.grab()                             # runs the paint code
    return widget


def test_xy_pairs_two_channels_and_picks_different_ones(chart, qapp):
    for i in range(100):
        chart.quick_update({"lon": float(i), "lat": i * 2.0})
    view = analysis(chart, "xy", qapp)
    assert (view.picked(0), view.picked(1)) == ("lon", "lat")
    assert view.trail.yData[-1] == 198.0 and view.trail.xData[-1] == 99.0
    assert "100 points" in view.info.text()
    view.equal.setChecked(True)
    assert view.plot.vb.state["aspectLocked"]
    chart.grab()


def test_xy_with_one_channel_and_none(chart, qapp):
    view = analysis(chart, "xy", qapp)        # no channels at all
    assert view.info.text() == ""
    chart.quick_update({"only": 1.0})
    view.refresh()
    assert view.picked(0) == view.picked(1) == "only"


def test_spectrum_view_reports_the_peak(chart, qapp):
    c = chart._channels
    chart.add_channel("sig")
    t = np.arange(2000) / 1000.0
    c["sig"]["buf_t"].extend(t + time.monotonic() - chart._t0 - 2)
    c["sig"]["buf_v"].extend(np.sin(2 * np.pi * 50 * t))
    view = analysis(chart, "spectrum", qapp)
    assert "Peak 50" in view.info.text()
    assert view.curve.xData[np.argmax(view.curve.yData)] == pytest.approx(50.0, abs=1)


def test_spectrum_says_when_it_needs_more(chart, qapp):
    chart.quick_update({"x": 1.0})
    view = analysis(chart, "spectrum", qapp)
    assert "Needs more" in view.info.text()


def test_histogram_view(chart, qapp):
    feed(chart, 500, names=("temp",))
    view = analysis(chart, "histogram", qapp)
    assert view.bars.opts["height"].sum() == 500
    assert "500 samples" in view.info.text()


def test_views_only_use_the_time_window(chart, qapp):
    chart.add_channel("x")
    ch = chart._channels["x"]
    ch["buf_t"].extend([0.0, 100.0, 101.0])
    ch["buf_v"].extend([1.0, 2.0, 3.0])
    chart.set_window(10)
    t, v = chart.samples("x")
    assert list(v) == [2.0, 3.0]
    chart.set_window(0)
    assert list(chart.samples("x")[1]) == [1.0, 2.0, 3.0]


def test_bars_show_the_latest_values(chart, qapp):
    feed(chart, 5)
    chart.quick_update({"rpm": 7.0, "temp": 8.0})
    view = analysis(chart, "bars", qapp)
    assert list(view.bars.opts["height"]) == [7.0, 8.0]


def test_gauges_follow_channels_and_range(chart, qapp):
    feed(chart, 50)
    view = analysis(chart, "gauges", qapp)
    assert set(view.gauges) == {"rpm", "temp"}
    gauge = view.gauges["temp"]
    assert gauge.low < gauge.value <= gauge.high or gauge.value == gauge.low
    assert 0.0 <= gauge.fraction <= 1.0
    chart.remove_channel("rpm")
    assert set(view.gauges) == {"temp"}


def test_gauge_with_no_range_sits_in_the_middle(qapp):
    from ui.chart_views import Gauge
    g = Gauge("x", "#ffffff")
    g.set_reading(5.0, 5.0, 5.0)
    assert g.fraction == 0.5
    g.set_reading(50.0, 0.0, 10.0)
    assert g.fraction == 1.0


@pytest.mark.parametrize("value, shown", [(90.0, 90.0), (370.0, 10.0), (-10.0, 350.0)])
def test_compass_wraps(chart, qapp, value, shown):
    chart.quick_update({"heading": value})
    view = analysis(chart, "compass", qapp)
    assert view.dial.heading == shown
    assert f"{shown:.1f}" in view.info.text()


def test_views_stay_idle_when_hidden_or_paused(chart, qapp):
    feed(chart)
    view = analysis(chart, "bars", qapp)
    calls = []
    view.refresh = lambda: calls.append(1)
    chart.set_paused(True)
    chart._refresh_analysis()
    chart.set_paused(False)
    chart.hide()
    chart._refresh_analysis()
    assert calls == []
    chart.show()
    chart._refresh_analysis()
    assert calls == [1]


def test_views_stop_their_timer_going_back(chart, qapp):
    chart.set_view("bars")
    assert chart._analysis_timer.isActive()
    chart.set_view("shared")
    assert not chart._analysis_timer.isActive()
    assert chart._views.currentWidget() is chart._canvas


def test_unknown_view_is_ignored(chart):
    chart.set_view("nonsense")
    assert chart.view == "shared"


def test_the_combo_drives_the_view(chart):
    chart._view_combo.setCurrentIndex(chart._view_combo.findData("rows"))
    assert chart.view == "rows"
    chart.set_view("gauges")
    assert chart._view_combo.currentData() == "gauges"


def test_view_survives_a_restart(qapp, tmp_path, monkeypatch):
    from ui.controllers.update_manager import UpdateManager
    from ui.main_window import MainWindow
    monkeypatch.setattr(MainWindow, "_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(UpdateManager, "start", lambda self: None)
    w = MainWindow()
    w._active_conn.chart.set_view("rows")
    w._settings.save()
    w.close()
    again = MainWindow()
    assert again._active_conn.chart.view == "rows"
    again.close()
