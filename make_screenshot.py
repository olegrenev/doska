# -*- coding: utf-8 -*-
"""Генератор скриншота доски (offscreen) для README."""
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from doska_qt import MainWindow, GRID_STEP  # noqa: E402

app = QApplication(sys.argv)
win = MainWindow()
win.showNormal()
win.resize(1100, 700)
win.show()
app.processEvents()
c = win.canvas

# оси и график
c.add_axes()
c.add_graph("sin(x)")
c.add_graph("0.5x - 1")

# пара «рукописных» штрихов с разным нажатием
def wave(x0, y0, n, dx, amp, color, width):
    pts = []
    for i in range(n):
        t = i / (n - 1)
        x = x0 + i * dx
        y = y0 + amp * math.sin(t * math.pi * 2.2)
        p = 0.35 + 0.65 * abs(math.sin(t * math.pi * 1.5))
        pts.append((x, y, p))
    return {"kind": "stroke", "pts": pts, "color": QColor(color),
            "width": width}

cx = c.offset.x() + c.width() / 2 / c.zoom
cy = c.offset.y() + c.height() / 2 / c.zoom
c.entries.append(wave(cx - 480, cy + 180, 60, 3.2, 26, "#1a1a1a", 5))
c.entries.append(wave(cx - 470, cy + 235, 55, 3.4, 22, "#c00000", 4))
c._rebuild_layers()

c.update()
app.processEvents()

out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "screenshot.png")
c.save_png(out)
print("saved:", out, os.path.getsize(out), "bytes")
