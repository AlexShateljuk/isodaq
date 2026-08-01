#!/usr/bin/env python3
"""tools/gen_demo_gif.py — record the animated README demo GIF.

Builds the real MainWindow, streams synthetic serial data into it frame by frame
(chart grows, indicators tick, a Brownout trigger fires a red banner), captures
each frame with ``QWidget.grab()`` and assembles ``docs/images/demo.gif``.

Reuses the data helpers from ``gen_screenshots`` so the demo matches the static
screenshots. Run on a machine with a display:

    python tools/gen_demo_gif.py

Needs Pillow (``pip install Pillow``) and NumPy for GIF assembly.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))   # tools/ for gen_screenshots

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402
from PyQt6.QtGui import QIcon, QImage  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import gen_screenshots as gs  # noqa: E402  (sets MainWindow._CONFIG_PATH to a temp file)
from core import i18n  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402

OUT = ROOT / "docs" / "images" / "demo.gif"
ICON = ROOT / "ui" / "resources" / "icon.png"

# ── Tunables ──────────────────────────────────────────────────────────────────
FRAMES = 46          # total captured frames
FRAME_MS = 90        # display duration per frame (≈11 fps, ~4.1 s loop)
GIF_WIDTH = 820      # downscaled width (grab is ~2600 px on Retina)
COLORS = 128         # GIF palette size
TRIGGER_FRAME = 24   # frame at which the Brownout line fires


def capture(win) -> Image.Image:
    """Grab the window and return a downscaled Pillow RGB frame."""
    img = win.grab().toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = img.width(), img.height()
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    arr = np.frombuffer(ptr, np.uint8).reshape((h, img.bytesPerLine() // 4, 4))
    pil = Image.fromarray(arr[:, :w, :3].copy())   # (h, w, 3) uint8 → RGB
    return pil.resize((GIF_WIDTH, round(h * GIF_WIDTH / w)), Image.LANCZOS)


def save_gif(frames: list[Image.Image]) -> None:
    # One shared palette (from the last frame, which has every colour incl. the
    # red trigger banner) → no inter-frame flicker.
    pal = frames[-1].quantize(colors=COLORS, method=Image.Quantize.MEDIANCUT)
    q = [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in frames]
    q[0].save(OUT, save_all=True, append_images=q[1:],
              duration=FRAME_MS, loop=0, optimize=True)


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("IsoDAQ Studio")
    app.setApplicationVersion("0.3.1")
    if ICON.exists():
        app.setWindowIcon(QIcon(str(ICON)))
    i18n.init("en")
    random.seed(7)   # reproducible sensor noise
    MainWindow._CONFIG_PATH.unlink(missing_ok=True)

    win = MainWindow()
    win.resize(1300, 800)
    win.show()
    win._right_panel_visible = True
    win._apply_right_panel()
    gs.pump(app, 400)

    gs.setup_channels(win)
    win._log_colorizer_enabled = {"ESP32 (ESP-IDF)"}
    gs.set_connected(win)
    win._tabs.setCurrentWidget(win._chart_panel)

    # A little boot context in the terminal before the stream starts.
    for line in ("I (0210) boot: IsoDAQ target online, fw 1.4.2",
                 "I (0244) wifi: connected  rssi=-52  ip=192.168.1.42"):
        win._on_line_received(line, "14:32:10.001")
    gs.pump(app, 200)

    frames: list[Image.Image] = []
    ms = 400
    k = 0
    for i in range(FRAMES):
        # 2 data points per frame → a denser, smoother live curve.
        for _ in range(2):
            temp, hum, load = gs._sample(k * 0.35)
            ms += 90
            ts = f"14:32:{10 + ms // 1000:02d}.{ms % 1000:03d}"
            win._on_line_received(
                f"DATA: temp={temp:.1f} hum={hum:.1f} load={load:.1f}", ts)
            k += 1
        if i == TRIGGER_FRAME:
            win._on_line_received(
                "E (0951) power: Brownout Detector asserted, vdd=2.71V",
                f"14:32:{10 + ms // 1000:02d}.{ms % 1000:03d}")
        gs.pump(app, FRAME_MS)
        frames.append(capture(win))
        print(f"\r  frame {i + 1}/{FRAMES}", end="", flush=True)

    print("\n  assembling GIF…")
    save_gif(frames)
    size_mb = OUT.stat().st_size / 1e6
    print(f"Done. {OUT.relative_to(ROOT)}  "
          f"({frames[0].width}×{frames[0].height}, {len(frames)} frames, {size_mb:.1f} MB)")
    win.close()


if __name__ == "__main__":
    main()
