"""
tools/gen_icon.py — Generate ui/resources/icon.png and icon.ico using PyQt6.

Run once:
    python tools/gen_icon.py
"""
import math
import struct
import sys
from pathlib import Path

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PyQt6.QtGui import QBrush, QColor, QImage, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QApplication

OUT = Path(__file__).parent.parent / "ui" / "resources"

BG      = "#1e2235"
ACCENT  = "#4ec9b0"


def _render(size: int) -> bytes:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)

    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    s = size
    radius = s * 0.18

    # Rounded background
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor(BG)))
    p.drawRoundedRect(0, 0, s, s, radius, radius)

    # Sine-wave (oscilloscope line)
    lw = max(1, round(s * 0.072))
    pen = QPen(QColor(ACCENT), lw,
               Qt.PenStyle.SolidLine,
               Qt.PenCapStyle.RoundCap,
               Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    x0, x1   = s * 0.10, s * 0.90
    y_mid     = s * 0.52
    amplitude = s * 0.24
    steps     = max(128, size * 3)
    path = QPainterPath()
    for i in range(steps + 1):
        t = i / steps
        x = x0 + t * (x1 - x0)
        y = y_mid - math.sin(t * 2 * math.pi) * amplitude
        if i == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)
    p.drawPath(path)
    p.end()

    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba)


def _make_ico(png_map: dict[int, bytes]) -> bytes:
    sizes   = sorted(png_map, reverse=True)
    count   = len(sizes)
    header  = struct.pack("<HHH", 0, 1, count)
    offset  = 6 + count * 16
    entries = b""
    blobs   = b""
    for sz in sizes:
        data = png_map[sz]
        w = 0 if sz >= 256 else sz          # 0 encodes 256 in ICO spec
        h = 0 if sz >= 256 else sz
        entries += struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(data), offset)
        blobs   += data
        offset  += len(data)
    return header + entries + blobs


def main():
    app = QApplication(sys.argv)          # required for QPainter / QImage
    OUT.mkdir(parents=True, exist_ok=True)

    png_map = {sz: _render(sz) for sz in (256, 128, 64, 48, 32, 16)}

    icon_png = OUT / "icon.png"
    icon_ico = OUT / "icon.ico"
    icon_png.write_bytes(png_map[256])
    icon_ico.write_bytes(_make_ico(png_map))

    print(f"icon.png  -> {icon_png}")
    print(f"icon.ico  -> {icon_ico}")


if __name__ == "__main__":
    main()
