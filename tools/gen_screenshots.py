#!/usr/bin/env python3
"""tools/gen_screenshots.py — regenerate the documentation screenshots.

Builds the *real* MainWindow, feeds it representative synthetic serial data
(so charts have curves, indicators have values and the trigger tables have
rows), then saves ``widget.grab()`` PNGs into ``docs/images/``. These images
are the ones referenced by ``README.md`` and the wiki pages.

Run it on a machine with a display (the app is a Qt GUI):

    python tools/gen_screenshots.py

Notes
-----
* Uses a throwaway config path, so it never touches
  ``~/.isodaq_studio/config.json``.
* The chart / analytics curves are filled with a synthetic time axis because
  ``ChartPanel`` timestamps points with the wall clock — feeding a whole
  session instantly would collapse every point onto t≈0.
* Nothing here talks to a serial port or the network; data is injected straight
  into the GUI slots the serial reader would normally drive.
"""
from __future__ import annotations

import math
import random
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Redirect config persistence to a temp file *before* MainWindow is imported/built
import ui.main_window as mw_mod  # noqa: E402
mw_mod.MainWindow._CONFIG_PATH = Path(tempfile.gettempdir()) / "isodaq_shots_config.json"

from PyQt6.QtGui import QIcon  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core import i18n  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402

OUT = ROOT / "docs" / "images"
ICON = ROOT / "ui" / "resources" / "icon.png"

# Channels shown in the demo — kept in a similar numeric range so all three
# curves stay visible on the shared Y axis.
CHANNELS = ["temp", "hum", "load"]


def _sample(t: float) -> tuple[float, float, float]:
    """Synthetic telemetry sample at elapsed second *t*."""
    temp = 46 + 9 * math.sin(2 * math.pi * t / 22) + random.uniform(-0.4, 0.4)
    hum = 58 + 16 * math.sin(2 * math.pi * t / 30 + 1.4) + random.uniform(-0.6, 0.6)
    load = 55 + 20 * math.sin(2 * math.pi * t / 13 + 3.0) + random.uniform(-1.3, 1.3)
    return temp, hum, load


def pump(app: QApplication, ms: int = 350) -> None:
    """Spin the event loop for ~*ms* so queued slots run and pyqtgraph repaints."""
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.008)


def grab(widget, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    pm = widget.grab()
    pm.save(str(OUT / name))
    print(f"  ✓ {name}  ({pm.width()}×{pm.height()})")


# ── Data setup ────────────────────────────────────────────────────────────────

def setup_channels(win: MainWindow) -> None:
    from core.data_parser import ChannelConfig
    for name in CHANNELS:
        win._parser.add_channel(
            ChannelConfig(name=name, key=name, prefix="DATA:",
                          show_chart=True, show_indicator=True))
    win._parse_panel._rebuild()
    # Emits chart/indicator requests → registers curves, indicator cards and
    # the per-channel columns in the Events table.
    win._parse_panel.sync_display_panels()


def feed_terminal(win: MainWindow, app: QApplication) -> None:
    """Feed a readable slice of a session into the terminal + trigger tables."""
    win._log_colorizer_enabled = {"ESP32 (ESP-IDF)"}

    lines = [
        "I (0210) boot: IsoDAQ target online, fw 1.4.2",
        "I (0244) wifi: connected  rssi=-52  ip=192.168.1.42",
        "DATA: temp=45.9 hum=59.4 load=54.1",
        "DATA: temp=47.1 hum=60.2 load=57.8",
        "W (0602) sensor: calibration drift 1.8% (recalibrating)",
        "DATA: temp=48.6 hum=61.0 load=61.2",
        "DATA: temp=50.2 hum=62.3 load=63.9",
        "DATA: temp=51.8 hum=61.7 load=59.5",
        "E (0951) power: Brownout Detector asserted, vdd=2.71V",
        "DATA: temp=52.4 hum=60.9 load=58.0",
        "DATA: temp=53.1 hum=60.1 load=62.7",
        "HardFault_Handler: pc=0x08004a1c lr=0x0800391d psr=0x61000000",
        "I (1120) sys: recovered, resuming acquisition",
        "DATA: temp=52.0 hum=59.6 load=64.8",
        "DATA: temp=50.9 hum=58.8 load=66.1",
        "DATA: temp=49.4 hum=58.0 load=63.2",
    ]
    base_ms = 0
    for i, line in enumerate(lines):
        base_ms += random.randint(120, 480)
        sec, ms = 32 + base_ms // 1000, base_ms % 1000
        ts = f"14:{sec // 60 % 60 + 30:02d}:{sec % 60:02d}.{ms:03d}"
        win._on_line_received(line, ts)
        if i % 4 == 0:
            pump(app, 40)
    pump(app, 300)   # let queued trigger-match events land in the table


def fill_chart(win: MainWindow) -> None:
    """Overwrite chart buffers with a smooth synthetic 60 s window."""
    random.seed(3)
    cp = win._chart_panel
    n = 300
    xs = [i * (60.0 / n) for i in range(n)]
    samples = [_sample(x) for x in xs]
    for idx, name in enumerate(CHANNELS):
        ch = cp._channels.get(name)
        if ch is None:
            continue
        ys = [s[idx] for s in samples]
        ch["buf_t"].clear(); ch["buf_v"].clear()
        ch["buf_t"].extend(xs); ch["buf_v"].extend(ys)
        ch["curve"].setData(xs, ys)
    cp._plot.setXRange(0, 60, padding=0)
    cp._plot.enableAutoRange(axis="y")


def fill_analytics(win: MainWindow) -> None:
    ap = win._analytics_panel
    ap._t0 = time.monotonic() - 60.0
    hits = {"Brownout": [7.5, 14.2, 33.1, 41.8, 52.0], "HardFault": [22.4, 47.6]}
    for name, times in hits.items():
        s = ap._series.get(name)
        if s is None:
            continue
        s["hits_t"].clear(); s["hits_t"].extend(times); s["count"] = len(times)
        ap._redraw(name, s)
    ap._plot.setXRange(0, 60, padding=0)
    ap._plot.enableAutoRange(axis="y")
    # Make the sidebar hit counters agree with the analytics staircase.
    for t in win._engine.get_triggers():
        t.hit_count = len(hits.get(t.name, []))
    win._trigger_panel.refresh_hits()


def set_connected(win: MainWindow) -> None:
    win._port_combo.insertItem(0, "/dev/tty.usbmodem1101")
    win._port_combo.setCurrentIndex(0)
    win._conn_btn.setText("Disconnect")
    win._sb_conn.setText("● /dev/tty.usbmodem1101 · 115200")
    win._sb_conn.setStyleSheet("color:#22c55e")
    win._sb_sess.setText("Session: 00:03:12")
    win._sb_rate.setText("Rate: 21 lines/s")
    win._update_banner.hide()


# ── Close-up helpers ──────────────────────────────────────────────────────────

def expand(win: MainWindow, key: str) -> None:
    sec = win._sidebar_sections.get(key)
    if sec is not None and sec.collapsed:
        sec.toggle()


def shot_tabs(win: MainWindow, app: QApplication) -> None:
    for panel, fname in ((win._chart_panel, "tab-graphs.png"),
                         (win._indicator_panel, "tab-indicators.png"),
                         (win._trigger_events_panel, "tab-events.png"),
                         (win._analytics_panel, "tab-analytics.png")):
        win._tabs.setCurrentWidget(panel)
        pump(app, 200)
        grab(panel, fname)


def shot_sidebar(win: MainWindow, app: QApplication) -> None:
    grab(win._sidebar_sections["macros"], "panel-macros.png")
    grab(win._sidebar_sections["logger"], "panel-logger.png")

    # Parsing — expand + open the channel editor with a worked example.
    expand(win, "parsing")
    pp = win._parse_panel
    pp.open_editor(default_prefix="DATA:")
    pp._key_e.setText("temp"); pp._name_e.setText("temp")
    pp._prefix_e.setText("DATA:"); pp._unit_e.setText("")
    pp._test_e.setText("DATA: temp=52.4 hum=60.9 load=58.0")
    pp._chk_chart.setChecked(True); pp._chk_indicator.setChecked(True)
    pp._run_test()
    pump(app, 150)
    grab(win._sidebar_sections["parsing"], "panel-parsing.png")

    # Triggers — open the rule editor.
    tp = win._trigger_panel
    tp._open_editor()
    tp._pat.setText("Brownout Detector")
    tp._name_e.setText("Brownout")
    tp._type_c.setCurrentText("contains")
    tp._on_type("contains")
    pump(app, 150)
    grab(win._sidebar_sections["triggers"], "panel-triggers.png")


def shot_dialogs(win: MainWindow, app: QApplication) -> None:
    from ui.log_colorizer_dialog import LogColorizerDialog
    from ui.macro_panel import MacroEditorDialog
    from ui.indicator_panel import _ThresholdDialog
    from core.macros import Macro, MacroStep

    dlg = LogColorizerDialog({"ESP32 (ESP-IDF)", "Zephyr RTOS"}, win)
    dlg.setStyleSheet(app.styleSheet()); dlg.show(); pump(app, 250)
    grab(dlg, "dialog-log-colorizer.png"); dlg.close()

    macro = Macro("Start→Status", steps=[
        MacroStep("AT+START", delay_ms=300, wait_for="OK", wait_timeout_ms=2000),
        MacroStep("AT+STATUS?", delay_ms=0),
    ])
    md = MacroEditorDialog(macro, win)
    md.setStyleSheet(app.styleSheet()); md.show(); pump(app, 250)
    grab(md, "dialog-macro-editor.png"); md.close()

    th = _ThresholdDialog("temp", [(50.0, "#f59e0b"), (60.0, "#ef4444")], win)
    th.setStyleSheet(app.styleSheet()); th.show(); pump(app, 250)
    grab(th, "dialog-thresholds.png"); th.close()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("IsoDAQ Studio")
    app.setApplicationVersion("0.2.2")
    if ICON.exists():
        app.setWindowIcon(QIcon(str(ICON)))
    i18n.init("en")

    win = MainWindow()
    win.resize(1300, 800)
    win.show()
    pump(app, 500)

    setup_channels(win)
    feed_terminal(win, app)
    fill_chart(win)
    fill_analytics(win)
    set_connected(win)

    win._tabs.setCurrentWidget(win._chart_panel)
    pump(app, 400)
    print("Dark theme:")
    grab(win, "main-window-dark.png")

    shot_tabs(win, app)
    shot_sidebar(win, app)
    shot_dialogs(win, app)

    # Light theme — re-grab the hero on the Graphs tab.
    print("Light theme:")
    win._apply_theme("light")
    win._tabs.setCurrentWidget(win._chart_panel)
    set_connected(win)
    pump(app, 400)
    grab(win, "main-window-light.png")

    # Reuse the annotated UI map that already ships in the repo root.
    src_map = ROOT / "ui_map.png"
    if src_map.exists():
        import shutil
        shutil.copyfile(src_map, OUT / "ui-map.png")
        print(f"  ✓ ui-map.png  (copied from {src_map.name})")

    print(f"\nDone. {len(list(OUT.glob('*.png')))} PNGs in {OUT}")
    win.close()


if __name__ == "__main__":
    main()
