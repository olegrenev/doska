# -*- coding: utf-8 -*-
"""Регрессионный тест доски (offscreen): штрихи, ластик, undo, изображения."""
import math
import os
import subprocess
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import (QColor, QCursor, QGuiApplication, QImage,
                           QKeyEvent, QMouseEvent, QPainter, QPixmap)
from PySide6.QtWidgets import QApplication, QInputDialog

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from doska_qt import (Canvas, MainWindow, StrokeLayer, GRID_STEP,  # noqa: E402
                      ERASER_BASE, ERASER_MAX, TextDialog, _dist_to_segment,
                      _text_extent, _smooth_points)

app = QApplication(sys.argv)
win = MainWindow()
win.showNormal()
win.resize(900, 620)
win.show()
app.processEvents()
c = win.canvas

PASS = []


def ok(name, cond):
    PASS.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        raise SystemExit("FAILED: " + name)


def pix_bytes(pm):
    img = pm.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    bits = img.constBits()
    data = bits.tobytes() if hasattr(bits, "tobytes") else bytes(bits)
    return data[: img.sizeInBytes()]


def layers_match_entries():
    """Слои штрихов должны побайтово совпадать со свежим рендером из entries.
    Если это так после каждой операции, ластик/undo ничего 'не проявляют'."""
    fresh = []
    cur = None
    for e in c.entries:
        if e["kind"] in ("stroke", "line", "axes", "curve", "text"):
            if cur is None:
                cur = {"entries": []}
                fresh.append(cur)
            cur["entries"].append(e)
        else:
            cur = None
            fresh.append({"image": e})
    if len(fresh) != len(c.layers):
        return False
    for fl, cl in zip(fresh, c.layers):
        if "image" in fl:
            if cl["kind"] != "image" or cl["entry"] is not fl["image"]:
                return False
            continue
        if cl["kind"] != "strokes":
            return False
        tl = StrokeLayer()
        for e in fl["entries"]:
            bbox = c._entry_bbox(e)
            if e["kind"] == "stroke":
                tl.draw(bbox, lambda p, e=e: c._render_stroke(p, e))
            elif e["kind"] == "axes":
                tl.draw(bbox, lambda p, e=e: c._render_axes(p, e))
            elif e["kind"] == "curve":
                tl.draw(bbox, lambda p, e=e: c._render_curve(p, e))
            elif e["kind"] == "text":
                tl.draw(bbox, lambda p, e=e: c._render_text(p, e))
            else:
                tl.draw(bbox, lambda p, e=e: c._render_line(p, e))
        if set(tl.tiles) != set(cl["tl"].tiles):
            return False
        for k in tl.tiles:
            if pix_bytes(tl.tiles[k]) != pix_bytes(cl["tl"].tiles[k]):
                return False
    return True


def scr(sp):
    return (sp - c.offset) * c.zoom


def _entry_min_dist(e, pos):
    """Минимальное расстояние от записи до точки (для проверки ластика)."""
    best = 1e18
    if e["kind"] == "line":
        best = _dist_to_segment(pos.x(), pos.y(), e["p1"].x(), e["p1"].y(),
                                e["p2"].x(), e["p2"].y())
    elif e["kind"] == "stroke":
        pts = e["pts"]
        for i in range(1, len(pts)):
            best = min(best, _dist_to_segment(
                pos.x(), pos.y(), pts[i - 1][0], pts[i - 1][1],
                pts[i][0], pts[i][1]))
    elif e["kind"] == "curve":
        for seg in e["segs"]:
            for i in range(1, len(seg)):
                best = min(best, _dist_to_segment(
                    pos.x(), pos.y(), seg[i - 1][0], seg[i - 1][1],
                    seg[i][0], seg[i][1]))
    return best


def mev(etype, sp, btn, buttons, mods=Qt.KeyboardModifier.NoModifier):
    lp = scr(sp)
    gp = c.mapToGlobal(QPoint(int(lp.x()), int(lp.y())))
    return QMouseEvent(etype, QPointF(lp), QPointF(gp), btn, buttons, mods)


_orig_mfg = c.mapFromGlobal


def pen_at(sp, pressure, eraser=False):
    lp = scr(sp)
    c.mapFromGlobal = lambda gp, _lp=lp: QPoint(int(_lp.x()), int(_lp.y()))
    c.on_wintab(pressure, eraser)
    app.processEvents()


def pen_done():
    c.on_wintab(0.0, False)
    app.processEvents()
    c.mapFromGlobal = _orig_mfg
    c._wt_hover_last = 0


def stroke_pts(pts, pressure=0.6):
    """Штрих пером (нажатие 60% -> множитель 1.0, как у мыши раньше)."""
    pen_at(pts[0], pressure)
    for sp in pts[1:]:
        pen_at(sp, pressure)
    pen_done()


def mouse_line(p1, p2, mods=Qt.KeyboardModifier.ShiftModifier,
               mods_move=None):
    """Прямая линия мышью с зажатым Shift (или Shift+Ctrl — прилипание).
    mods_move — модификаторы на движении/отпускании, если отличаются."""
    if mods_move is None:
        mods_move = mods
    c._wt_hover_last = 0
    c.mousePressEvent(mev(QEvent.Type.MouseButtonPress, p1,
                          Qt.MouseButton.LeftButton,
                          Qt.MouseButton.LeftButton, mods))
    c.mouseMoveEvent(mev(QEvent.Type.MouseMove, p2,
                         Qt.MouseButton.NoButton,
                         Qt.MouseButton.LeftButton, mods_move))
    c.mouseReleaseEvent(mev(QEvent.Type.MouseButtonRelease, p2,
                            Qt.MouseButton.LeftButton,
                            Qt.MouseButton.NoButton, mods_move))
    app.processEvents()


def local_to_scene(e, loc):
    cx, cy = c._image_center(e)
    a = math.radians(e.get("angle", 0))
    ca, sa = math.cos(a), math.sin(a)
    return QPointF(cx + loc.x() * ca - loc.y() * sa,
                   cy + loc.x() * sa + loc.y() * ca)


# ---------- 0. стартовый вид: центр рабочей зоны ----------
ok("стартовый вид ~центр зоны",
   7000 < c.offset.x() < 8100 and 5200 < c.offset.y() < 6200)

# ---------- 1. обычный штрих пером: слой совпадает с entries ----------
base = QPointF(c.offset.x() + 200, c.offset.y() + 200)
pts1 = [base + QPointF(i * 14, 30 * math.sin(i / 2.0)) for i in range(14)]
stroke_pts(pts1)
ok("штрих добавлен", len(c.entries) == 1 and c.entries[0]["kind"] == "stroke")
ok("слой == entries после штриха", layers_match_entries())

# ---------- 2. второй штрих, ластик, undo: ничего лишнего не проявляется ----------
pts2 = [base + QPointF(i * 12, 120 - i * 6) for i in range(14)]
stroke_pts(pts2)
ok("второй штрих", len(c.entries) == 2)
ok("слой == entries после 2-го штриха", layers_match_entries())

# ---------- 2b. регрессия: два штриха делят плитку, но области не пересекаются ----------
# длинный штрих внизу плитки, затем короткий вверху той же плитки:
# длинный не должен потерять свои пиксели
tile_x = (int(base.x()) // 512) * 512
tile_y = (int(base.y()) // 512) * 512 + 512   # отдельная плитка ниже, в стороне от остальных тестов
long_pts = [QPointF(tile_x + 20 + i * 30, tile_y + 500) for i in range(16)]
stroke_pts(long_pts)
ok("длинный штрих добавлен", layers_match_entries())
short_pts = [QPointF(tile_x + 20 + i * 8, tile_y + 20 + i * 2) for i in range(4)]
stroke_pts(short_pts)
ok("короткий штрих в той же плитке не стирает длинный", layers_match_entries())

mid2 = pts2[7]
c._wt_hover_last = 0
c._erase_last = None          # радиус базовый, без влияния скорости
c._erase_speed = 0.0
c.erase_at(mid2)
app.processEvents()
# ластик теперь режет штрих локально: второй штрих -> два куска
ok("ластик разрезал второй штрих на два куска", len(c.entries) == 5)
frags = c.entries[1:3]
r_eff = ERASER_BASE + c.pen_width / 2
ok("куски штриха — вне круга ластика",
   all(e["kind"] == "stroke" and
       math.hypot(p[0] - mid2.x(), p[1] - mid2.y()) >= r_eff - 1.5
       for e in frags for p in e["pts"]))
ok("после ластика слой == entries (без лишних следов)", layers_match_entries())
c.undo()
app.processEvents()
ok("undo вернул штрих", len(c.entries) == 4)
ok("после undo слой == entries", layers_match_entries())

# ---------- 2c. радиус ластика зависит от скорости ----------
c._erase_last = (QPointF(0, 0), 1000.0)
c._erase_speed = 0.0
r_slow = c._erase_radius(QPointF(0, 0), now=1001.0)     # скорость ~0
c._erase_last = (QPointF(0, 0), 1000.0)
c._erase_speed = 0.0
r_fast = c._erase_radius(QPointF(500, 0), now=1000.1)   # ~5000 px/с
ok("медленное движение -> базовый радиус", abs(r_slow - ERASER_BASE) < 0.5)
ok("быстрое движение -> радиус заметно больше", r_fast > r_slow * 2)
ok("радиус ограничен максимумом", r_fast <= ERASER_MAX)
c._erase_last = (QPointF(0, 0), 1000.0)
c._erase_speed = 0.0
r_mid = c._erase_radius(QPointF(50, 0), now=1000.1)   # ~500 px/с — мёртвая зона
ok("скорость ниже порога -> базовый радиус (без шума)",
   abs(r_mid - ERASER_BASE) < 0.5)
c._erase_last = None
c._erase_speed = 0.0

# ---------- 2d. медленное ведение ластиком рядом с линией не трогает её ----------
c.mode = "line"
c.line_anchor = QPointF(2000, 8200)
c._line_snap = False
c._line_arrow = False
c._finish_input(QPointF(3000, 8200))
app.processEvents()
line_guard = c.entries[-1]
c._begin_erase()
c.mode = "erase"
t0 = 1000.0
for i in range(20):   # 25px за 50 мс = 500 px/с — в мёртвой зоне
    t0 += 0.05
    c.erase_at(QPointF(2010 + i * 25, 8220), now=t0)   # в 20px ниже линии
c.mode = None
c._erase_group = None
app.processEvents()
ok("медленное ведение рядом с линией не стирает её",
   line_guard in c.entries and layers_match_entries())
c.undo()   # убрать тестовую линию
app.processEvents()

# ---------- 3. Shift — прямая линия ----------
p1 = base + QPointF(300, 50)
p2 = base + QPointF(430, 160)
mouse_line(p1, p2)
ok("линия добавлена", c.entries[-1]["kind"] == "line")
ok("слой == entries после линии", layers_match_entries())

# ---------- 3b. прилипание по Ctrl и пунктир ----------
SH = Qt.KeyboardModifier.ShiftModifier
SHC = SH | Qt.KeyboardModifier.ControlModifier
mouse_line(base + QPointF(655.5, 207.5), base + QPointF(723.5, 262.5), SHC)
ln = c.entries[-1]
ok("Shift+Ctrl: концы линии в узлах сетки",
   ln["kind"] == "line"
   and ln["p1"].x() % GRID_STEP == 0 and ln["p1"].y() % GRID_STEP == 0
   and ln["p2"].x() % GRID_STEP == 0 and ln["p2"].y() % GRID_STEP == 0
   and not ln.get("dash"))
ok("слой == entries после линии с прилипанием", layers_match_entries())
# Ctrl можно нажать уже по ходу рисования
mouse_line(base + QPointF(655.5, 447.5), base + QPointF(723.5, 502.5),
           SH, mods_move=SHC)
ln2 = c.entries[-1]
ok("Ctrl нажат по ходу — линия тоже прилипла",
   ln2["p1"].x() % GRID_STEP == 0 and ln2["p1"].y() % GRID_STEP == 0
   and ln2["p2"].x() % GRID_STEP == 0 and ln2["p2"].y() % GRID_STEP == 0)
win.dash_btn.setChecked(True)
app.processEvents()
ok("пунктир включён на холсте", c.dashed_lines)
mouse_line(base + QPointF(655.5, 287.5), base + QPointF(723.5, 342.5))
ok("пунктир без Ctrl: dash, без прилипания",
   c.entries[-1].get("dash") is True
   and abs(c.entries[-1]["p1"].x() - (base.x() + 655.5)) < 1e-9
   and layers_match_entries())
win.dash_btn.setChecked(False)
app.processEvents()
mouse_line(base + QPointF(655.5, 367.5), base + QPointF(723.5, 422.5))
ln3 = c.entries[-1]
ok("без Ctrl линия сплошная и не прилипает",
   not ln3.get("dash")
   and abs(ln3["p1"].x() - (base.x() + 655.5)) < 1e-9
   and abs(ln3["p1"].y() - (base.y() + 367.5)) < 1e-9)
ok("слой == entries после обычной линии", layers_match_entries())
for _ in range(4):
    c.undo()  # убрать четыре тестовые линии
app.processEvents()
ok("тестовые линии убраны", len(c.entries) == 5 and layers_match_entries())

# ---------- 3b++. ластик сохраняет пунктир и redo повторяет стирание ----------
_dash = {"kind": "line",
         "p1": base + QPointF(500, 700),
         "p2": base + QPointF(700, 700),
         "color": QColor("#1a1a1a"), "width": 5,
         "dash": True, "arrow": True}
c.entries.append(_dash)
c._rebuild_layers()
c._erase_last = None
c._erase_speed = 0.0
c.erase_at(base + QPointF(600, 700))
app.processEvents()
_dfrags = [e for e in c.entries
           if e["kind"] == "line" and e.get("dash") is True]
ok("ластик разрезал пунктирную линию на два куска",
   len(_dfrags) == 2 and layers_match_entries())
ok("куски пунктирной линии остались пунктирными",
   all(e.get("dash") is True for e in _dfrags))
ok("стрелка осталась на куске с исходным концом",
   sum(1 for e in _dfrags if e.get("arrow")) == 1
   and any(e.get("arrow") and e["p2"] == _dash["p2"] for e in _dfrags))
c.undo()
app.processEvents()
ok("undo вернул пунктирную линию", _dash in c.entries
   and layers_match_entries())
c.redo()
app.processEvents()
_dfrags2 = [e for e in c.entries
            if e["kind"] == "line" and e.get("dash") is True]
ok("redo повторил стирание и сохранил пунктир",
   _dash not in c.entries and len(_dfrags2) == 2
   and all(e.get("dash") is True for e in _dfrags2)
   and layers_match_entries())
c.undo()
app.processEvents()
ok("повторный undo снова вернул пунктирную линию",
   _dash in c.entries and layers_match_entries())
c.entries.remove(_dash)
c.selected_entries = []
c._rebuild_layers()
c.redo_stack.clear()

# ---------- 3b+. Q — отрезок со стрелкой ----------
# флаг клавиши Q выставляется настоящими клавишными событиями
c.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Q,
                          Qt.KeyboardModifier.NoModifier))
ok("нажатие Q выставляет флаг", c._q_held)
c.keyReleaseEvent(QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Q,
                            Qt.KeyboardModifier.NoModifier))
ok("отпускание Q сбрасывает флаг", not c._q_held)

# русская раскладка: key() уже не Key_Q, но виртуальный код тот же (VK_Q)
c.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A,
                          Qt.KeyboardModifier.NoModifier,
                          0x10, 0x51, 0, "й"))
ok("Q срабатывает при русской раскладке", c._q_held)
c.keyReleaseEvent(QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_A,
                            Qt.KeyboardModifier.NoModifier,
                            0x10, 0x51, 0, "й"))
ok("отпускание Q в русской раскладке", not c._q_held)

c._q_held = True
mouse_line(base + QPointF(300, 550), base + QPointF(430, 660),
           Qt.KeyboardModifier.NoModifier)
ar = c.entries[-1]
ok("Q: проведена линия-стрелка",
   ar["kind"] == "line" and ar.get("arrow") is True)
ok("флаг стрелки сброшен после отпускания", c._line_arrow is False)
ok("слой == entries после стрелки", layers_match_entries())

# Q + Ctrl — концы прилипают к узлам сетки
c._q_held = True
mouse_line(base + QPointF(655.5, 527.5), base + QPointF(723.5, 582.5),
           Qt.KeyboardModifier.ControlModifier)
ar2 = c.entries[-1]
ok("Q+Ctrl: стрелка прилипла к узлам сетки",
   ar2.get("arrow") is True
   and ar2["p1"].x() % GRID_STEP == 0 and ar2["p1"].y() % GRID_STEP == 0
   and ar2["p2"].x() % GRID_STEP == 0 and ar2["p2"].y() % GRID_STEP == 0)
c._q_held = False

# Q + перо (WinTab) — тоже стрелка
c._q_held = True
pen_at(base + QPointF(100, 750), 0.6)
pen_at(base + QPointF(220, 780), 0.6)
pen_done()
ok("Q + перо: стрелка",
   c.entries[-1]["kind"] == "line" and c.entries[-1].get("arrow") is True)
c._q_held = False

# рендер стрелки (наконечник) не падает ни в плитках, ни при перерисовке
c.repaint()
app.processEvents()
for _ in range(3):
    c.undo()  # убрать три тестовые стрелки
app.processEvents()
ok("стрелки убраны", len(c.entries) == 5 and layers_match_entries())

# ---------- 3c. окружность по центру и точке ----------
win.circle_btn.setChecked(True)
app.processEvents()
ok("инструмент окружности включён", c.circle_tool)
ok("кнопка называется «Окружность»", win.circle_btn.text() == "Окружность")
cc = base + QPointF(700, 620)
mouse_line(cc, cc + QPointF(100, 0), Qt.KeyboardModifier.NoModifier)
cir = c.entries[-2]
dot1 = c.entries[-1]
ok("окружность построена как замкнутая кривая",
   cir["kind"] == "curve" and len(cir["segs"]) == 1
   and abs(cir["segs"][0][0][0] - cir["segs"][0][-1][0]) < 1e-9
   and abs(cir["segs"][0][0][1] - cir["segs"][0][-1][1]) < 1e-9)
ok("все точки на окружности r=100 от центра",
   all(abs(math.hypot(px - cc.x(), py - cc.y()) - 100) < 0.6
       for (px, py) in cir["segs"][0]))
ok("центр окружности отмечен точкой",
   dot1["kind"] == "stroke" and len(dot1["pts"]) == 1
   and abs(dot1["pts"][0][0] - cc.x()) < 1e-9
   and abs(dot1["pts"][0][1] - cc.y()) < 1e-9)
ok("слой == entries после окружности", layers_match_entries())
# Ctrl: центр и радиус прилипают к сетке
cc2 = base + QPointF(705.5, 827.5)
mouse_line(cc2, cc2 + QPointF(60, 0), Qt.KeyboardModifier.ControlModifier)
cir2 = c.entries[-2]
dot2 = c.entries[-1]
xs2 = [p[0] for p in cir2["segs"][0]]
ys2 = [p[1] for p in cir2["segs"][0]]
cx2 = (min(xs2) + max(xs2)) / 2
cy2 = (min(ys2) + max(ys2)) / 2
ok("Ctrl: центр окружности в узле сетки",
   min(cx2 % GRID_STEP, GRID_STEP - cx2 % GRID_STEP) < 0.1
   and min(cy2 % GRID_STEP, GRID_STEP - cy2 % GRID_STEP) < 0.1
   and abs(dot2["pts"][0][0] % GRID_STEP) < 1e-6
   and abs(dot2["pts"][0][1] % GRID_STEP) < 1e-6)
r_raw = math.hypot(cc2.x() + 60 - dot2["pts"][0][0],
                   cc2.y() - dot2["pts"][0][1])
r_snap = max(GRID_STEP, round(r_raw / GRID_STEP) * GRID_STEP)
ok("Ctrl: радиус кратен клетке",
   all(abs(math.hypot(px - dot2["pts"][0][0], py - dot2["pts"][0][1])
           - r_snap) < 0.6 for (px, py) in cir2["segs"][0]))
# Ctrl отпущен по ходу: центр остаётся прилипшим, радиус — нет
cc3 = base + QPointF(707.5, 967.5)
mouse_line(cc3, cc3 + QPointF(60, 10),
           Qt.KeyboardModifier.ControlModifier,
           mods_move=Qt.KeyboardModifier.NoModifier)
cir3 = c.entries[-2]
dot3 = c.entries[-1]
ok("Ctrl отпущен по ходу: центр остался в узле сетки",
   abs(dot3["pts"][0][0] % GRID_STEP) < 1e-6
   and abs(dot3["pts"][0][1] % GRID_STEP) < 1e-6)
r3_raw = math.hypot(cc3.x() + 60 - dot3["pts"][0][0],
                    cc3.y() + 10 - dot3["pts"][0][1])
ok("Ctrl отпущен по ходу: радиус не прилипает",
   min(r3_raw % GRID_STEP, GRID_STEP - r3_raw % GRID_STEP) > 1.0
   and all(abs(math.hypot(px - dot3["pts"][0][0], py - dot3["pts"][0][1])
               - r3_raw) < 0.6 for (px, py) in cir3["segs"][0]))
# пунктир действует на окружность
win.dash_btn.setChecked(True)
app.processEvents()
cc4 = base + QPointF(707.5, 1107.5)
mouse_line(cc4, cc4 + QPointF(70, 0), Qt.KeyboardModifier.NoModifier)
ok("пунктир действует на окружность",
   c.entries[-2].get("dash") is True and layers_match_entries())
win.dash_btn.setChecked(False)
app.processEvents()
# ластик вырезает дугу, а не всю окружность
c._erase_last = None
c._erase_speed = 0.0
ep = QPointF(cc.x() + 100, cc.y())
c.erase_at(ep)
app.processEvents()
ok("ластик вырезал дугу окружности", cir not in c.entries
   and layers_match_entries())
ok("рядом с ластиком окружности не осталось",
   all(_entry_min_dist(en, ep) >= ERASER_BASE - 1.5 for en in c.entries))
c.undo()
app.processEvents()
ok("undo вернул окружность", cir in c.entries and layers_match_entries())
# центр стирается отдельно от окружности
c._erase_last = None
c._erase_speed = 0.0
c.erase_at(QPointF(dot2["pts"][0][0], dot2["pts"][0][1]))
app.processEvents()
ok("центр стирается отдельно",
   dot2 not in c.entries and cir2 in c.entries and layers_match_entries())
c.undo()
app.processEvents()
ok("undo вернул центр", dot2 in c.entries and layers_match_entries())
win.circle_btn.setChecked(False)
app.processEvents()
ok("инструмент окружности выключен", not c.circle_tool)
c.undo()
c.undo()
c.undo()
c.undo()  # убрать четыре окружности (каждую — вместе с её центром)
app.processEvents()
ok("окружности убраны", len(c.entries) == 5 and layers_match_entries())

# ---------- 3d. сетка и надписи ----------
ok("сетка включена по умолчанию", c.show_grid and win.grid_btn.isChecked())
win.grid_btn.setChecked(False)
app.processEvents()
ok("кнопка выключила сетку", not c.show_grid)
win.grid_btn.setChecked(True)
app.processEvents()
ok("кнопка включила сетку", c.show_grid)

# обычная надпись — появляется в центре видимой области
ok("обычная надпись добавлена", c.add_text("Привет, доска", 28, False))
t1 = c.entries[-1]
ok("надпись — запись text", t1["kind"] == "text" and not t1["latex"])
w1, a1, d1 = _text_extent(t1)
vcx = c.offset.x() + c.width() / 2 / c.zoom
vcy = c.offset.y() + c.height() / 2 / c.zoom
ok("надпись по центру вида",
   abs(t1["pos"].x() - (vcx - w1 / 2)) < 1
   and abs(t1["pos"].y() - (vcy + (a1 - d1) / 2)) < 1)
ok("слой == entries после надписи", layers_match_entries())

# многострочная надпись — сдвигаем вид, чтобы надписи не перекрывались
c.offset += QPointF(0, 300)
c.update()
ok("многострочная надпись", c.add_text("строка 1\nстрока 2", 20, False))
t_multi = c.entries[-1]
w_m, a_m, d_m = _text_extent(t_multi)
ok("многострочная: высота учитывает обе строки",
   d_m >= 20 * 1.25 and layers_match_entries())

# LaTeX-надпись
c.offset += QPointF(0, 300)
c.update()
ok("latex-надпись добавлена",
   c.add_text(r"E=mc^2, \frac{a+b}{2}, \sqrt{x}, \alpha \cdot \beta", 28,
              True))
t2 = c.entries[-1]
w2, a2, d2 = _text_extent(t2)
ok("latex-формула отмеряется", w2 > 60 and a2 > 10 and d2 > 2)
ok("слой == entries после latex-надписи", layers_match_entries())

# ластик стирает надпись целиком
c._erase_last = None
c._erase_speed = 0.0
c.erase_at(QPointF(t2["pos"].x() + w2 / 2, t2["pos"].y() + d2 / 2))
app.processEvents()
ok("ластик стёр надпись целиком",
   t2 not in c.entries and layers_match_entries())
c.undo()
app.processEvents()
ok("undo вернул надпись", t2 in c.entries and layers_match_entries())

# диалог: поля и выбранные значения
dlg = TextDialog(win)
dlg.edit.setPlainText("x^2")
dlg.radio_latex.setChecked(True)
dlg.combo.setCurrentIndex(2)
ok("диалог отдаёт текст, режим и размер",
   dlg.text() == "x^2" and dlg.is_latex() and dlg.size() == 40)
ok("Tab в диалоге переводит фокус", dlg.edit.tabChangesFocus())

# надпись перемещается
old_pos = QPointF(t1["pos"])
c._translate_entry(t1, QPointF(15, 25))
ok("надпись перемещается",
   abs(t1["pos"].x() - old_pos.x() - 15) < 1e-9
   and abs(t1["pos"].y() - old_pos.y() - 25) < 1e-9)
c._rebuild_layers()
ok("слой == entries после перемещения надписи", layers_match_entries())

c.undo()
c.undo()
c.undo()  # убрать три надписи
app.processEvents()
ok("надписи убраны", len(c.entries) == 5 and layers_match_entries())

# ---------- 4. перо через WinTab: нажатие -> толщина, задний конец -> ластик ----------
pen_pts = [base + QPointF(60 + i * 10, 260 + 20 * math.sin(i)) for i in range(10)]
pen_at(pen_pts[0], 0.3)
for sp in pen_pts[1:]:
    pen_at(sp, 0.3)
pen_done()
ok("перьевой штрих добавлен",
   len(c.entries) == 6 and c.entries[-1]["kind"] == "stroke")
ok("нажатие 30% -> множитель ~0.72",
   abs(c.entries[-1]["pts"][0][2] - 0.72) < 1e-6)
ok("слой == entries после пера", layers_match_entries())

pen_stroke_entry = c.entries[-1]
mid_pen = pen_pts[5]
pen_at(mid_pen, 0.5, True)   # задний конец пера
pen_at(pen_pts[6], 0.5, True)
pen_done()
ok("задний конец пера стирает", pen_stroke_entry not in c.entries)
ok("слой == entries после ластика пера", layers_match_entries())
c.undo()
app.processEvents()
ok("undo после ластика пера", len(c.entries) == 6 and layers_match_entries())

# ---------- 4b. ластик стирает одиночную точку ----------
dot = base + QPointF(600, -250)
pen_at(dot, 0.5)
pen_done()
ok("одиночная точка поставлена",
   len(c.entries) == 7 and c.entries[-1]["kind"] == "stroke"
   and len(c.entries[-1]["pts"]) == 1)
c._erase_last = None
c._erase_speed = 0.0
c.erase_at(dot)
app.processEvents()
ok("ластик стёр точку",
   len(c.entries) == 6 and layers_match_entries())
c.undo()
app.processEvents()
ok("undo вернул точку",
   len(c.entries) == 7 and len(c.entries[-1]["pts"]) == 1
   and layers_match_entries())

# ---------- 5. изображение: вставка, поворот со snap, resize, Del ----------
img = QImage(160, 100, QImage.Format.Format_RGB32)
img.fill(QColor("#d92b2b"))
QGuiApplication.clipboard().setImage(img)
c.cursor_scene = base + QPointF(600, 300)
c.paste_image()
app.processEvents()
ok("картинка вставлена и выбрана",
   c.entries[-1]["kind"] == "image" and c.selected_image is c.entries[-1])
e = c.selected_image
ok("слой == entries после вставки картинки", layers_match_entries())

# пропорциональный resize мышью: тянем угол неравномерно — пропорции сохраняются
w0, h0 = e["pix"].width(), e["pix"].height()   # 160x100
sh = local_to_scene(e, QPointF(w0 / 2.0, h0 / 2.0))
c._wt_hover_last = 0
c.mousePressEvent(mev(QEvent.Type.MouseButtonPress, sh,
                      Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
mv = local_to_scene(e, QPointF(w0 / 2.0 + 40, h0 / 2.0 + 5))  # сдвиг по x сильнее
c.mouseMoveEvent(mev(QEvent.Type.MouseMove, mv,
                     Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
c.mouseReleaseEvent(mev(QEvent.Type.MouseButtonRelease, mv,
                        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
app.processEvents()
# sx = 120/80 = 1.5, sy = 55/50 = 1.1 -> единый масштаб 1.5 -> 240x150
ok("resize пропорциональный: 160x100 -> 240x150",
   abs(e["pix"].width() - 240) <= 1 and abs(e["pix"].height() - 150) <= 1)

# перо на ручке размера: меняет размер, а НЕ ставит точку
orig_mfg2 = c.mapFromGlobal
n0 = len(c.entries)
w1, h1 = e["pix"].width(), e["pix"].height()
shp = local_to_scene(e, QPointF(w1 / 2.0, h1 / 2.0))
lp = scr(shp)
c.mapFromGlobal = lambda gp, _lp=lp: QPoint(int(_lp.x()), int(_lp.y()))
c.on_wintab(0.6, False)
app.processEvents()
ok("перо на ручке размера: режим resize, а не штрих", c.mode == "resize")
tgp = local_to_scene(e, QPointF(w1 / 2.0 + 20, h1 / 2.0 + 20))
lp = scr(tgp)
c.mapFromGlobal = lambda gp, _lp=lp: QPoint(int(_lp.x()), int(_lp.y()))
c.on_wintab(0.6, False)
c.on_wintab(0.0, False)
app.processEvents()
c.mapFromGlobal = orig_mfg2
c._wt_hover_last = 0
ok("перо на ручке размера: ни одной лишней записи", len(c.entries) == n0)
ok("перо на ручке размера: выросло пропорционально",
   abs(e["pix"].width() / e["pix"].height() - w1 / h1) < 0.02
   and e["pix"].width() > w1)

# перо на ручке поворота: вращает, а НЕ ставит точку
n0 = len(c.entries)
rhp = local_to_scene(e, QPointF(0, -e["pix"].height() / 2.0 - 28.0 / c.zoom))
lp = scr(rhp)
c.mapFromGlobal = lambda gp, _lp=lp: QPoint(int(_lp.x()), int(_lp.y()))
c.on_wintab(0.6, False)
app.processEvents()
ok("перо на ручке поворота: режим rotate, а не штрих", c.mode == "rotate")
cx, cy = c._image_center(e)
tgp = QPointF(cx + 300 * math.cos(math.radians(5)),
              cy + 300 * math.sin(math.radians(5)))
lp = scr(tgp)
c.mapFromGlobal = lambda gp, _lp=lp: QPoint(int(_lp.x()), int(_lp.y()))
c.on_wintab(0.6, False)
c.on_wintab(0.0, False)
app.processEvents()
c.mapFromGlobal = orig_mfg2
c._wt_hover_last = 0
ok("перо повернуло картинку к 90", abs(e["angle"] - 90.0) < 1e-9)
ok("перо на ручке поворота: ни одной лишней записи", len(c.entries) == n0)

# поворот мышью: за ручку, указатель ~95 градусов -> snap к 90
rh = local_to_scene(e, QPointF(0, -e["pix"].height() / 2.0 - 28.0 / c.zoom))
c._wt_hover_last = 0
c.mousePressEvent(mev(QEvent.Type.MouseButtonPress, rh,
                      Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
cx, cy = c._image_center(e)
target = QPointF(cx + 300 * math.cos(math.radians(5)),
                 cy + 300 * math.sin(math.radians(5)))
c.mouseMoveEvent(mev(QEvent.Type.MouseMove, target,
                     Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
c.mouseReleaseEvent(mev(QEvent.Type.MouseButtonRelease, target,
                        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
app.processEvents()
ok("поворот с прилипанием к 90", abs(e["angle"] - 90.0) < 1e-9)

# resize после поворота (локальные координаты)
w0, h0 = e["pix"].width(), e["pix"].height()
sh = local_to_scene(e, QPointF(w0 / 2.0, h0 / 2.0))
c.mousePressEvent(mev(QEvent.Type.MouseButtonPress, sh,
                      Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
mv = local_to_scene(e, QPointF(w0 / 2.0 + 60, h0 / 2.0 + 40))
c.mouseMoveEvent(mev(QEvent.Type.MouseMove, mv,
                     Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
c.mouseReleaseEvent(mev(QEvent.Type.MouseButtonRelease, mv,
                        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
app.processEvents()
ok("resize после поворота: пропорции сохранены, размер вырос",
   abs(e["pix"].width() / e["pix"].height() - w0 / h0) < 0.02
   and e["pix"].width() > w0 and e["pix"].height() > h0)

# курсор-стрелка над ручками
sh2 = local_to_scene(e, QPointF(e["pix"].width() / 2.0, e["pix"].height() / 2.0))
c.mouseMoveEvent(mev(QEvent.Type.MouseMove, sh2,
                     Qt.MouseButton.NoButton, Qt.MouseButton.NoButton))
app.processEvents()
ok("курсор-стрелка над ручкой размера",
   c.cursor().shape() == Qt.CursorShape.ArrowCursor)
rh2 = local_to_scene(e, QPointF(0, -e["pix"].height() / 2.0 - 28.0 / c.zoom))
c.mouseMoveEvent(mev(QEvent.Type.MouseMove, rh2,
                     Qt.MouseButton.NoButton, Qt.MouseButton.NoButton))
app.processEvents()
ok("курсор-стрелка над ручкой поворота",
   c.cursor().shape() == Qt.CursorShape.ArrowCursor)
empty = QPointF(c.offset.x() + 30, c.offset.y() + 30)
c.mouseMoveEvent(mev(QEvent.Type.MouseMove, empty,
                     Qt.MouseButton.NoButton, Qt.MouseButton.NoButton))
app.processEvents()
ok("цветной курсор на пустом месте",
   c.cursor().shape() == Qt.CursorShape.BitmapCursor)
c.set_color(QColor("#2b5fd9"))
_pm = c._make_pen_cursor().pixmap().toImage()
ok("курсор использует выбранный цвет",
   _pm.pixelColor(16, 16) == QColor("#2b5fd9"))
c.set_color(QColor("#1a1a1a"))

# Delete удаляет, undo возвращает
c.delete_selected()
app.processEvents()
ok("Del удаляет картинку", e not in c.entries and c.selected_image is None)
ok("слой == entries после Del", layers_match_entries())
c.undo()
app.processEvents()
ok("undo возвращает картинку", e in c.entries and layers_match_entries())

# ---------- 6. координатные оси ----------
# уводим вид на пустое место, чтобы ластик не задел другие записи
c.offset = QPointF(2000, 2000)
c.update()
c.add_axes()
app.processEvents()
ok("оси добавлены: начало ровно в узле сетки",
   c.entries[-1]["kind"] == "axes"
   and c.entries[-1]["center"].x() % GRID_STEP == 0
   and c.entries[-1]["center"].y() % GRID_STEP == 0
   and abs(c.entries[-1]["center"].x() - (c.offset.x() + c.width() / 2)) <= GRID_STEP / 2)
ok("слой == entries после осей", layers_match_entries())
ax = c.entries[-1]
c._wt_hover_last = 0
c._erase_last = None
c._erase_speed = 0.0
ep = ax["center"] + QPointF(100, 0)   # точка на оси X
c.erase_at(ep)
app.processEvents()
# частичное стирание: оси разложены на линии, кусок оси X вырезан
ok("ластик частично стёр оси (разложил на линии)", ax not in c.entries)


ok("рядом с ластиком линий не осталось",
   all(_entry_min_dist(en, ep) >= ERASER_BASE - 1.5 for en in c.entries))
ok("слой == entries после частичного стирания осей", layers_match_entries())
c.undo()
app.processEvents()
ok("undo возвращает оси", ax in c.entries and layers_match_entries())

# ---------- 7. рамка выделения: выбор, перемещение, Del ----------
c.offset = QPointF(4000, 4000)
c.update()
s1_pts = [QPointF(4400 + i * 12, 4400 + 30 * math.sin(i / 2.0)) for i in range(10)]
s2_pts = [QPointF(4400 + i * 12, 4520 + i * 4) for i in range(10)]
stroke_pts(s1_pts)
stroke_pts(s2_pts)
n_sel0 = len(c.entries)

# ЛКМ тянет рамку выделения
c._wt_hover_last = 0
c.mousePressEvent(mev(QEvent.Type.MouseButtonPress, QPointF(4350, 4350),
                      Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
c.mouseMoveEvent(mev(QEvent.Type.MouseMove, QPointF(4620, 4620),
                     Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
c.mouseReleaseEvent(mev(QEvent.Type.MouseButtonRelease, QPointF(4620, 4620),
                        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
app.processEvents()
ok("рамка выделила оба штриха", len(c.selected_entries) == 2)

# перетаскивание ЛКМ внутри выделения двигает записи
before = c.selected_entries[0]["pts"][0][:2]
c.mousePressEvent(mev(QEvent.Type.MouseButtonPress, QPointF(4430, 4430),
                      Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
c.mouseMoveEvent(mev(QEvent.Type.MouseMove, QPointF(4490, 4470),
                     Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
c.mouseReleaseEvent(mev(QEvent.Type.MouseButtonRelease, QPointF(4490, 4470),
                        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
app.processEvents()
after = c.selected_entries[0]["pts"][0][:2]
ok("выделенные записи переместились на (60, 40)",
   abs(after[0] - before[0] - 60) < 1 and abs(after[1] - before[1] - 40) < 1)
ok("слой == entries после перемещения", layers_match_entries())

# Del удаляет выделенное, undo возвращает
c.delete_selected()
app.processEvents()
ok("Del удалил выделенные записи",
   len(c.entries) == n_sel0 - 2 and not c.selected_entries)
ok("слой == entries после Del", layers_match_entries())
c.undo()
app.processEvents()
ok("undo вернул выделенные записи",
   len(c.entries) == n_sel0 and layers_match_entries())

# клик на пустом месте снимает выделение
c.selected_entries = list(c.entries[-2:])
c.mousePressEvent(mev(QEvent.Type.MouseButtonPress, QPointF(4800, 4800),
                      Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
c.mouseReleaseEvent(mev(QEvent.Type.MouseButtonRelease, QPointF(4800, 4800),
                        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
app.processEvents()
ok("клик на пустом месте снял выделение", not c.selected_entries)

# ---------- 8. график по формуле ----------
ax = [e for e in c.entries if e["kind"] == "axes"][-1]
cxg, cyg = ax["center"].x(), ax["center"].y()
n_g0 = len(c.entries)
ok("график y=x построен", c.add_graph("x"))
graph_entries = c.entries[n_g0:]
ok("график — одна запись-кривая",
   len(graph_entries) == 1 and graph_entries[0]["kind"] == "curve")
# точка: x = 2 клетки -> y = 2 клетки вверх от начала координат
found = any(abs(px - (cxg + 80)) <= 2.5 and abs(py - (cyg - 80)) <= 2.5
            for e in graph_entries for seg in e["segs"] for (px, py) in seg)
ok("точка графика (2, 2) на месте", found)
ok("слой == entries после графика", layers_match_entries())
ok("плохая формула не строится и ничего не добавляет",
   not c.add_graph("no_such_fn(x)") and len(c.entries) == n_g0 + len(graph_entries))
c.undo()
app.processEvents()
ok("undo снимает весь график одним действием",
   len(c.entries) == n_g0 and layers_match_entries())

# степень через ^
ok("степень через ^ работает", c.add_graph("x^2/20"))
par = c.entries[-1]
found = any(abs(px - (cxg + 160)) <= 3 and abs(py - (cyg - 32)) <= 3
            for seg in par["segs"] for (px, py) in seg)
ok("парабола x^2/20: точка (4 клетки, 0.8 клетки) на месте", found)
c.undo()
app.processEvents()

# подразумеваемое умножение: 4x, x(x+1), (a)(b), 2sin(x)
ok("y=x^2-4x+3 строится", c.add_graph("y=x^2-4x+3"))
quad = c.entries[-1]
root1 = any(abs(px - (cxg + 40)) <= 3 and abs(py - cyg) <= 3
            for seg in quad["segs"] for (px, py) in seg)
root3 = any(abs(px - (cxg + 120)) <= 3 and abs(py - cyg) <= 3
            for seg in quad["segs"] for (px, py) in seg)
ok("корни параболы x=1 и x=3 на месте", root1 and root3)
ok("слой == entries после параболы", layers_match_entries())
c.undo()
app.processEvents()
ok("умножение x(x+1) строится", c.add_graph("x(x+1)/4"))
c.undo()
app.processEvents()
ok("умножение (x-1)(x-3) строится", c.add_graph("(x-1)(x-3)/4"))
c.undo()
app.processEvents()
ok("умножение 2sin(x) строится", c.add_graph("2sin(x/2)"))
c.undo()
app.processEvents()
ok("уравнение xy=4 строится", c.add_graph("xy=4"))
hyp = c.entries[-1]
found_h = any(abs(px - (cxg + 80)) <= 6 and abs(py - (cyg - 80)) <= 6
              for seg in hyp["segs"] for (px, py) in seg)
ok("гипербола xy=4 проходит через (2, 2) клетки", found_h)
c.undo()
app.processEvents()

# форма y = f(x)
ok("форма y = -x/2 работает", c.add_graph("y = -x/2"))
half = c.entries[-1]
found = any(abs(px - (cxg + 80)) <= 2.5 and abs(py - (cyg + 40)) <= 2.5
            for seg in half["segs"] for (px, py) in seg)
ok("точка (2, -1) формы y=-x/2 на месте", found)
c.undo()
app.processEvents()

# уравнение окружности x^2 + y^2 = 9 (радиус 3 клетки)
ok("уравнение окружности построено", c.add_graph("x^2 + y^2 = 9"))
circ = c.entries[-1]
ok("окружность — кривая", circ["kind"] == "curve")
found_r = any(abs(px - (cxg + 120)) <= 6 and abs(py - cyg) <= 6
              for seg in circ["segs"] for (px, py) in seg)
found_t = any(abs(px - cxg) <= 6 and abs(py - (cyg - 120)) <= 6
              for seg in circ["segs"] for (px, py) in seg)
ok("окружность проходит через (3, 0) и (0, 3) клетки", found_r and found_t)
ok("слой == entries после окружности", layers_match_entries())
# ластик вырезает дугу окружности, а не всю кривую
c._wt_hover_last = 0
c._erase_last = None
c._erase_speed = 0.0
ep = QPointF(cxg + 85, cyg - 85)  # под 45°, чтобы не зацепить оси
c.erase_at(ep)
app.processEvents()
ok("ластик вырезал дугу окружности", circ not in c.entries)
ok("окружность сохранилась как кривая",
   any(e["kind"] == "curve" for e in c.entries) and layers_match_entries())
ok("рядом с ластиком кривой не осталось",
   all(_entry_min_dist(en, ep) >= ERASER_BASE - 1.5 for en in c.entries))
c.undo()
app.processEvents()
ok("undo вернул окружность", circ in c.entries and layers_match_entries())
c.entries.remove(circ)  # окружность не нужна в финальном PNG
c._rebuild_layers()

c.add_graph("sin(x)")
app.processEvents()
ok("sin(x) построен для финального PNG", len(c.entries) > n_g0)

# ---------- 8.5. кнопка «График» (диалог) и переключатель режима ЛКМ ----------
orig_get = QInputDialog.getText
QInputDialog.getText = staticmethod(lambda *a, **k: ("x/3", True))
n_b0 = len(c.entries)
win.graph_btn.click()
app.processEvents()
QInputDialog.getText = orig_get
ok("кнопка «График» открыла диалог и построила график",
   len(c.entries) > n_b0)
ok("слой == entries после кнопки «График»", layers_match_entries())

orig_get = QInputDialog.getText
QInputDialog.getText = staticmethod(lambda *a, **k: ("", False))
n_b1 = len(c.entries)
win.graph_btn.click()
app.processEvents()
QInputDialog.getText = orig_get
ok("отмена диалога ничего не добавляет", len(c.entries) == n_b1)

# ---------- 8.6. кнопка «Информация» ----------
ok("кнопка Информация есть", win.info_btn is not None
   and win.info_btn.text() == "Информация")
_html = win._help_html()
ok("справка описывает ластик, выделение, изображения, графики",
   all(s in _html for s in ("Ластик", "Ctrl+Z", "Ctrl+V", "Ctrl+колёсико",
                            "Выделение", "Изображения", "Оси", "График",
                            "F1", "задний конец пера")))
ok("справка содержит номер версии", "3.1" in _html)

ok("по умолчанию ЛКМ — выделение", not c.mouse_draw)
win.mousemode_btn.setChecked(True)
app.processEvents()
ok("переключатель включил рисование ЛКМ",
   c.mouse_draw and win.mousemode_btn.text() == "ЛКМ: рисует (M)")
n_m0 = len(c.entries)
mpts = [QPointF(5200 + i * 10, 5200 + i * 3) for i in range(8)]
c._wt_hover_last = 0
c.mousePressEvent(mev(QEvent.Type.MouseButtonPress, mpts[0],
                      Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
for sp in mpts[1:]:
    c.mouseMoveEvent(mev(QEvent.Type.MouseMove, sp,
                         Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
c.mouseReleaseEvent(mev(QEvent.Type.MouseButtonRelease, mpts[-1],
                        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
app.processEvents()
ok("ЛКМ в режиме рисования рисует штрих",
   len(c.entries) == n_m0 + 1 and c.entries[-1]["kind"] == "stroke")
ok("слой == entries после рисования мышью", layers_match_entries())

win.mousemode_btn.setChecked(False)
app.processEvents()
c._wt_hover_last = 0
c.mousePressEvent(mev(QEvent.Type.MouseButtonPress, QPointF(5400, 5400),
                      Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
c.mouseReleaseEvent(mev(QEvent.Type.MouseButtonRelease, QPointF(5400, 5400),
                        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
app.processEvents()
ok("обратно в режим выделения: ЛКМ не рисует",
   len(c.entries) == n_m0 + 1 and not c.selected_entries)

# M переключает режим ЛКМ независимо от раскладки
c.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A,
                          Qt.KeyboardModifier.NoModifier,
                          0x10, 0x4D, 0, "ь"))
app.processEvents()
ok("M включила режим рисования ЛКМ",
   c.mouse_draw and win.mousemode_btn.isChecked())
c.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A,
                          Qt.KeyboardModifier.NoModifier,
                          0x10, 0x4D, 0, "ь"))
app.processEvents()
ok("M вернула режим выделения ЛКМ",
   not c.mouse_draw and not win.mousemode_btn.isChecked())

# выбранная толщина подсвечивается
win._set_width(8)
app.processEvents()
ok("выбранная толщина подсвечена",
   "border: 2px" in win.width_buttons[8.0].styleSheet()
   and all("border: 2px" not in btn.styleSheet()
           for w, btn in win.width_buttons.items() if w != 8.0))
win._set_width(5)
app.processEvents()

# ---------- 8.7. стрелка: древко укорочено под наконечником ----------
_p1, _p2 = QPointF(0, 0), QPointF(100, 0)
_end = Canvas._shaft_end(_p1, _p2, 5)
ok("древко стрелки не доходит до острия",
   _end is not None and 0 < _end.x() < _p2.x())
ok("древко укорочено примерно на длину наконечника",
   abs((_p2.x() - _end.x()) - (Canvas._arrowhead_len(5) - 2.5)) < 1e-6)
ok("линия короче наконечника рисуется без древка",
   Canvas._shaft_end(_p1, QPointF(5, 0), 5) is None)

# ---------- 8.8. запуск без Windows WinTab (режим macOS) ----------
_macos_script = r'''
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
app = QApplication([])
sys.platform = "darwin"
import doska_qt
w = doska_qt.MainWindow()
assert w.canvas.wintab is None
assert "Планшет через Qt" in w.statusBar().currentMessage()
print("macos-smoke-ok")
w.close()
'''
_res = subprocess.run(
    [sys.executable, "-c", _macos_script],
    cwd=os.path.dirname(os.path.abspath(__file__)),
    capture_output=True, text=True, timeout=20)
if _res.returncode != 0:
    print(_res.stderr)
ok("код запускается в режиме macOS без WinTab",
   _res.returncode == 0 and "macos-smoke-ok" in _res.stdout)

# ---------- 8.9. Ctrl+C/Ctrl+V: фигуры и надписи, undo/redo ----------
_cp_line = {"kind": "line",
            "p1": QPointF(7980, 5990), "p2": QPointF(8020, 5990),
            "color": QColor("#2b5fd9"), "width": 5,
            "dash": True, "arrow": False}
_cp_text = {"kind": "text", "pos": QPointF(7985, 6015),
            "text": "Формула", "size": 24.0, "latex": False,
            "color": QColor("#1a1a1a")}
c.entries.extend((_cp_line, _cp_text))
c._rebuild_layers()
c.selected_entries = [_cp_line, _cp_text]
ok("выделенные фигуры и надпись копируются", c.copy_selected())
_boxes = [c._entry_bbox(e) for e in (_cp_line, _cp_text)]
_old_cx = ((min(b[0] for b in _boxes) + max(b[2] for b in _boxes)) / 2.0)
_old_cy = ((min(b[1] for b in _boxes) + max(b[3] for b in _boxes)) / 2.0)
c.zoom = 1.0
c.offset = QPointF(_old_cx - c.width() / 2.0,
                   _old_cy - c.height() / 2.0)
n_cp = len(c.entries)
ok("вставка скопированного выделения", c.paste())
app.processEvents()
_new_cp = list(c.selected_entries)
ok("вставлены независимые копии объектов",
   len(_new_cp) == 2
   and all(e in c.entries for e in _new_cp)
   and all(ne is not oe for ne, oe in zip(_new_cp, (_cp_line, _cp_text)))
   and layers_match_entries())
_boxes = [c._entry_bbox(e) for e in _new_cp]
_new_cx = ((min(b[0] for b in _boxes) + max(b[2] for b in _boxes)) / 2.0)
_new_cy = ((min(b[1] for b in _boxes) + max(b[3] for b in _boxes)) / 2.0)
_vr = c._visible_scene_rect()
ok("вставленная копия находится в видимой области",
   _vr.left() <= _new_cx <= _vr.right()
   and _vr.top() <= _new_cy <= _vr.bottom())
ok("вставленная копия не совпадает с оригиналом",
   math.hypot(_new_cx - _old_cx, _new_cy - _old_cy) > 1e-6)
ok("пунктир сохранился при копировании",
   _new_cp[0]["kind"] == "line" and _new_cp[0].get("dash") is True)
c.undo()
app.processEvents()
ok("undo убрал вставленную копию",
   len(c.entries) == n_cp and layers_match_entries())
c.redo()
app.processEvents()
ok("redo вернул вставленную копию",
   len(c.entries) == n_cp + 2
   and all(e in c.entries for e in _new_cp)
   and layers_match_entries())
c.undo()
app.processEvents()
ok("повторный undo снова убрал вставку",
   len(c.entries) == n_cp and layers_match_entries())
c.entries.remove(_cp_line)
c.entries.remove(_cp_text)
c.selected_entries = []
c._rebuild_layers()
c.redo_stack.clear()

# ---------- 8.10. Ctrl+V: внешнее изображение замещает скопированное ----------
_cp2 = {"kind": "line",
        "p1": QPointF(7980, 5990), "p2": QPointF(8020, 5990),
        "color": QColor("#2b5fd9"), "width": 5,
        "dash": False, "arrow": False}
c.entries.append(_cp2)
c._rebuild_layers()
c.selected_entries = [_cp2]
ok("копирование выделения перед внешней вставкой", c.copy_selected())
_img2 = QImage(80, 60, QImage.Format.Format_RGB32)
_img2.fill(QColor("#2b9e44"))
QGuiApplication.clipboard().setImage(_img2)   # копирование во внешнем приложении
app.processEvents()
ok("внешнее изображение заместило скопированное выделение",
   not c.clipboard_entries)
_n_before = len(c.entries)
ok("Ctrl+V теперь вставляет внешнее изображение", bool(c.paste()))
app.processEvents()
ok("вставлена именно картинка, а не копия выделения",
   len(c.entries) == _n_before + 1
   and c.entries[-1]["kind"] == "image"
   and layers_match_entries())
c.undo()
app.processEvents()
c.entries.remove(_cp2)
c.selected_entries = []
c.selected_image = None
c._rebuild_layers()
c.redo_stack.clear()

# ---------- 8.11. «Пунктир» действует на графики ----------
win.dash_btn.setChecked(True)
ok("график строится пунктирным",
   c.add_graph("x^2/10")
   and c.entries[-1]["kind"] == "curve"
   and c.entries[-1].get("dash") is True
   and layers_match_entries())
c.undo()
app.processEvents()
win.dash_btn.setChecked(False)
ok("без «Пунктира» график сплошной",
   c.add_graph("x^2/10")
   and c.entries[-1]["kind"] == "curve"
   and c.entries[-1].get("dash") is False)
c.undo()
app.processEvents()

# ---------- 8.12. сглаживание: ядро 0.25/0.5/0.25, нажатие — среднее трёх ----------
_pts = [(0.0, 0.0, 1.0), (10.0, 0.0, 1.0), (10.0, 10.0, 1.0)]
_sm = _smooth_points(_pts)
ok("геометрия сглажена ядром 0.25/0.5/0.25, концы закреплены",
   _sm[0] == (0.0, 0.0, 1.0)
   and abs(_sm[1][0] - 7.5) < 1e-9 and abs(_sm[1][1] - 2.5) < 1e-9
   and _sm[2] == (10.0, 10.0, 1.0))
_pf = [(float(i), 0.0, f) for i, f in enumerate([0.0, 0.0, 1.0, 0.0, 0.0])]
_smf = _smooth_points(_pf)
ok("нажатие — среднее трёх соседних точек, концы не тронуты",
   abs(_smf[2][2] - 1.0 / 3.0) < 1e-9
   and _smf[0][2] == 0.0 and _smf[-1][2] == 0.0)

# ---------- 8.13. курсор: меньше на 15%, размер зависит от толщины ----------
c.set_width(2)
_img_s = c._make_pen_cursor().pixmap().toImage()
c.set_width(16)
_img_l = c._make_pen_cursor().pixmap().toImage()
ok("размер курсора растёт вместе с толщиной кисти",
   _img_s.pixelColor(16, 27).alpha() == 0
   and _img_l.pixelColor(16, 27).alpha() > 0)
c.set_width(5)
_img_d = c._make_pen_cursor().pixmap().toImage()
ok("курсор при толщине по умолчанию стал компактнее",
   _img_d.pixelColor(16, 28).alpha() == 0)
c.set_width(5)
app.processEvents()

# ---------- 8.14. штрих мышью завершается корректно (эталонный конвейер) ----------
c.zoom = 1.0
c.offset = QPointF(7400, 5700)
win.mousemode_btn.setChecked(True)
sp0 = QPointF(7600, 5900)
c._wt_hover_last = 0
c.mousePressEvent(mev(QEvent.Type.MouseButtonPress, sp0,
                      Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
app.processEvents()
sp = sp0
for i in range(1, 40):
    sp = QPointF(7600 + i * 14, 5900 + 80 * math.sin(i * 0.45))
    c.mouseMoveEvent(mev(QEvent.Type.MouseMove, sp,
                         Qt.MouseButton.NoButton,
                         Qt.MouseButton.LeftButton))
    app.processEvents()
_n_before_release = len(c.entries)
c.mouseReleaseEvent(mev(QEvent.Type.MouseButtonRelease, sp,
                        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
app.processEvents()
ok("штрих завершён, слои соответствуют записям",
   len(c.entries) == _n_before_release + 1
   and c.entries[-1]["kind"] == "stroke"
   and layers_match_entries())
c.undo()
app.processEvents()
win.mousemode_btn.setChecked(False)

# ---------- 8.14b. синтетика мыши от драйвера пера подавляется ----------
# перо идёт через Qt tabletEvent: драйвер Wacom вслед шлёт дубли мышью —
# они не должны начинать второй жест (иначе resize дёргается между двумя
# потоками позиций и изображение «моргает»)
c._wt_hover_last = 0
c._tab_last = time.time()   # перо только что было активно через Qt
ok("свежие события пера включают подавление мыши", c._pen_emulation())
n_tb = len(c.entries)
c.mousePressEvent(mev(QEvent.Type.MouseButtonPress, QPointF(5600, 5600),
                      Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
c.mouseReleaseEvent(mev(QEvent.Type.MouseButtonRelease, QPointF(5600, 5600),
                        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
app.processEvents()
ok("синтетика мыши во время работы пера проигнорирована",
   c.mode is None and len(c.entries) == n_tb and not c.selected_entries)
c._tab_last = 0.0
ok("без пера мышь снова работает", not c._pen_emulation())

# ---------- 8.15. resize изображения: preview не трогает pixmap до release ----------
n_pre = len(c.entries)   # записи 8.15-8.18 потом уберём: иначе область
                         # экспорта PNG превышает лимит QImageReader в тесте
c.zoom = 1.0
c.offset = QPointF(9000, 8000)
c.update()
img_pv = QImage(160, 100, QImage.Format.Format_RGB32)
img_pv.fill(QColor("#2b5fd9"))
QGuiApplication.clipboard().setImage(img_pv)
c.cursor_scene = QPointF(9300, 8300)
c.paste_image()
app.processEvents()
e_pv = c.selected_image
pw0, ph0 = e_pv["pix"].width(), e_pv["pix"].height()
sh = local_to_scene(e_pv, QPointF(pw0 / 2.0, ph0 / 2.0))
c._wt_hover_last = 0
c.mousePressEvent(mev(QEvent.Type.MouseButtonPress, sh,
                      Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
mv = local_to_scene(e_pv, QPointF(pw0 / 2.0 + 40, ph0 / 2.0 + 25))
c.mouseMoveEvent(mev(QEvent.Type.MouseMove, mv,
                     Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
app.processEvents()
ok("во время resize pixmap не пересоздаётся (preview)",
   "_pv" in e_pv
   and e_pv["pix"].width() == pw0 and e_pv["pix"].height() == ph0)
c.mouseReleaseEvent(mev(QEvent.Type.MouseButtonRelease, mv,
                        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
app.processEvents()
ok("после release применён финальный размер",
   "_pv" not in e_pv
   and e_pv["pix"].width() > pw0 and e_pv["pix"].height() > ph0
   and layers_match_entries())

# ---------- 8.15b. resize: неподвижный курсор не дёргает размер ----------
# регрессия: масштаб считался от текущего preview-размера, центр картинки
# смещался вместе с ним, и при неподвижном курсоре размер осциллировал
# между двумя значениями (S -> 2d - S) — изображение «моргало»
img_big = QImage(1000, 600, QImage.Format.Format_RGB32)
img_big.fill(QColor("#2b9e44"))
QGuiApplication.clipboard().setImage(img_big)
c.cursor_scene = QPointF(9700, 8600)
c.paste_image()
app.processEvents()
e_big = c.selected_image
bw0, bh0 = e_big["pix"].width(), e_big["pix"].height()
w2b, h2b = bw0 / 2.0, bh0 / 2.0
sh = local_to_scene(e_big, QPointF(w2b, h2b))
c._wt_hover_last = 0
c.mousePressEvent(mev(QEvent.Type.MouseButtonPress, sh,
                      Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
# тянем наружу и ДЕРЖИМ курсор на месте: каждый move — в ту же точку сцены
mv = local_to_scene(e_big, QPointF(w2b + 100, h2b + 60))
sizes = []
for i in range(6):
    c.mouseMoveEvent(mev(QEvent.Type.MouseMove, mv,
                         Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
    app.processEvents()
    sizes.append(c._img_size(e_big))
ok("неподвижный курсор: размер не осциллирует",
   len(sizes) >= 2 and all(sz == sizes[0] for sz in sizes))
# мелкий шум курсора вокруг точки: размер меняется плавно, без скачков
noisy = []
for i in range(6):
    mv2 = QPointF(mv.x() + (i % 2), mv.y() + ((i + 1) % 2))
    c.mouseMoveEvent(mev(QEvent.Type.MouseMove, mv2,
                         Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
    app.processEvents()
    noisy.append(c._img_size(e_big))
ok("шум курсора в 1 px даёт разброс размера < 3 px",
   max(s[0] for s in noisy) - min(s[0] for s in noisy) < 3
   and max(s[1] for s in noisy) - min(s[1] for s in noisy) < 3)
c.mouseReleaseEvent(mev(QEvent.Type.MouseButtonRelease, mv,
                        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
app.processEvents()
s_expect = (w2b + 100) / w2b
ok("финальный размер соответствует оси x",
   abs(e_big["pix"].width() - bw0 * s_expect) <= 2
   and abs(e_big["pix"].height() - bh0 * s_expect) <= 2
   and layers_match_entries())
c.undo()   # убрать большое изображение
app.processEvents()

# ---------- 8.16. график без осей при zoom out ограничен, а не на весь экран ----------
c.entries = [en for en in c.entries if en["kind"] != "axes"]
c._rebuild_layers()
c.zoom = 0.2
c.offset = QPointF(6000, 4500)
c.update()
ok("график без осей при zoom 0.2 не разъезжается на весь экран",
   c.add_graph("x^2/10")
   and c.entries[-1]["kind"] == "curve"
   and (max(px for seg in c.entries[-1]["segs"] for (px, _py) in seg)
        - min(px for seg in c.entries[-1]["segs"] for (px, _py) in seg)) <= 1900
   and layers_match_entries())
c.undo()
app.processEvents()
c.zoom = 1.0
c.update()

# ---------- 8.17. порог нажатия: почти оторванное перо завершает штрих ----------
c.offset = QPointF(11000, 8500)
c.update()
n0 = len(c.entries)
tp = [QPointF(11200 + i * 14, 8700 + 20 * math.sin(i / 2.0)) for i in range(8)]
pen_at(tp[0], 0.6)
for sp in tp[1:]:
    pen_at(sp, 0.6)
ok("штрих идёт при нормальном нажатии", c._wt_mode == "stroke")
pen_at(tp[-1] + QPointF(14, 4), 0.03)   # нажатие ниже 5% — конец штриха
ok("нажатие < 5% завершило штрих без хвоста",
   c._wt_mode is None and len(c.entries) == n0 + 1
   and c.entries[-1]["kind"] == "stroke"
   and layers_match_entries())
pen_at(tp[-1] + QPointF(28, 8), 0.03)   # слабый контакт не начинает новый штрих
ok("слабый контакт не начинает новый штрих",
   c._wt_mode is None and len(c.entries) == n0 + 1)
pen_done()
ok("после прохода слой соответствует записям", layers_match_entries())
c.undo()
app.processEvents()

# ---------- 8.18. ластик локален: чужие плитки не пересоздаются ----------
c.offset = QPointF(11800, 9000)
c.update()
ptsA = [QPointF(12000 + i * 12, 9100 + 18 * math.sin(i / 2.0)) for i in range(10)]
stroke_pts(ptsA)
ptsB = [QPointF(12700 + i * 12, 9800 + 18 * math.sin(i / 2.0)) for i in range(10)]
stroke_pts(ptsB)
lay = c.layers[-1]
ids_before = {k: id(pm) for k, pm in lay["tl"].tiles.items()}
c._erase_last = None
c._erase_speed = 0.0
c.erase_at(QPointF(12060, 9100))   # режем только штрих A
app.processEvents()
ids_after = {k: id(pm) for k, pm in lay["tl"].tiles.items()}
bxA = c._eb(c.entries[-2]) if c.entries[-2]["kind"] == "stroke" else (0, 0, 1, 1)
far_keys = [k for k in ids_before
            if k[1] * 512 > 9300]   # плитки штриха B ниже области ластика
ok("ластик не пересоздаёт плитки вне своей области",
   far_keys and all(ids_after.get(k) == ids_before[k] for k in far_keys))
ok("слой == entries после локального стирания", layers_match_entries())

# убрать далёкие записи тестов 8.15-8.18: область экспорта PNG должна
# остаться в пределах лимита QImageReader (иначе финальная проверка
# сохранения не сможет перечитать файл)
del c.entries[n_pre:]
c.selected_image = None
c._rebuild_layers()
ok("слой == entries после уборки", layers_match_entries())

# ---------- 8.19. фаза пунктира: разрезание не сдвигает штриховку ----------
c.zoom = 1.0
c.offset = QPointF(3000, 9000)
c.update()
win.dash_btn.setChecked(True)
c.mode = "line"
c.line_anchor = QPointF(3100, 9100)
c._line_snap = False
c._line_arrow = False
c._finish_input(QPointF(3900, 9100))
win.dash_btn.setChecked(False)
dash_e = c.entries[-1]
tl0 = StrokeLayer()
tl0.draw(c._entry_bbox(dash_e), lambda p: c._render_line(p, dash_e))
c._erase_last = None
c._erase_speed = 0.0
c.erase_at(QPointF(3300, 9100))
app.processEvents()
ok("пунктирная линия разрезана ластиком",
   dash_e not in c.entries and layers_match_entries())
tk = (int(3700) // 512, int(9100) // 512)
ok("фаза пунктира не прыгает при стирании",
   tk in c.layers[-1]["tl"].tiles
   and pix_bytes(c.layers[-1]["tl"].tiles[tk]) == pix_bytes(tl0.tiles[tk]))
c.undo()
app.processEvents()

# ---------- 8.20. смерть redo-ветки сбрасывает кэш рамок ----------
c._bbox_cache[id(c.entries[-1])] = (0.0, 0.0, 1.0, 1.0)
c._push_undo(("add", c.entries[-1]))
ok("push_undo сбросил кэш рамок вместе с redo-веткой", not c._bbox_cache)
c.undo()
app.processEvents()
c.redo()
app.processEvents()
ok("слой == entries после пары undo/redo", layers_match_entries())

# ---------- 8.21. окружность рисуется выбранной толщиной и цветом ----------
# регрессия: setPen в _render_curve вызывался только для пунктира, поэтому
# сплошные кривые рисовались стандартным пером Qt (чёрный волос 1 px)
c.set_color(QColor("#d92b2b"))
c.set_width(8)
win.circle_btn.setChecked(True)
app.processEvents()
cc = QPointF(6000, 6000)
mouse_line(cc, cc + QPointF(120, 0), Qt.KeyboardModifier.NoModifier)
_circ8 = c.entries[-2]
ok("окружность сохранила выбранные толщину и цвет",
   _circ8["kind"] == "curve" and _circ8["width"] == 8.0
   and _circ8["color"] == QColor("#d92b2b"))
pm = QPixmap(400, 400)
pm.fill(Qt.GlobalColor.transparent)
_p = QPainter(pm)
_p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
_p.translate(-(6000 - 200), -(6000 - 200))
c._render_curve(_p, _circ8)
_p.end()
_img = pm.toImage().convertToFormat(QImage.Format.Format_ARGB32)
_run = _best = 0
for _x in range(308, 333):
    _run = _run + 1 if _img.pixelColor(_x, 200).alpha() > 100 else 0
    _best = max(_best, _run)
ok("кольцо рисуется выбранной толщиной, а не волосом", 6 <= _best <= 11)
_core = _img.pixelColor(320, 200)
ok("кольцо рисуется выбранным цветом",
   _core.alpha() > 100 and _core.red() > 150 and _core.green() < 120)
c.undo()   # убрать окружность вместе с точкой центра
app.processEvents()
win.circle_btn.setChecked(False)
app.processEvents()
c.set_color(QColor("#1a1a1a"))
c.set_width(5)
app.processEvents()
ok("слой == entries после уборки окружности", layers_match_entries())

# ---------- 8.22. ластик у прямых и стрелок: режет, а не удаляет целиком ----------
_arr = {"kind": "line", "p1": QPointF(4000, 6000), "p2": QPointF(4120, 6000),
        "color": QColor("#1a1a1a"), "width": 5, "dash": False, "arrow": True}
c.entries.append(_arr)
c._rebuild_layers()


def _arrow_frags():
    return [e for e in c.entries if e["kind"] == "line"
            and (e["p1"] == _arr["p1"] or e["p2"] == _arr["p2"])]


# стирание у начала стрелки: хвост с наконечником обязан уцелеть
c._erase_last = None
c._erase_speed = 0.0
c.erase_at(QPointF(4006, 6000))
app.processEvents()
_fr = _arrow_frags()
ok("ластик у начала стрелки оставил хвост с наконечником",
   _arr not in c.entries and len(_fr) == 1 and _fr[0].get("arrow") is True
   and _fr[0]["p2"] == _arr["p2"] and _fr[0]["p2"] is _arr["p2"]
   and _fr[0]["p1"].x() > 4006
   and layers_match_entries())
c.undo()
app.processEvents()
ok("undo вернул стрелку", _arr in c.entries and layers_match_entries())

# стирание посередине: два куска, наконечник на хвосте
c._erase_last = None
c._erase_speed = 0.0
c.erase_at(QPointF(4060, 6000))
app.processEvents()
_fr = _arrow_frags()
ok("ластик посередине разрезал стрелку на два куска",
   _arr not in c.entries and len(_fr) == 2 and layers_match_entries())
ok("наконечник остался на куске с исходным концом",
   sum(1 for e in _fr if e.get("arrow")) == 1
   and any(e.get("arrow") and e["p2"] == _arr["p2"] for e in _fr))
c.undo()
app.processEvents()

# стирание у самого конца: удаляется только кончик
c._erase_last = None
c._erase_speed = 0.0
c.erase_at(QPointF(4115, 6000))
app.processEvents()
_fr = _arrow_frags()
ok("ластик у конца стрелки удалил только кончик",
   len(_fr) == 1 and _fr[0]["p1"] == _arr["p1"] and _fr[0]["p2"].x() > 4100
   and not _fr[0].get("arrow") and layers_match_entries())
c.undo()
app.processEvents()

# касание ровно по границе круга: линия не тронута
c._erase_last = None
c._erase_speed = 0.0
c.erase_at(QPointF(4060, 6000 + ERASER_BASE + _arr["width"] / 2))
app.processEvents()
ok("касание по границе круга не трогает линию",
   _arr in c.entries and layers_match_entries())

# центр ластика ровно на линии у конца: режется, а не удаляется целиком
c._erase_last = None
c._erase_speed = 0.0
c.erase_at(QPointF(4000 + ERASER_BASE + _arr["width"] / 2, 6000))
app.processEvents()
_fr = _arrow_frags()
ok("ластик на самой линии режет её, а не удаляет целиком",
   _arr not in c.entries and len(_fr) == 1
   and _fr[0].get("arrow") is True and layers_match_entries())
c.undo()
app.processEvents()
c.entries.remove(_arr)
c.selected_entries = []
c._rebuild_layers()
c.redo_stack.clear()
ok("слой == entries после уборки стрелки", layers_match_entries())

# ---------- 8.23. улучшения по обзору кода (пункты 1-11) ----------
import doska_qt  # noqa: E402

# кнопка ластика синхронизирована с режимом: выбор цвета гасит и её
win.eraser_btn.setChecked(True)
app.processEvents()
ok("кнопка включила режим ластика", c.eraser_tool)
c.set_color(QColor("#2b5fd9"))
app.processEvents()
ok("выбор цвета выключил ластик и его кнопку",
   not c.eraser_tool and not win.eraser_btn.isChecked())
c.set_color(QColor("#1a1a1a"))

# «Очистить» — обычное действие с отменой, а не сброс истории
_n_before = len(c.entries)
_q = doska_qt.QMessageBox.question
doska_qt.QMessageBox.question = staticmethod(
    lambda *a, **k: doska_qt.QMessageBox.StandardButton.Yes)
try:
    c.clear()
finally:
    doska_qt.QMessageBox.question = _q
app.processEvents()
ok("очистка убрала все записи", not c.entries and not c.layers)
c.undo()
app.processEvents()
ok("очистка отменяется по Ctrl+Z",
   len(c.entries) == _n_before and layers_match_entries())
c.redo_stack.clear()

# Ctrl+A — выделить все записи
c.select_all()
ok("select_all выделяет все записи",
   len(c.selected_entries) == len(c.entries) and c.selected_image is None)
c.selected_entries = []

# вставка огромного изображения: позиция прижимается к сцене
c.zoom = 0.05   # атрибутом, минуя clamp колёсика: вид огромен
_big_img = QImage(17000, 50, QImage.Format.Format_RGB32)
_big_img.fill(QColor("#d92b2b"))
QGuiApplication.clipboard().setImage(_big_img)
app.processEvents()
ok("огромное изображение вставлено", c.paste_image())
_big = c.entries[-1]
ok("позиция огромного изображения прижата к краю сцены",
   _big["pos"].x() == 0.0 and _big["pos"].y() >= 0.0)
c.undo()
app.processEvents()
c.zoom = 1.0

# _clear_redo: при пустом redo кэш рамок сохраняется, после undo — сбрасывается
c.redo_stack.clear()
_e0 = c.entries[-1]
c._eb(_e0)
ok("кэш рамок заполнен", id(_e0) in c._bbox_cache)
stroke_pts([QPointF(8400, 5900), QPointF(8420, 5900), QPointF(8440, 5900)])
ok("push при пустом redo сохранил кэш рамок", id(_e0) in c._bbox_cache)
c.undo()
app.processEvents()
c._eb(_e0)
stroke_pts([QPointF(8400, 5920), QPointF(8420, 5920), QPointF(8440, 5920)])
ok("push после undo убил redo-ветку и кэш рамок",
   not c.redo_stack and id(_e0) not in c._bbox_cache)
c.undo()
app.processEvents()
c.redo_stack.clear()
ok("слой == entries после проверки _clear_redo", layers_match_entries())

# график рядом с осями, далеко за экраном: сообщение предупреждает
_old_off = QPointF(c.offset)
c.offset = QPointF(2000, 2000)
c.add_axes()   # оси — опора графика; вид уводим далеко от них
app.processEvents()
c.offset = QPointF(14000, 10000)
ok("график построен", c.add_graph("x"))
ok("сообщение предупреждает о графике вне видимой области",
   "вне видимой области" in win.statusBar().currentMessage())
c.undo()   # убрать график
c.undo()   # убрать оси
app.processEvents()
c.offset = _old_off
ok("слой == entries после графика вне вида", layers_match_entries())

# синтаксическая ошибка в формуле: сообщение содержит текст ошибки
ok("битая формула отклонена", not c.add_graph("x +"))
ok("сообщение об ошибке содержит формулу и текст ошибки",
   "«x+»" in win.statusBar().currentMessage())

# страж save_png: чудовищный растр не создаётся
_far1 = {"kind": "stroke", "pts": [(100.0, 100.0, 1.0)],
         "color": QColor("#1a1a1a"), "width": 5}
_far2 = {"kind": "stroke", "pts": [(15900.0, 11900.0, 1.0)],
         "color": QColor("#1a1a1a"), "width": 5}
c.entries.extend((_far1, _far2))
_warn = []
_w = doska_qt.QMessageBox.warning
doska_qt.QMessageBox.warning = staticmethod(
    lambda *a, **k: _warn.append(a) or 0)
_out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "test_guard.png")
try:
    c.save_png(_out)
finally:
    doska_qt.QMessageBox.warning = _w
ok("слишком большая область не сохраняется, а предупреждает",
   bool(_warn) and not os.path.exists(_out))
c.entries.remove(_far1)
c.entries.remove(_far2)
if os.path.exists(_out):
    os.remove(_out)
ok("слой == entries после стража save_png", layers_match_entries())

# ---------- 8.24. перенос выделенного и изображения отменяется по Ctrl+Z ----------
c.zoom = 1.0
c.offset = QPointF(8000, 6000)
c.update()
stroke_pts([QPointF(8200 + i * 10, 6200) for i in range(4)])
stroke_pts([QPointF(8200 + i * 10, 6260) for i in range(4)])
_mv = list(c.entries[-2:])
_p1 = list(_mv[0]["pts"])
_p2 = list(_mv[1]["pts"])
c.selected_entries = list(_mv)
c.selected_image = None
c.mouse_draw = False
_press = QPointF(8210, 6200)   # внутри рамки первой записи
c._wt_hover_last = 0
c.mousePressEvent(mev(QEvent.Type.MouseButtonPress, _press,
                      Qt.MouseButton.LeftButton,
                      Qt.MouseButton.LeftButton))
ok("нажатие внутри выделенного начало перенос", c.mode == "move_sel")
c.mouseMoveEvent(mev(QEvent.Type.MouseMove, _press + QPointF(60, 40),
                     Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
c.mouseReleaseEvent(mev(QEvent.Type.MouseButtonRelease,
                        _press + QPointF(60, 40),
                        Qt.MouseButton.LeftButton,
                        Qt.MouseButton.NoButton))
app.processEvents()
ok("выделенное перенесено",
   abs(_mv[0]["pts"][0][0] - _p1[0][0] - 60) < 1e-6
   and abs(_mv[0]["pts"][0][1] - _p1[0][1] - 40) < 1e-6
   and abs(_mv[1]["pts"][0][0] - _p2[0][0] - 60) < 1e-6
   and layers_match_entries())
c.undo()
app.processEvents()
ok("Ctrl+Z отменяет перенос выделенного, а не штрихи",
   _mv[0] in c.entries and _mv[1] in c.entries
   and abs(_mv[0]["pts"][0][0] - _p1[0][0]) < 1e-6
   and abs(_mv[0]["pts"][0][1] - _p1[0][1]) < 1e-6
   and abs(_mv[1]["pts"][0][0] - _p2[0][0]) < 1e-6
   and layers_match_entries())
c.redo()
app.processEvents()
ok("Ctrl+Y повторяет перенос выделенного",
   abs(_mv[0]["pts"][0][0] - _p1[0][0] - 60) < 1e-6
   and layers_match_entries())
c.undo()
app.processEvents()
c.selected_entries = []
c.undo()   # убрать второй тестовый штрих
c.undo()   # убрать первый тестовый штрих
app.processEvents()
c.redo_stack.clear()
ok("слой == entries после теста переноса", layers_match_entries())

# перенос изображения мышью — тоже отменяемое действие
c.cursor_scene = QPointF(8300, 6300)
_dimg = QImage(60, 40, QImage.Format.Format_RGB32)
_dimg.fill(QColor("#2b5fd9"))
QGuiApplication.clipboard().setImage(_dimg)
app.processEvents()
ok("изображение вставлено для теста переноса", c.paste_image())
_ie = c.entries[-1]
_ip0 = QPointF(_ie["pos"])
c._wt_hover_last = 0
c.mousePressEvent(mev(QEvent.Type.MouseButtonPress, QPointF(8300, 6300),
                      Qt.MouseButton.LeftButton,
                      Qt.MouseButton.LeftButton))
ok("нажатие на изображение начало перенос", c.mode == "drag")
c.mouseMoveEvent(mev(QEvent.Type.MouseMove, QPointF(8350, 6270),
                     Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
c.mouseReleaseEvent(mev(QEvent.Type.MouseButtonRelease, QPointF(8350, 6270),
                        Qt.MouseButton.LeftButton,
                        Qt.MouseButton.NoButton))
app.processEvents()
ok("изображение перенесено",
   abs(_ie["pos"].x() - _ip0.x() - 50) < 1e-6
   and abs(_ie["pos"].y() - _ip0.y() + 30) < 1e-6)
c.undo()
app.processEvents()
ok("Ctrl+Z отменяет перенос изображения",
   _ie in c.entries and abs(_ie["pos"].x() - _ip0.x()) < 1e-6
   and abs(_ie["pos"].y() - _ip0.y()) < 1e-6 and layers_match_entries())
c.redo()
app.processEvents()
ok("Ctrl+Y повторяет перенос изображения",
   abs(_ie["pos"].x() - _ip0.x() - 50) < 1e-6 and layers_match_entries())
c.undo()
app.processEvents()
c.entries.remove(_ie)
c.selected_image = None
c._rebuild_layers()
c.redo_stack.clear()
ok("слой == entries после теста переноса изображения",
   layers_match_entries())

# ---------- 8.25. resize и поворот изображения отменяются по Ctrl+Z ----------
c.zoom = 1.0
c.offset = QPointF(8000, 6000)
c.update()
c.cursor_scene = QPointF(8400, 6400)
_rimg = QImage(60, 40, QImage.Format.Format_RGB32)
_rimg.fill(QColor("#2b9e44"))
QGuiApplication.clipboard().setImage(_rimg)
app.processEvents()
ok("изображение вставлено для теста отмены resize/поворота", c.paste_image())
_re = c.entries[-1]
ok("размер до resize 60x40",
   _re["pix"].width() == 60 and _re["pix"].height() == 40)

# resize мышью: тянем уголок в полтора раза дальше центра -> 90x60
_rw, _rh = _re["pix"].width(), _re["pix"].height()
_sh = local_to_scene(_re, QPointF(_rw / 2.0, _rh / 2.0))
c._wt_hover_last = 0
c.mousePressEvent(mev(QEvent.Type.MouseButtonPress, _sh,
                      Qt.MouseButton.LeftButton,
                      Qt.MouseButton.LeftButton))
_mv = local_to_scene(_re, QPointF(_rw / 2.0 * 1.5, _rh / 2.0 * 1.5))
c.mouseMoveEvent(mev(QEvent.Type.MouseMove, _mv,
                     Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
c.mouseReleaseEvent(mev(QEvent.Type.MouseButtonRelease, _mv,
                        Qt.MouseButton.LeftButton,
                        Qt.MouseButton.NoButton))
app.processEvents()
ok("resize изменил размер до 90x60",
   _re["pix"].width() == 90 and _re["pix"].height() == 60)
c.undo()
app.processEvents()
ok("Ctrl+Z отменяет resize: размер снова 60x40",
   _re in c.entries and _re["pix"].width() == 60
   and _re["pix"].height() == 40 and layers_match_entries())
c.redo()
app.processEvents()
ok("Ctrl+Y повторяет resize: размер снова 90x60",
   _re["pix"].width() == 90 and _re["pix"].height() == 60
   and layers_match_entries())

# поворот мышью за ручку: указатель вправо от центра -> snap к 90
_rh = local_to_scene(_re, QPointF(0, -_re["pix"].height() / 2.0
                                  - 28.0 / c.zoom))
c._wt_hover_last = 0
c.mousePressEvent(mev(QEvent.Type.MouseButtonPress, _rh,
                      Qt.MouseButton.LeftButton,
                      Qt.MouseButton.LeftButton))
_rcx, _rcy = c._image_center(_re)
_tgt = QPointF(_rcx + 50, _rcy)
c.mouseMoveEvent(mev(QEvent.Type.MouseMove, _tgt,
                     Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
c.mouseReleaseEvent(mev(QEvent.Type.MouseButtonRelease, _tgt,
                        Qt.MouseButton.LeftButton,
                        Qt.MouseButton.NoButton))
app.processEvents()
ok("поворот прилип к 90", abs(_re.get("angle", 0.0) - 90.0) < 1e-9)
c.undo()
app.processEvents()
ok("Ctrl+Z отменяет поворот: угол снова 0",
   abs(_re.get("angle", 0.0)) < 1e-9 and layers_match_entries())
c.redo()
app.processEvents()
ok("Ctrl+Y повторяет поворот: угол снова 90",
   abs(_re.get("angle", 0.0) - 90.0) < 1e-9 and layers_match_entries())

c.entries.remove(_re)
c.selected_image = None
c._rebuild_layers()
c.redo_stack.clear()
app.processEvents()
ok("слой == entries после теста отмены resize/поворота",
   layers_match_entries())

# ---------- 9. сохранение PNG (со штрихами, картинкой, осями, графиком) ----------
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_out.png")
c.save_png(out)
ok("PNG сохранён", os.path.exists(out) and not QImage(out).isNull())
if os.path.exists(out):
    os.remove(out)

print("\nВСЕ ПРОВЕРКИ ПРОШЛИ:", len(PASS))
win.close()
app.processEvents()
