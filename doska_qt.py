# -*- coding: utf-8 -*-
"""
Доска (Whiteboard) для Windows 10 и macOS — Qt/PySide6.
В Windows перо Wacom дополнительно обрабатывается через WinTab; на macOS
используется штатный ввод планшета через QTabletEvent.

- фон в клетку, рабочая зона 16000x12000, стартовый вид — центр зоны;
- масштаб: Ctrl + колёсико мыши (привязка к курсору), Ctrl+0 — сброс к центру;
- перемещение области: ПКМ на пустом месте (или средняя кнопка);
- ластик: обратный конец пера Wacom (WinTab), ПКМ по штриху, Alt, клавиша E;
- нажатие пера влияет на толщину; нормальное нажатие = толщина мыши;
- живое сглаживание: линия гладкая сразу во время письма;
- прямая линия при зажатом Shift; зажатая Q — отрезок со стрелкой
  на конце; Shift+Ctrl и Q+Ctrl — концы прилипают к узлам сетки;
- Ctrl+V — вставка изображения; писать можно ПОВЕРХ изображений;
- левый клик по изображению — выбор (рамка); перетаскивание — перемещение;
  уголок рамки — изменение размера;
- левый клик по изображению — выбор (рамка); перетаскивание — перемещение;
  уголок рамки — пропорциональное изменение размера; ручка сверху — поворот
  с прилипанием к 90°;
- ЛКМ на пустом месте — рамка выделения записей; выделенное перемещается
  перетаскиванием и удаляется по Del; переключатель «ЛКМ: …» (M)
  переводит левую кнопку в режим рисования;
- кнопка «Оси» — координатные оси с делениями по клеткам (начало — в узле
  сетки); кнопка «График» — график f(x) или кривая по уравнению
  (x^2 + y^2 = 25); степень — ^ или **; x, y — в клетках от осей;
- Ctrl+Z — отмена, Ctrl+Y — повтор, Ctrl+C/Ctrl+V — копировать/вставить
  выделенные записи; если в буфере обмена появляется внешнее
  изображение, оно замещает скопированное выделение;
  Ctrl+S — сохранить PNG, Ctrl+N — очистить;
- клавиши 1–4 — цвета; толщина — кнопками на панели;
- курсор показывает выбранный цвет и толщину кисти.
"""

import ctypes
from ctypes import wintypes
import math
import os
import re
import sys
import time

from PySide6.QtCore import QPoint, QPointF, QRectF, QSizeF, Qt, QTimer
from PySide6.QtGui import (
    QColor, QCursor, QFont, QFontMetricsF, QGuiApplication, QIcon, QImage,
    QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QPolygonF,
    QShortcut, QTabletEvent,
)
from PySide6.QtWidgets import (
    QApplication, QComboBox, QColorDialog, QDialog, QDialogButtonBox,
    QFileDialog, QHBoxLayout, QInputDialog, QLabel, QMainWindow,
    QMessageBox, QPlainTextEdit, QToolBar, QPushButton, QRadioButton,
    QVBoxLayout, QWidget,
)

SCENE_W, SCENE_H = 16000, 12000   # рабочая зона
TILE = 512                        # размер плитки слоя штрихов
GRID_STEP = 40
GRID_COLOR = QColor("#dcdcdc")
BG_COLOR = QColor("white")
OUTSIDE_COLOR = QColor("#c9c9c9")
ERASER_RADIUS = 14      # hit-test ПКМ (решение «ластик или панорама»)
ERASER_BASE = 10.0        # базовый радиус ластика в покое (сцена, px)
ERASER_MAX = 60.0         # максимальный радиус на высокой скорости
ERASER_SPEED_LO = 600.0   # ниже этой скорости (px/с) радиус всегда базовый:
                          # медленное ведение стирает точно, а не широко
ERASER_SPEED_HI = 3000.0  # с этой скорости радиус достигает максимума
MIN_POINT_DIST = 1.2
MIN_STROKE_W = 0.3
# нажатие ниже порога завершает штрих (почти оторванное перо не «дописывает»
# хвост): порог против случайных микродвижений в конце росчерка
PRESS_MIN_DRAW = 0.05
ZOOM_MIN, ZOOM_MAX = 0.1, 5.0

STROKE_KINDS = ("stroke", "line", "axes", "curve", "text")  # в плиточных слоях

DEFAULT_COLOR = QColor("#1a1a1a")
DEFAULT_WIDTH = 5

VERSION = "3.1"

QUICK_COLORS = [
    ("1", "#1a1a1a"),
    ("2", "#d92b2b"),
    ("3", "#2b5fd9"),
    ("4", "#2b9e44"),
]

# ================= WinAPI / WinTab =================

WM_POINTERUPDATE = 0x0245
WM_POINTERDOWN = 0x0246
WM_POINTERUP = 0x0247
PEN_FLAG_ERASER = 0x00000004

WT_PACKET = 0x7FF0
WT_PROXIMITY = 0x7FF5
WTI_DEFSYSCTX = 4
WTI_DEVICES = 100
DVC_NPRESSURE = 11
CXO_MESSAGES = 0x0004
CXO_SYSTEM = 0x0100
PK_STATUS = 0x0002
PK_CURSOR = 0x0020
PK_BUTTONS = 0x0040
PK_X = 0x0080
PK_Y = 0x0100
PK_NORMAL_PRESSURE = 0x0400
TPS_INVERT = 0x0010


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _POINTER_INFO(ctypes.Structure):
    _fields_ = [
        ("pointerType", wintypes.DWORD),
        ("pointerId", wintypes.UINT),
        ("frameId", wintypes.UINT),
        ("pointerFlags", wintypes.UINT),
        ("sourceDevice", wintypes.HANDLE),
        ("hwndSource", wintypes.HWND),
        ("ptPixelLocation", _POINT),
        ("ptHimetricLocation", _POINT),
        ("ptPixelLocationRaw", _POINT),
        ("ptHimetricLocationRaw", _POINT),
        ("dwTime", wintypes.DWORD),
        ("historyCount", wintypes.UINT),
        ("InputData", ctypes.c_int32),
        ("dwKeyStates", wintypes.DWORD),
        ("PerformanceCount", ctypes.c_uint64),
        ("ButtonChangeType", ctypes.c_int),
    ]


class _POINTER_PEN_INFO(ctypes.Structure):
    _fields_ = [
        ("pointerInfo", _POINTER_INFO),
        ("penFlags", wintypes.DWORD),
        ("penMask", wintypes.DWORD),
        ("pressure", wintypes.UINT),
        ("rotation", wintypes.UINT),
        ("tiltX", ctypes.c_int16),
        ("tiltY", ctypes.c_int16),
    ]


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_ssize_t),
        ("time", wintypes.DWORD),
        ("pt", _POINT),
    ]


class _LOGCONTEXTA(ctypes.Structure):
    _fields_ = [
        ("lcName", ctypes.c_char * 40),
        ("lcOptions", wintypes.UINT),
        ("lcStatus", wintypes.UINT),
        ("lcLocks", wintypes.UINT),
        ("lcMsgBase", wintypes.UINT),
        ("lcDevice", wintypes.UINT),
        ("lcPktRate", wintypes.UINT),
        ("lcPktData", wintypes.UINT),
        ("lcPktMode", wintypes.UINT),
        ("lcMoveMask", wintypes.DWORD),
        ("lcBtnDnMask", wintypes.DWORD),
        ("lcBtnUpMask", wintypes.DWORD),
        ("lcInOrgX", ctypes.c_long), ("lcInOrgY", ctypes.c_long), ("lcInOrgZ", ctypes.c_long),
        ("lcInExtX", ctypes.c_long), ("lcInExtY", ctypes.c_long), ("lcInExtZ", ctypes.c_long),
        ("lcOutOrgX", ctypes.c_long), ("lcOutOrgY", ctypes.c_long), ("lcOutOrgZ", ctypes.c_long),
        ("lcOutExtX", ctypes.c_long), ("lcOutExtY", ctypes.c_long), ("lcOutExtZ", ctypes.c_long),
        ("lcSensX", wintypes.DWORD), ("lcSensY", wintypes.DWORD), ("lcSensZ", wintypes.DWORD),
        ("lcSysMode", wintypes.BOOL),
        ("lcSysOrgX", ctypes.c_int), ("lcSysOrgY", ctypes.c_int),
        ("lcSysExtX", ctypes.c_int), ("lcSysExtY", ctypes.c_int),
        ("lcSysSensX", wintypes.DWORD), ("lcSysSensY", wintypes.DWORD),
    ]


class _AXIS(ctypes.Structure):
    _fields_ = [
        ("axMin", ctypes.c_long),
        ("axMax", ctypes.c_long),
        ("axUnits", wintypes.UINT),
        ("axResolution", wintypes.DWORD),
    ]


class _WTPACKET(ctypes.Structure):
    _fields_ = [
        ("status", wintypes.UINT),
        ("cursor", wintypes.UINT),
        ("buttons", wintypes.DWORD),
        ("x", wintypes.DWORD),
        ("y", wintypes.DWORD),
        ("pressure", wintypes.UINT),
    ]


class WinTabTablet:
    """Прямое подключение к драйверу Wacom через wintab32.dll."""

    def __init__(self, hwnd):
        self.ctx = None
        self.ok = False
        self.max_pressure = 1023
        # WinTab существует только в Windows. На macOS/Linux перо и ластик
        # обрабатываются штатным tabletEvent() Qt.
        if sys.platform != "win32":
            return
        try:
            self.wt = ctypes.WinDLL("wintab32")
        except (AttributeError, OSError):
            return
        try:
            self.wt.WTInfoA.restype = wintypes.UINT
            self.wt.WTInfoA.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p]
            self.wt.WTOpenA.restype = ctypes.c_void_p
            self.wt.WTOpenA.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.BOOL]
            self.wt.WTPacket.restype = wintypes.BOOL
            self.wt.WTPacket.argtypes = [ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p]
            self.wt.WTClose.restype = wintypes.BOOL
            self.wt.WTClose.argtypes = [ctypes.c_void_p]

            if not self.wt.WTInfoA(0, 0, None):
                return

            axis = _AXIS()
            if self.wt.WTInfoA(WTI_DEVICES, DVC_NPRESSURE, ctypes.byref(axis)):
                if axis.axMax > 0:
                    self.max_pressure = axis.axMax

            ctx = _LOGCONTEXTA()
            if not self.wt.WTInfoA(WTI_DEFSYSCTX, 0, ctypes.byref(ctx)):
                return
            ctx.lcOptions |= CXO_MESSAGES | CXO_SYSTEM
            ctx.lcPktData = (PK_STATUS | PK_CURSOR | PK_BUTTONS |
                             PK_X | PK_Y | PK_NORMAL_PRESSURE)
            ctx.lcPktMode = 0
            ctx.lcMoveMask = ctx.lcPktData
            ctx.lcBtnUpMask = ctx.lcBtnDnMask
            self.ctx = self.wt.WTOpenA(wintypes.HWND(int(hwnd)),
                                       ctypes.byref(ctx), True)
            self.ok = bool(self.ctx)
        except Exception:
            self.ok = False

    def process(self, msg_id, wparam, lparam, canvas):
        if msg_id == WT_PACKET:
            pkt = _WTPACKET()
            if self.wt.WTPacket(ctypes.c_void_p(lparam), wparam,
                                ctypes.byref(pkt)):
                is_eraser = bool(pkt.status & TPS_INVERT)
                pressure = pkt.pressure / self.max_pressure
                canvas.on_wintab(pressure, is_eraser)
        elif msg_id == WT_PROXIMITY:
            if (lparam & 0xFFFF) == 0:
                canvas.finish_wintab()

    def close(self):
        if self.ctx:
            try:
                self.wt.WTClose(self.ctx)
            except Exception:
                pass
            self.ctx = None
            self.ok = False


def _update_win_pointer_flag(msg: _MSG):
    state = _update_win_pointer_flag
    if msg.message == WM_POINTERUP:
        state.eraser = False
        return
    if msg.message not in (WM_POINTERDOWN, WM_POINTERUPDATE):
        return
    pointer_id = msg.wParam & 0xFFFF
    info = _POINTER_PEN_INFO()
    try:
        ok = ctypes.windll.user32.GetPointerPenInfo(
            pointer_id, ctypes.byref(info))
    except Exception:
        return
    if ok:
        state.eraser = bool(info.penFlags & PEN_FLAG_ERASER)


_update_win_pointer_flag.eraser = False


# ================= утилиты =================

_FORMULA_TOKEN = re.compile(
    r"\s*(?:(\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)"
    r"|([a-zA-Z_]\w*)|(.))")


def _normalize_formula(s):
    """Подготовить формулу к eval: ^ -> ** и подставить * там, где
    умножение опущено: 4x, 2sin(x), x(x+1), (x-1)(x-3), 2pi."""
    s = s.replace("^", "**")
    tokens = []
    pos = 0
    for m in _FORMULA_TOKEN.finditer(s):
        num, name, other = m.groups()
        if num is not None:
            tokens.append(("num", num))
        elif name is not None:
            if len(name) > 1 and set(name) <= {"x", "y"}:
                tokens.extend(("name", ch) for ch in name)  # xy -> x, y
            else:
                tokens.append(("name", name))
        elif other in "+-*/=(),":
            tokens.append(("op", other))
        else:
            raise ValueError(f"недопустимый символ: {other!r}")
        pos = m.end()
    if s[pos:].strip():
        raise ValueError("не удалось разобрать формулу")
    out = []
    prev = None
    for kind, val in tokens:
        if prev is not None:
            pk, pv = prev
            closes = pk == "num" or pk == "name" or (pk == "op" and pv == ")")
            opens = kind == "num" or kind == "name" or (kind == "op" and val == "(")
            # имя + '(' — вызов функции (sin(x)); x и y — переменные, не функции
            is_call = pk == "name" and pv not in ("x", "y") and val == "("
            if closes and opens and not is_call:
                out.append("*")
        out.append(val)
        prev = (kind, val)
    return "".join(out)


def _dist_to_segment(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(px - x1, py - y1)
    t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def _circle_interval(a, b, cx, cy, r):
    """Параметры (t1, t2) части отрезка a-b, лежащей внутри круга
    (центр, радиус), ограниченные отрезком [0, 1].
    None — отрезок круг не пересекает.
    Считается по знаку квадратного многочлена |a + t(b-a) - c|^2 - r^2,
    без классификации концов через hypot: устойчиво к численным краям
    (конец почти ровно на границе круга не приводит ни к потере всего
    отрезка, ни к ложному пересечению)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    fx, fy = a[0] - cx, a[1] - cy
    A = dx * dx + dy * dy
    if A == 0:  # вырожденный отрезок-точка
        return (0.0, 1.0) if fx * fx + fy * fy < r * r else None
    B = 2.0 * (fx * dx + fy * dy)
    C = fx * fx + fy * fy - r * r
    D = B * B - 4.0 * A * C
    if D <= 0:
        return None
    sq = math.sqrt(D)
    t1 = max(0.0, (-B - sq) / (2 * A))
    t2 = min(1.0, (-B + sq) / (2 * A))
    if t2 - t1 <= 1e-12:
        return None
    return (t1, t2)


def _lerp_pt(a, b, t):
    """Линейная интерполяция точек-кортежей произвольной длины (x, y, ...)."""
    return tuple(a[k] + (b[k] - a[k]) * t for k in range(len(a)))


def _cut_points(pts, cx, cy, r):
    """Вырезать круг (cx, cy, r) из ломаной.
    None — ломаная не задета; иначе список оставшихся кусков (каждый — список
    точек, >= 2 точек; пустой список — ломаная стёрта целиком).
    Концы кусков обрезаются ровно по границе круга."""
    n = len(pts)
    if n == 1:  # одиночная точка — стирается, если попала в круг
        return [] if math.hypot(pts[0][0] - cx, pts[0][1] - cy) < r else None
    touched = False
    runs = []
    cur = []

    def close():
        if len(cur) >= 2:
            runs.append(cur[:])
        cur.clear()

    for i in range(1, n):
        a, b = pts[i - 1], pts[i]
        iv = _circle_interval(a, b, cx, cy, r)
        if iv is None:
            if not cur:
                cur.append(a)
            cur.append(b)
            continue
        touched = True
        t1, t2 = iv
        if t1 > 0.0:                 # начало сегмента уцелело
            if not cur:
                cur.append(a)
            cur.append(_lerp_pt(a, b, t1))
            close()
        else:                        # точка a внутри круга
            close()
        if t2 < 1.0:                 # конец сегмента уцелел — новый кусок
            cur.extend((_lerp_pt(a, b, t2), b))
    close()
    if not touched:
        return None
    return runs


def _cut_points_len(pts, cx, cy, r):
    """Как _cut_points, но каждый кусок — (точки, длина пути вдоль ломаной
    от её начала до начала куска). Длина нужна, чтобы при разрезании
    пунктирной кривой сохранить фазу штриховки."""
    n = len(pts)
    if n == 1:
        return [] if math.hypot(pts[0][0] - cx, pts[0][1] - cy) < r else None
    touched = False
    runs = []
    cur = []
    cur_start = 0.0
    acc = 0.0   # длина пути до pts[i-1]

    def close():
        if len(cur) >= 2:
            runs.append((cur[:], cur_start))
        cur.clear()

    for i in range(1, n):
        a, b = pts[i - 1], pts[i]
        seg_len = math.hypot(b[0] - a[0], b[1] - a[1])
        iv = _circle_interval(a, b, cx, cy, r)
        if iv is None:
            if not cur:
                cur.append(a)
                cur_start = acc
            cur.append(b)
        else:
            touched = True
            t1, t2 = iv
            if t1 > 0.0:             # начало сегмента уцелело
                if not cur:
                    cur.append(a)
                    cur_start = acc
                cur.append(_lerp_pt(a, b, t1))
                close()
            else:                    # точка a внутри круга
                close()
            if t2 < 1.0:             # конец сегмента уцелел — новый кусок
                cur.extend((_lerp_pt(a, b, t2), b))
                cur_start = acc + t2 * seg_len
        acc += seg_len
    close()
    if not touched:
        return None
    return runs


def _cut_line_intervals(p1, p2, cx, cy, r):
    """Вырезать круг из отрезка. None — не задет; иначе список пар точек
    (координаты сцены) оставшихся кусков. Уцелевший хвост заканчивается
    ровно в исходной точке p2 (а не в пересчитанной x1 + dx·1.0, которая
    может отличаться последним битом float) — у стрелки благодаря этому
    сохраняется наконечник."""
    x1, y1, x2, y2 = p1.x(), p1.y(), p2.x(), p2.y()
    if _dist_to_segment(cx, cy, x1, y1, x2, y2) >= r:
        return None
    iv = _circle_interval((x1, y1), (x2, y2), cx, cy, r)
    if iv is None:
        return None   # численный край: касание без пересечения — не задет
    t1, t2 = iv
    dx, dy = x2 - x1, y2 - y1
    out = []
    if t1 > 1e-9:
        out.append((QPointF(x1, y1),
                    QPointF(x1 + dx * t1, y1 + dy * t1)))
    if t2 < 1.0 - 1e-9:
        # хвост — именно исходный объект p2 (identity): стрелка после
        # разрезания сравнивает конец с прежним p2 через ==/is, и наконечник
        # сохраняется; алиасинг безопасен — исходная запись покидает документ
        out.append((QPointF(x1 + dx * t2, y1 + dy * t2), p2))
    return out


# ---------- мини-LaTeX: подмножество для надписей ----------
# Поддержка: ^ _ { } \frac{a}{b} \sqrt{x} \alpha..\omega (и заглавные),
# \times \cdot \pm \leq \geq \neq \approx \infty \sum \prod \int \partial
# \nabla \rightarrow \leftarrow \in \cup \cap \forall \exists \ldots

_TEX_GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π", "rho": "ρ",
    "sigma": "σ", "tau": "τ", "phi": "φ", "chi": "χ", "psi": "ψ",
    "omega": "ω", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ",
    "Pi": "Π", "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
}
_TEX_SYMS = {
    "times": "×", "cdot": "·", "pm": "±", "mp": "∓", "leq": "≤",
    "geq": "≥", "neq": "≠", "approx": "≈", "infty": "∞", "sum": "∑",
    "prod": "∏", "int": "∫", "partial": "∂", "nabla": "∇",
    "rightarrow": "→", "leftarrow": "←", "to": "→", "in": "∈",
    "cup": "∪", "cap": "∩", "forall": "∀", "exists": "∃",
    "ldots": "…", "dots": "…",
}
_TEX_IGNORE = {"left", "right", "big", "Big", "bigg", "Bigg",
               "displaystyle", "limits", "limits", "quad", "qquad"}

_FONT_CACHE = {}


def _tex_font(px):
    px = max(4, int(round(px)))
    f = _FONT_CACHE.get(px)
    if f is None:
        f = QFont()
        f.setPixelSize(px)
        _FONT_CACHE[px] = f
    return f


def _tex_tokenize(s):
    toks = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\":
            j = i + 1
            if j < len(s) and s[j].isalpha():
                k = j
                while k < len(s) and s[k].isalpha():
                    k += 1
                toks.append(("cmd", s[j:k]))
                i = k
            else:  # экранированный символ: \{ \_ \, и т.п.
                toks.append(("ch", s[j:j + 1] if j < len(s) else ""))
                i = j + 1
        elif ch in "{}^_":
            toks.append((ch, ch))
            i += 1
        elif ch == " ":
            i += 1
        else:
            toks.append(("ch", ch))
            i += 1
    return toks


def _tex_atom(toks, i):
    """Один аргумент команды/индекса: {группа} или один токен."""
    if i < len(toks) and toks[i][0] == "{":
        return _tex_parse_seq(toks, i + 1, True)
    if i < len(toks):
        kind, val = toks[i]
        if kind == "cmd":
            ch = _TEX_GREEK.get(val) or _TEX_SYMS.get(val)
            return [("t", ch if ch is not None else val)], i + 1
        if kind == "ch":
            return [("t", val)], i + 1
        return [], i + 1
    return [], i


def _tex_parse_seq(toks, i, stop_at_brace):
    nodes = []
    while i < len(toks):
        kind, val = toks[i]
        if kind == "}" and stop_at_brace:
            return nodes, i + 1
        if kind == "{":
            sub, i = _tex_parse_seq(toks, i + 1, True)
            nodes.append(("grp", sub))
            continue
        if kind in ("^", "_"):
            atom, i = _tex_atom(toks, i + 1)
            nodes.append(("sup" if kind == "^" else "sub", atom))
            continue
        if kind == "cmd" and val in ("frac", "dfrac", "tfrac"):
            num, i = _tex_atom(toks, i + 1)
            den, i = _tex_atom(toks, i)
            nodes.append(("frac", num, den))
            continue
        if kind == "cmd" and val == "sqrt":
            body, i = _tex_atom(toks, i + 1)
            nodes.append(("sqrt", body))
            continue
        if kind == "cmd":
            i += 1
            if val in _TEX_IGNORE:
                continue
            ch = _TEX_GREEK.get(val) or _TEX_SYMS.get(val)
            nodes.append(("t", ch if ch is not None else val))
            continue
        nodes.append(("t", val))
        i += 1
    return nodes, i


def _tex_parse(s):
    return _tex_parse_seq(_tex_tokenize(s), 0, False)[0]


def _tex_measure(nodes, px):
    w = asc = desc = 0.0
    for nd in nodes:
        nw, na, ndesc = _tex_measure1(nd, px)
        w += nw
        asc = max(asc, na)
        desc = max(desc, ndesc)
    return w, asc, desc


def _tex_measure1(nd, px):
    typ = nd[0]
    if typ == "t":
        fm = QFontMetricsF(_tex_font(px))
        return fm.horizontalAdvance(nd[1]), fm.ascent(), fm.descent()
    if typ == "grp":
        return _tex_measure(nd[1], px)
    if typ in ("sup", "sub"):
        w, a, d = _tex_measure(nd[1], px * 0.72)
        if typ == "sup":
            return w, a + 0.45 * px, max(d - 0.45 * px, 0.0)
        return w, max(a - 0.28 * px, 0.0), d + 0.28 * px
    if typ == "frac":
        wn, an, dn = _tex_measure(nd[1], px * 0.82)
        wd, ad, dd = _tex_measure(nd[2], px * 0.82)
        pad = 0.12 * px
        gap = 0.22 * px
        bar = 0.06 * px
        top = bar + gap / 2 + dn + an
        bot = gap / 2 + ad + dd - bar
        return max(wn, wd) + 2 * pad, top, bot
    if typ == "sqrt":
        w, a, d = _tex_measure(nd[1], px)
        return 0.55 * px + w + 0.1 * px, a + 0.18 * px, d
    return 0.0, 0.0, 0.0


def _tex_draw(painter, nodes, x, y, px, color):
    painter.setPen(QPen(color, max(1.0, px * 0.045)))
    return _tex_draw_run(painter, nodes, x, y, px)


def _tex_draw_run(painter, nodes, x, y, px):
    x0 = x
    for nd in nodes:
        x += _tex_draw1(painter, nd, x, y, px)
    return x - x0


def _tex_draw1(painter, nd, x, y, px):
    typ = nd[0]
    if typ == "t":
        f = _tex_font(px)
        painter.setFont(f)
        painter.drawText(QPointF(x, y), nd[1])
        return QFontMetricsF(f).horizontalAdvance(nd[1])
    if typ == "grp":
        return _tex_draw_run(painter, nd[1], x, y, px)
    if typ in ("sup", "sub"):
        dy = -0.45 * px if typ == "sup" else 0.28 * px
        return _tex_draw_run(painter, nd[1], x, y + dy, px * 0.72)
    if typ == "frac":
        wn, an, dn = _tex_measure(nd[1], px * 0.82)
        wd, ad, dd = _tex_measure(nd[2], px * 0.82)
        pad = 0.12 * px
        gap = 0.22 * px
        w = max(wn, wd) + 2 * pad
        bar_y = y - 0.06 * px
        _tex_draw_run(painter, nd[1], x + (w - wn) / 2,
                      bar_y - gap / 2 - dn, px * 0.82)
        _tex_draw_run(painter, nd[2], x + (w - wd) / 2,
                      bar_y + gap / 2 + ad, px * 0.82)
        painter.drawLine(QPointF(x + 0.4 * pad, bar_y),
                         QPointF(x + w - 0.4 * pad, bar_y))
        return w
    if typ == "sqrt":
        w, a, _d = _tex_measure(nd[1], px)
        rw = 0.55 * px
        top = y - a - 0.14 * px
        painter.drawPolyline(QPolygonF([
            QPointF(x, y - 0.25 * px),
            QPointF(x + 0.18 * px, y + 0.02 * px),
            QPointF(x + 0.34 * px, top - 0.14 * px),
            QPointF(x + 0.45 * px, top),
        ]))
        painter.drawLine(QPointF(x + 0.45 * px, top),
                         QPointF(x + rw + w + 0.08 * px, top))
        _tex_draw_run(painter, nd[1], x + rw, y, px)
        return rw + w + 0.1 * px
    return 0.0


def _text_extent(e):
    """(ширина, высота над базовой линией, под ней) для записи text."""
    if e.get("latex"):
        return _tex_measure(_tex_parse(e["text"]), e["size"])
    px = e["size"]
    fm = QFontMetricsF(_tex_font(px))
    lines = e["text"].split("\n") or [""]
    w = max((fm.horizontalAdvance(ln) for ln in lines), default=0.0)
    return w, fm.ascent(), fm.descent() + (len(lines) - 1) * px * 1.25


TEXT_SIZES = [("Мелкий", 20), ("Обычный", 28), ("Крупный", 40),
              ("Большой", 56), ("Огромный", 80)]


class TextDialog(QDialog):
    """Диалог надписи: текст, размер шрифта, обычный текст или LaTeX."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Надпись")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "Текст или LaTeX-формула.\n"
            "LaTeX-подмножество: ^ _ \\frac{a}{b} \\sqrt{x} "
            "\\alpha…\\omega \\times \\leq \\infty …"))
        self.edit = QPlainTextEdit()
        # Tab переводит фокус на следующий элемент диалога, а не вставляет
        # табуляцию: надписи всё равно однострочные в большинстве случаев
        self.edit.setTabChangesFocus(True)
        self.edit.setPlaceholderText(
            "Например: Теорема Пифагора  или  c^2 = a^2 + b^2")
        lay.addWidget(self.edit)
        row = QHBoxLayout()
        self.radio_plain = QRadioButton("Обычный текст")
        self.radio_plain.setChecked(True)
        self.radio_latex = QRadioButton("LaTeX")
        row.addWidget(self.radio_plain)
        row.addWidget(self.radio_latex)
        row.addStretch(1)
        row.addWidget(QLabel("Размер:"))
        self.combo = QComboBox()
        for name, px in TEXT_SIZES:
            self.combo.addItem(f"{name} ({px})", px)
        self.combo.setCurrentIndex(1)  # Обычный
        row.addWidget(self.combo)
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def text(self):
        return self.edit.toPlainText()

    def size(self):
        return self.combo.currentData()

    def is_latex(self):
        return self.radio_latex.isChecked()


def _smooth_points(pts, passes=1):
    """Скользящее среднее (0.25/0.5/0.25), концы закреплены.
    Число проходов совпадает с живой отрисовкой — линия не меняется
    после отрыва пера."""
    for _ in range(passes):
        if len(pts) < 3:
            break
        out = [pts[0]]
        for i in range(1, len(pts) - 1):
            x = 0.25 * pts[i - 1][0] + 0.5 * pts[i][0] + 0.25 * pts[i + 1][0]
            y = 0.25 * pts[i - 1][1] + 0.5 * pts[i][1] + 0.25 * pts[i + 1][1]
            p = (pts[i - 1][2] + pts[i][2] + pts[i + 1][2]) / 3.0
            out.append((x, y, p))
        out.append(pts[-1])
        pts = out
    return pts


class StrokeLayer:
    """Слой штрихов из плиток TILE x TILE: память только под исписанные участки."""

    def __init__(self):
        self.tiles = {}

    def _tile(self, tx, ty):
        key = (tx, ty)
        pm = self.tiles.get(key)
        if pm is None:
            pm = QPixmap(TILE, TILE)
            pm.fill(Qt.GlobalColor.transparent)
            self.tiles[key] = pm
        return pm

    def draw(self, bbox, draw_fn, only_tiles=None):
        """draw_fn(painter) рисует в координатах сцены; bbox — (x0,y0,x1,y1)."""
        x0, y0, x1, y1 = bbox
        tx0 = int(max(0, x0)) // TILE
        tx1 = int(min(SCENE_W - 1, x1)) // TILE
        ty0 = int(max(0, y0)) // TILE
        ty1 = int(min(SCENE_H - 1, y1)) // TILE
        for tx in range(tx0, tx1 + 1):
            for ty in range(ty0, ty1 + 1):
                if only_tiles is not None and (tx, ty) not in only_tiles:
                    continue
                pm = self._tile(tx, ty)
                p = QPainter(pm)
                p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                p.translate(-tx * TILE, -ty * TILE)
                draw_fn(p)
                p.end()

    def rebuild_region(self, bbox, entries, canvas):
        """Очистить плитки в области и заново отрисовать в них записи слоя.
        Нужно, чтобы живую отрисовку заменить на финальную без «лишних» следов."""
        x0, y0, x1, y1 = bbox
        tx0 = int(max(0, x0)) // TILE
        tx1 = int(min(SCENE_W - 1, x1)) // TILE
        ty0 = int(max(0, y0)) // TILE
        ty1 = int(min(SCENE_H - 1, y1)) // TILE
        cleared = set()
        for tx in range(tx0, tx1 + 1):
            for ty in range(ty0, ty1 + 1):
                pm = self.tiles.get((tx, ty))
                if pm is not None:
                    pm.fill(Qt.GlobalColor.transparent)
                    cleared.add((tx, ty))
        if not cleared:
            return
        # пересечение проверяем с ПОЛНЫМИ границами очищенных плиток:
        # запись, чьи пиксели лежат в очищенной плитке, но чей bbox не
        # пересекает исходную область, всё равно должна быть перерисована,
        # иначе часть записи пропадёт
        region = (tx0 * TILE, ty0 * TILE, (tx1 + 1) * TILE, (ty1 + 1) * TILE)
        for e in entries:
            eb = canvas._eb(e)
            if eb[0] <= region[2] and eb[2] >= region[0] and eb[1] <= region[3] and eb[3] >= region[1]:
                if e["kind"] == "stroke":
                    self.draw(eb, lambda p, e=e: canvas._render_stroke(p, e),
                              only_tiles=cleared)
                elif e["kind"] == "line":
                    self.draw(eb, lambda p, e=e: canvas._render_line(p, e),
                              only_tiles=cleared)
                elif e["kind"] == "axes":
                    self.draw(eb, lambda p, e=e: canvas._render_axes(p, e),
                              only_tiles=cleared)
                elif e["kind"] == "curve":
                    self.draw(eb, lambda p, e=e: canvas._render_curve(p, e),
                              only_tiles=cleared)
                elif e["kind"] == "text":
                    self.draw(eb, lambda p, e=e: canvas._render_text(p, e),
                              only_tiles=cleared)


class Canvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TabletTracking, True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)

        self.color = DEFAULT_COLOR
        self.pen_width = DEFAULT_WIDTH
        self.eraser_tool = False
        self._apply_pen_cursor()
        self.mouse_draw = False   # False: ЛКМ — выделение; True: ЛКМ — рисование

        self.zoom = 1.0
        self.offset = QPointF(0, 0)

        self.entries = []       # документ в порядке создания
        self.undo_stack = []
        self.redo_stack = []
        self.layers = []        # [{'kind':'strokes','tl':StrokeLayer,'entries':[...]} | {'kind':'image','entry':e}]
        self._active_tl = None
        self._bbox_cache = {}   # id(entry) -> bbox; инвалидируется при изменении записи

        self.mode = None        # None|'stroke'|'line'|'erase'|'drag'|'pan'|'resize'|'rotate'|'select'|'move_sel'
        self.cur_points = []
        self._live = []
        self.line_anchor = None
        self.dashed_lines = False   # линии по Shift рисуются пунктиром
        self._line_snap = False     # Ctrl во время рисования линии — прилипание
        self._line_arrow = False    # текущая линия со стрелкой (зажата Q)
        self._q_held = False        # клавиша Q сейчас зажата
        self.circle_tool = False    # инструмент «Окружность»: центр + точка на ней
        self._circle_center_fixed = None  # прилипший центр окружности (Ctrl)
        self.show_grid = True       # фоновая сетка
        self.cursor_scene = QPointF(200, 200)
        self.drag = None
        self._pan_last = None
        self.selected_image = None
        self.selected_entries = []
        self.clipboard_entries = []  # внутренний буфер для Ctrl+C/Ctrl+V
        self._paste_counter = 0
        # если системный буфер изменился извне (там, например, появилось
        # изображение) — скопированное выделение больше не актуально
        QGuiApplication.clipboard().dataChanged.connect(
            self._on_clipboard_changed)
        self._sel_anchor = None
        self._move_last = None
        self._move_total = None    # суммарный сдвиг текущего переноса
        self._move_entries = []    # записи текущего переноса (для undo)
        self._drag_start = None    # pos изображения до перетаскивания
        self._resize_info = None
        self._rotate_info = None
        self._rotate_start = None  # угол изображения до начала поворота (для undo)
        self._resize_axis = None   # доминирующая ось текущего растягивания

        # Wintab (Wacom)
        self.wintab = None
        self._wt_mode = None
        self._wt_seen = False
        self._wt_hover_last = 0.0
        self._tab_last = 0.0   # время последнего события пера от Qt
        self._wt_eraser = False
        self._wt_diag_t = 0.0
        # ластик: радиус от скорости, группа undo на один проход
        self._erase_last = None      # (QPointF, time) предыдущей точки
        self._erase_speed = 0.0      # сглаженная скорость, px/с
        self._erase_group = None     # undo-группа текущего прохода
        if sys.platform == "win32":
            try:
                wt = WinTabTablet(self.winId())
                if wt.ok:
                    self.wintab = wt
            except Exception:
                self.wintab = None

    # ---------- координаты и вид ----------

    def _to_scene(self, screen_pos: QPointF) -> QPointF:
        return screen_pos / self.zoom + self.offset

    def _clamp_offset(self):
        vw = self.width() / self.zoom
        vh = self.height() / self.zoom
        max_x = max(0.0, SCENE_W - vw)
        max_y = max(0.0, SCENE_H - vh)
        self.offset.setX(min(max(self.offset.x(), 0.0), max_x))
        self.offset.setY(min(max(self.offset.y(), 0.0), max_y))

    def center_view(self):
        vw = self.width() / self.zoom
        vh = self.height() / self.zoom
        self.offset = QPointF((SCENE_W - vw) / 2, (SCENE_H - vh) / 2)
        self._clamp_offset()
        self.update()

    def reset_view(self):
        self.zoom = 1.0
        self.center_view()

    # ---------- слои ----------

    def _ensure_stroke_layer(self):
        if self.layers and self.layers[-1]["kind"] == "strokes":
            return self.layers[-1]
        layer = {"kind": "strokes", "tl": StrokeLayer(), "entries": []}
        self.layers.append(layer)
        return layer

    def _entry_bbox(self, e, margin=None):
        if e["kind"] == "stroke":
            m = e["width"] + 4 if margin is None else margin
            xs = [p[0] for p in e["pts"]]
            ys = [p[1] for p in e["pts"]]
            return (min(xs) - m, min(ys) - m, max(xs) + m, max(ys) + m)
        if e["kind"] == "line":
            m = e["width"] + 4 if margin is None else margin
            return (min(e["p1"].x(), e["p2"].x()) - m,
                    min(e["p1"].y(), e["p2"].y()) - m,
                    max(e["p1"].x(), e["p2"].x()) + m,
                    max(e["p1"].y(), e["p2"].y()) + m)
        if e["kind"] == "curve":
            m = e["width"] + 4 if margin is None else margin
            xs = [px for seg in e["segs"] for (px, _py) in seg]
            ys = [py for seg in e["segs"] for (_px, py) in seg]
            return (min(xs) - m, min(ys) - m, max(xs) + m, max(ys) + m)
        if e["kind"] == "text":
            m = 4 if margin is None else margin
            w, asc, desc = _text_extent(e)
            x, y = e["pos"].x(), e["pos"].y()
            return (x - m, y - asc - m, x + w + m, y + desc + m)
        if e["kind"] == "axes":
            m = 60 if margin is None else margin
            return (e["center"].x() - e["hw"] - m, e["center"].y() - e["hh"] - m,
                    e["center"].x() + e["hw"] + m, e["center"].y() + e["hh"] + m)
        # image: учитываем поворот
        m = margin or 0
        iw, ih = self._img_size(e)
        w, h = iw / 2.0, ih / 2.0
        cx, cy = e["pos"].x() + w, e["pos"].y() + h
        a = math.radians(e.get("angle", 0))
        ca, sa = math.cos(a), math.sin(a)
        xs, ys = [], []
        for sx in (-w, w):
            for sy in (-h, h):
                xs.append(cx + sx * ca - sy * sa)
                ys.append(cy + sx * sa + sy * ca)
        return (min(xs) - m, min(ys) - m, max(xs) + m, max(ys) + m)

    def _render_entry_to_layer(self, tl, e):
        bbox = self._entry_bbox(e)
        if e["kind"] == "stroke":
            tl.draw(bbox, lambda p: self._render_stroke(p, e))
        elif e["kind"] == "line":
            tl.draw(bbox, lambda p: self._render_line(p, e))
        elif e["kind"] == "axes":
            tl.draw(bbox, lambda p: self._render_axes(p, e))
        elif e["kind"] == "curve":
            tl.draw(bbox, lambda p: self._render_curve(p, e))
        elif e["kind"] == "text":
            tl.draw(bbox, lambda p: self._render_text(p, e))

    def _rebuild_layers(self):
        self.layers = []
        cur = None
        for e in self.entries:
            if e["kind"] in STROKE_KINDS:
                if cur is None:
                    cur = {"kind": "strokes", "tl": StrokeLayer(), "entries": []}
                    self.layers.append(cur)
                cur["entries"].append(e)
            else:
                cur = None
                self.layers.append({"kind": "image", "entry": e})
        for layer in self.layers:
            if layer["kind"] == "strokes":
                for e in layer["entries"]:
                    self._render_entry_to_layer(layer["tl"], e)
        self._active_tl = None

    # ---------- рендер ----------

    def _render_stroke(self, painter, e):
        col = QColor(e["color"])
        base_w = e["width"]
        pts = e["pts"]
        if len(pts) == 1:
            x, y, f = pts[0]
            w = max(base_w * f, MIN_STROKE_W)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(col)
            painter.drawEllipse(QPointF(x, y), w / 2, w / 2)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            return
        if len(pts) == 2:
            w = max(base_w * (pts[0][2] + pts[1][2]) / 2.0, MIN_STROKE_W)
            painter.setPen(QPen(col, w, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap))
            painter.drawLine(QPointF(pts[0][0], pts[0][1]),
                             QPointF(pts[1][0], pts[1][1]))
            return
        prev = (pts[0][0], pts[0][1])
        n = len(pts)
        for i in range(1, n):
            if i < n - 1:
                mx = (pts[i][0] + pts[i + 1][0]) / 2.0
                my = (pts[i][1] + pts[i + 1][1]) / 2.0
            else:
                mx, my = pts[i][0], pts[i][1]
            w = max(base_w * (pts[i - 1][2] + 2 * pts[i][2] +
                              (pts[i + 1][2] if i < n - 1 else pts[i][2])) / 4.0,
                    MIN_STROKE_W)
            painter.setPen(QPen(col, w, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            path = QPainterPath(QPointF(prev[0], prev[1]))
            path.quadTo(QPointF(pts[i][0], pts[i][1]), QPointF(mx, my))
            painter.drawPath(path)
            prev = (mx, my)

    def _snap(self, pos):
        """При зажатом Ctrl во время рисования линии — ближайший узел сетки."""
        if not self._line_snap:
            return QPointF(pos)
        return QPointF(round(pos.x() / GRID_STEP) * GRID_STEP,
                       round(pos.y() / GRID_STEP) * GRID_STEP)

    def _circle_params(self, pos):
        """Центр и радиус строящейся окружности.
        Центр, однажды прилипнув к узлу сетки (Ctrl), таким и остаётся,
        даже если Ctrl отпустить; радиус прилипает только пока Ctrl зажат."""
        if self._circle_center_fixed is not None:
            center = QPointF(self._circle_center_fixed)
        elif self._line_snap:
            center = self._snap(self.line_anchor)
            self._circle_center_fixed = QPointF(center)
        else:
            center = QPointF(self.line_anchor)
        r = math.hypot(pos.x() - center.x(), pos.y() - center.y())
        if self._line_snap:
            r = max(GRID_STEP, round(r / GRID_STEP) * GRID_STEP)
        return center, r

    def _render_line(self, painter, e):
        style = (Qt.PenStyle.DashLine if e.get("dash")
                 else Qt.PenStyle.SolidLine)
        pen = QPen(e["color"], e["width"], style,
                   Qt.PenCapStyle.RoundCap)
        if e.get("dash") and e.get("dash_off"):
            # фаза пунктира продолжается от исходного начала линии,
            # а не от начала уцелевшего куска
            pen.setDashOffset(e["dash_off"] / max(e["width"], 1e-6))
        painter.setPen(pen)
        p1, p2 = e["p1"], e["p2"]
        if e.get("arrow"):
            # древко не доходит до острия: конец линии прячется под
            # наконечник и не выглядывает из-под него
            end = self._shaft_end(p1, p2, e["width"])
            if end is not None:
                painter.drawLine(p1, end)
            self._draw_arrowhead(painter, p1, p2, e["color"], e["width"])
        else:
            painter.drawLine(p1, p2)

    @staticmethod
    def _arrowhead_len(width):
        return 14.0 + width * 1.8

    @staticmethod
    def _shaft_end(p1, p2, width):
        """Конец древка стрелки: укорочен на длину наконечника (минус
        небольшой заход внутрь). None — линия короче наконечника,
        древко не рисуем."""
        dx, dy = p2.x() - p1.x(), p2.y() - p1.y()
        L = math.hypot(dx, dy)
        if L == 0:
            return None
        pull = Canvas._arrowhead_len(width) - max(width / 2.0, 1.5)
        if L <= pull:
            return None
        t = (L - pull) / L
        return QPointF(p1.x() + dx * t, p1.y() + dy * t)

    @staticmethod
    def _draw_arrowhead(painter, p1, p2, color, width):
        """Наконечник стрелки в точке p2 (направление p1 -> p2):
        простой узкий вытянутый треугольник."""
        dx, dy = p2.x() - p1.x(), p2.y() - p1.y()
        if dx == 0 and dy == 0:
            return
        ang = math.atan2(dy, dx)
        ah = 14.0 + width * 1.8   # длина наконечника
        spread = 0.30             # узкий полуугол раскрытия
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawPolygon(QPolygonF([
            QPointF(p2.x(), p2.y()),
            QPointF(p2.x() - ah * math.cos(ang - spread),
                    p2.y() - ah * math.sin(ang - spread)),
            QPointF(p2.x() - ah * math.cos(ang + spread),
                    p2.y() - ah * math.sin(ang + spread))]))
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def _render_axes(self, painter, e):
        """Координатные оси со стрелками, делениями по клеткам и подписями."""
        cx, cy = e["center"].x(), e["center"].y()
        hw, hh = e["hw"], e["hh"]
        col = QColor(e["color"])
        w = e["width"]
        painter.setPen(QPen(col, w, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(cx - hw, cy), QPointF(cx + hw, cy))
        painter.drawLine(QPointF(cx, cy + hh), QPointF(cx, cy - hh))
        # стрелки: вправо и вверх
        ah = 12.0 + w
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(col)
        painter.drawPolygon(QPolygonF([QPointF(cx + hw + ah, cy),
                                       QPointF(cx + hw, cy - ah * 0.45),
                                       QPointF(cx + hw, cy + ah * 0.45)]))
        painter.drawPolygon(QPolygonF([QPointF(cx, cy - hh - ah),
                                       QPointF(cx - ah * 0.45, cy - hh),
                                       QPointF(cx + ah * 0.45, cy - hh)]))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # деления по клеткам
        painter.setPen(QPen(col, max(w - 0.5, 1.0)))
        tick = 5.0
        x = cx - int(hw // GRID_STEP) * GRID_STEP
        while x <= cx + hw:
            if abs(x - cx) > 1:
                painter.drawLine(QPointF(x, cy - tick), QPointF(x, cy + tick))
            x += GRID_STEP
        y = cy - int(hh // GRID_STEP) * GRID_STEP
        while y <= cy + hh:
            if abs(y - cy) > 1:
                painter.drawLine(QPointF(cx - tick, y), QPointF(cx + tick, y))
            y += GRID_STEP

    def _render_curve(self, painter, e):
        """Кривая (график/уравнение) — набор ломаных сегментов."""
        style = (Qt.PenStyle.DashLine if e.get("dash")
                 else Qt.PenStyle.SolidLine)
        pen = QPen(e["color"], e["width"], style,
                   Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        offs = e.get("seg_off")
        w = max(e["width"], 1e-6)
        if not e.get("dash"):
            # обязательно ДО цикла: иначе сплошная кривая рисуется
            # стандартным пером Qt (чёрный волос 1 px), игнорируя
            # выбранные толщину и цвет
            painter.setPen(pen)
        for i, seg in enumerate(e["segs"]):
            if len(seg) >= 2:
                if e.get("dash"):
                    # фаза пунктира — от начала исходной кривой,
                    # явно для каждого сегмента (не течёт с предыдущего)
                    off = offs[i] if offs and i < len(offs) else 0.0
                    pen.setDashOffset(off / w)
                    painter.setPen(pen)
                painter.drawPolyline(
                    QPolygonF([QPointF(x, y) for x, y in seg]))

    def _render_text(self, painter, e):
        """Надпись: обычный текст (многострочный) или мини-LaTeX."""
        x, y = e["pos"].x(), e["pos"].y()
        if e.get("latex"):
            _tex_draw(painter, _tex_parse(e["text"]), x, y,
                      e["size"], e["color"])
            return
        painter.setFont(_tex_font(e["size"]))
        painter.setPen(QPen(e["color"]))
        for i, ln in enumerate(e["text"].split("\n")):
            painter.drawText(QPointF(x, y + i * e["size"] * 1.25), ln)

    # ---------- ручки изображения (рамка выбора) ----------

    @staticmethod
    def _img_size(e):
        """Текущий отображаемый размер: во время растягивания — preview (_pv),
        иначе размер pixmap."""
        return e.get("_pv", (e["pix"].width(), e["pix"].height()))

    def _image_center(self, e):
        w, h = self._img_size(e)
        return (e["pos"].x() + w / 2.0, e["pos"].y() + h / 2.0)

    def _to_image_local(self, e, pos):
        """Координаты сцены -> локальные координаты изображения (без поворота)."""
        cx, cy = self._image_center(e)
        dx, dy = pos.x() - cx, pos.y() - cy
        a = math.radians(-e.get("angle", 0))
        ca, sa = math.cos(a), math.sin(a)
        return QPointF(dx * ca - dy * sa, dx * sa + dy * ca)

    def _over_resize_handle(self, e, pos):
        hs = 18.0 / self.zoom   # зона попадания чуть шире видимой ручки
        loc = self._to_image_local(e, pos)
        w, h = self._img_size(e)
        w2, h2 = w / 2.0, h / 2.0
        return abs(loc.x() - w2) <= hs and abs(loc.y() - h2) <= hs

    def _over_rotate_handle(self, e, pos):
        r = 13.0 / self.zoom
        loc = self._to_image_local(e, pos)
        h2 = self._img_size(e)[1] / 2.0
        return math.hypot(loc.x(), loc.y() - (-h2 - 28.0 / self.zoom)) <= r

    def _draw_image(self, p, e):
        cx, cy = self._image_center(e)
        p.save()
        p.translate(cx, cy)
        p.rotate(e.get("angle", 0))
        pv = e.get("_pv")
        if pv is not None:
            # во время растягивания рисуем кэшированную масштабированную
            # копию (обновляется редко), домасштабированную до точного
            # preview-размера: плавно и без ресайза оригинала на каждый кадр
            w, h = pv
            src = e.get("_pv_cache")
            if src is None:
                src = e["orig"]
            p.drawPixmap(QRectF(-w / 2.0, -h / 2.0, w, h), src,
                         QRectF(src.rect()))
        else:
            p.drawPixmap(QPointF(-e["pix"].width() / 2.0,
                                 -e["pix"].height() / 2.0), e["pix"])
        p.restore()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), OUTSIDE_COLOR)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        p.scale(self.zoom, self.zoom)
        p.translate(-self.offset.x(), -self.offset.y())

        vw = self.width() / self.zoom
        vh = self.height() / self.zoom
        vis = QRectF(self.offset.x(), self.offset.y(), vw, vh)
        scene_rect = QRectF(0, 0, SCENE_W, SCENE_H).intersected(vis)

        p.fillRect(QRectF(0, 0, SCENE_W, SCENE_H), BG_COLOR)
        if self.show_grid:
            p.setPen(QPen(GRID_COLOR, 1))
            x0 = max(0, int(scene_rect.left() // GRID_STEP) * GRID_STEP)
            x1 = min(SCENE_W, scene_rect.right())
            y0 = max(0, int(scene_rect.top() // GRID_STEP) * GRID_STEP)
            y1 = min(SCENE_H, scene_rect.bottom())
            x = x0
            while x <= x1:
                p.drawLine(QPointF(x, scene_rect.top()), QPointF(x, scene_rect.bottom()))
                x += GRID_STEP
            y = y0
            while y <= y1:
                p.drawLine(QPointF(scene_rect.left(), y), QPointF(scene_rect.right(), y))
                y += GRID_STEP
        p.setPen(QPen(QColor("#888888"), 2 / self.zoom))
        p.drawRect(QRectF(0, 0, SCENE_W, SCENE_H))

        # слои в правильном z-порядке: штрихи поверх ранее вставленных картинок
        tx0 = int(max(0, vis.left())) // TILE
        tx1 = int(min(SCENE_W - 1, vis.right())) // TILE
        ty0 = int(max(0, vis.top())) // TILE
        ty1 = int(min(SCENE_H - 1, vis.bottom())) // TILE
        for layer in self.layers:
            if layer["kind"] == "strokes":
                tiles = layer["tl"].tiles
                for tx in range(tx0, tx1 + 1):
                    for ty in range(ty0, ty1 + 1):
                        pm = tiles.get((tx, ty))
                        if pm is not None:
                            p.drawPixmap(tx * TILE, ty * TILE, pm)
            else:
                self._draw_image(p, layer["entry"])

        # рамка выбранного изображения + ручки размера и поворота
        if self.selected_image is not None:
            e = self.selected_image
            cx, cy = self._image_center(e)
            iw, ih = self._img_size(e)
            w2, h2 = iw / 2.0, ih / 2.0
            hs = 12.0 / self.zoom
            hr = 7.0 / self.zoom
            hy = -h2 - 28.0 / self.zoom
            p.save()
            p.translate(cx, cy)
            p.rotate(e.get("angle", 0))
            pen = QPen(QColor("#2b5fd9"), 1.5, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(-w2, -h2, w2 * 2, h2 * 2))
            solid = QPen(QColor("#2b5fd9"), 1.5)
            solid.setCosmetic(True)
            p.setPen(solid)
            # ручка размера (правый нижний угол)
            p.setBrush(QColor("white"))
            p.drawRect(QRectF(w2 - hs / 2, h2 - hs / 2, hs, hs))
            # ручка поворота (над серединой верхней стороны)
            p.drawLine(QPointF(0, -h2), QPointF(0, hy))
            p.drawEllipse(QPointF(0, hy), hr, hr)
            p.restore()

        # рамка выделения (тянется ЛКМ) и подсветка выбранных записей
        if self.mode == "select" and self._sel_anchor is not None:
            pen = QPen(QColor("#2b5fd9"), 1.2, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.setBrush(QColor(43, 95, 217, 24))
            p.drawRect(QRectF(self._sel_anchor, self.cursor_scene).normalized())
            p.setBrush(Qt.BrushStyle.NoBrush)
        if self.selected_entries:
            pen = QPen(QColor("#2b9e44"), 1.2, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            for e in self.selected_entries:
                x0, y0, x1, y1 = self._entry_bbox(e)
                p.drawRect(QRectF(QPointF(x0, y0), QPointF(x1, y1)))

        if self.mode == "line" and self.line_anchor is not None:
            pen = QPen(self.color, self.pen_width,
                       Qt.PenStyle.DashLine if self.dashed_lines
                       else Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap)
            pen.setCosmetic(True)
            p.setPen(pen)
            a, b = self._snap(self.line_anchor), self._snap(self.cursor_scene)
            if self._line_arrow:
                # и в предпросмотре древко укорочено: наконечник не
                # накладывается на круглый конец линии
                end = self._shaft_end(a, b, self.pen_width)
                if end is not None:
                    p.drawLine(a, end)
                self._draw_arrowhead(p, a, b, self.color, self.pen_width)
            else:
                p.drawLine(a, b)
        if self.mode == "circle" and self.line_anchor is not None:
            center, r = self._circle_params(self.cursor_scene)
            pen = QPen(self.color, self.pen_width,
                       Qt.PenStyle.DashLine if self.dashed_lines
                       else Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.drawEllipse(center, r, r)
            # крестик в центре предпросмотра
            p.drawLine(QPointF(center.x() - 4, center.y()),
                       QPointF(center.x() + 4, center.y()))
            p.drawLine(QPointF(center.x(), center.y() - 4),
                       QPointF(center.x(), center.y() + 4))
        p.end()

    # ---------- перехват системных сообщений ----------

    def nativeEvent(self, eventType, message):
        # Перехват системных сообщений имеет смысл только в Windows:
        # message — это указатель на Win32 MSG. На macOS это NSEvent*, и
        # трактовать его как _MSG нельзя.
        if sys.platform == "win32":
            try:
                msg = _MSG.from_address(int(message))
                if WT_PACKET <= msg.message <= 0x7FF7:
                    if self.wintab is not None and self.wintab.ok:
                        self.wintab.process(msg.message, msg.wParam, msg.lParam, self)
                elif msg.message in (WM_POINTERDOWN, WM_POINTERUPDATE, WM_POINTERUP):
                    _update_win_pointer_flag(msg)
            except Exception:
                pass
        return super().nativeEvent(eventType, message)

    # ---------- ввод через Wintab (Wacom) ----------

    def on_wintab(self, pressure, is_eraser):
        self._wt_seen = True
        self._wt_hover_last = time.time()
        # Позицию берём у системного курсора: драйвер Wacom (CXO_SYSTEM) сам
        # ведёт курсор за пером — координаты всегда совпадают с Qt.
        wpos = self.mapFromGlobal(QCursor.pos())
        pos = self._to_scene(QPointF(wpos))
        self.cursor_scene = pos
        self._pen_diag(pressure, is_eraser)

        contact = pressure > 0.0
        if contact and self._wt_mode == "stroke" and pressure < PRESS_MIN_DRAW:
            # почти оторванное перо завершает штрих, а не дописывает хвост
            self._finish_input(pos)
            self._wt_mode = None
            contact = False
        if not contact:
            if self._wt_mode is not None:
                self._finish_input(pos)
                self._wt_mode = None
            return

        if self._wt_mode is None:
            # перо, попавшее на ручку выбранного изображения, управляет
            # размером/поворотом, а не рисует — иначе остаются лишние точки
            e = self.selected_image
            if not is_eraser and e is not None:
                if self._over_rotate_handle(e, pos):
                    cx, cy = self._image_center(e)
                    ptr = math.degrees(math.atan2(pos.y() - cy, pos.x() - cx))
                    self._wt_mode = "rotate"
                    self._rotate_info = (e, ptr - e.get("angle", 0))
                    self._rotate_start = e.get("angle", 0.0)
                    self.mode = "rotate"
                    return
                if self._over_resize_handle(e, pos):
                    self._begin_resize(e, pos)
                    self._wt_mode = "resize"
                    return
            mods = QGuiApplication.keyboardModifiers()
            if is_eraser or self.eraser_tool or (mods & Qt.KeyboardModifier.AltModifier):
                self._wt_mode = "erase"
                self._begin_erase()
            elif (mods & Qt.KeyboardModifier.ShiftModifier) or self._q_held:
                self._wt_mode = "line"
                self._line_snap = bool(
                    mods & Qt.KeyboardModifier.ControlModifier)
                self._line_arrow = self._q_held
                self.line_anchor = QPointF(pos)
                self.update()
            elif self.circle_tool:
                self._wt_mode = "circle"
                self._line_snap = bool(
                    mods & Qt.KeyboardModifier.ControlModifier)
                self.line_anchor = QPointF(pos)
                self._circle_center_fixed = (
                    self._snap(pos) if self._line_snap else None)
                self.update()
            elif pressure >= PRESS_MIN_DRAW:
                self._wt_mode = "stroke"
                self._active_tl = self._ensure_stroke_layer()["tl"]
                self.cur_points = [(pos.x(), pos.y(), self._factor(pressure))]
            else:
                return
            self.mode = self._wt_mode
            if self._wt_mode == "erase":
                self.erase_at(pos)
            return

        if self._wt_mode == "erase":
            self.erase_at(pos)
        elif self._wt_mode == "stroke":
            self._stroke_add_point(pos, self._factor(pressure))
        elif self._wt_mode == "line":
            self._line_snap = bool(
                QGuiApplication.keyboardModifiers()
                & Qt.KeyboardModifier.ControlModifier)
            self.update()
        elif self._wt_mode == "circle":
            self._line_snap = bool(
                QGuiApplication.keyboardModifiers()
                & Qt.KeyboardModifier.ControlModifier)
            self.update()
        elif self._wt_mode == "resize":
            self._resize_image_to(pos)
        elif self._wt_mode == "rotate":
            self._rotate_image_to(pos)

    def finish_wintab(self):
        if self._wt_mode is not None:
            self._finish_input(self.cursor_scene)
            self._wt_mode = None

    def _pen_diag(self, pressure, is_eraser):
        now = time.time()
        if is_eraser != self._wt_eraser or now - self._wt_diag_t > 0.15:
            self._wt_eraser = is_eraser
            self._wt_diag_t = now
            tip = "ЛАСТИК" if is_eraser else "наконечник"
            self.window().statusBar().showMessage(
                f"Перо Wacom: {tip} · нажатие {int(pressure * 100)}%")

    def _pen_emulation(self):
        """Перо сейчас активно (Wintab или Qt tabletEvent): синтетические
        события мыши, которые драйвер Wacom шлёт вслед за пером, нужно
        игнорировать — иначе при тяге ручки изображения пером два потока
        событий с разными позициями дёргают размер туда-сюда (моргание)."""
        if (self.wintab is not None and self.wintab.ok
                and time.time() - self._wt_hover_last < 0.4):
            return True
        return time.time() - self._tab_last < 0.5

    def close_wintab(self):
        if self.wintab is not None:
            self.wintab.close()

    # ---------- ввод: планшет через Qt (Windows Ink) ----------

    def tabletEvent(self, event: QTabletEvent):
        if self.wintab is not None and self.wintab.ok and self._wt_seen:
            event.accept()
            return
        self._tab_last = time.time()
        pos = self._to_scene(event.position())
        self.cursor_scene = pos
        is_eraser = (
            event.pointerType() == QTabletEvent.PointerType.Eraser
            or _update_win_pointer_flag.eraser
            or (event.type() == QTabletEvent.EventType.TabletPress
                and event.button() == Qt.MouseButton.RightButton)
            or self.eraser_tool
            or bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        )
        etype = event.type()

        if etype == QTabletEvent.EventType.TabletPress:
            e = self.selected_image
            if not is_eraser and e is not None:
                if self._over_rotate_handle(e, pos):
                    cx, cy = self._image_center(e)
                    ptr = math.degrees(math.atan2(pos.y() - cy, pos.x() - cx))
                    self.mode = "rotate"
                    self._rotate_info = (e, ptr - e.get("angle", 0))
                    self._rotate_start = e.get("angle", 0.0)
                    event.accept()
                    return
                if self._over_resize_handle(e, pos):
                    self._begin_resize(e, pos)
                    event.accept()
                    return
            if is_eraser:
                self.mode = "erase"
                self._begin_erase()
                self.erase_at(pos)
            elif (event.modifiers() & Qt.KeyboardModifier.ShiftModifier
                  or self._q_held):
                self.mode = "line"
                self._line_snap = bool(
                    event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                self._line_arrow = self._q_held
                self.line_anchor = QPointF(pos)
                self.update()
            elif self.circle_tool:
                self.mode = "circle"
                self._line_snap = bool(
                    event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                self.line_anchor = QPointF(pos)
                self._circle_center_fixed = (
                    self._snap(pos) if self._line_snap else None)
                self.update()
            else:
                self.mode = "stroke"
                self._active_tl = self._ensure_stroke_layer()["tl"]
                self.cur_points = [(pos.x(), pos.y(), self._factor(event.pressure()))]
            event.accept()
            return

        if etype == QTabletEvent.EventType.TabletMove:
            if self.mode == "erase":
                self.erase_at(pos)
            elif self.mode == "stroke":
                if event.pressure() < PRESS_MIN_DRAW:
                    # нажатие почти пропало — завершаем штрих без хвоста
                    self._finish_input(pos)
                else:
                    self._stroke_add_point(pos, self._factor(event.pressure()))
            elif self.mode == "line":
                self._line_snap = bool(
                    event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                self.update()
            elif self.mode == "circle":
                self._line_snap = bool(
                    event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                self.update()
            elif self.mode == "resize":
                self._resize_image_to(pos)
            elif self.mode == "rotate":
                self._rotate_image_to(pos)
            event.accept()
            return

        if etype == QTabletEvent.EventType.TabletRelease:
            self._finish_input(pos)
            event.accept()
            return

        event.ignore()

    # ---------- ввод: мышь ----------

    def mousePressEvent(self, event):
        if self._pen_emulation():
            return
        pos = self._to_scene(event.position())
        self.cursor_scene = pos
        btn = event.button()

        if btn == Qt.MouseButton.MiddleButton:
            self.mode = "pan"
            self._pan_last = event.position()
            return

        if btn == Qt.MouseButton.RightButton:
            if self.eraser_tool or self._stroke_at(pos):
                self.mode = "erase"
                self._begin_erase()
                self.erase_at(pos)
            else:
                self.mode = "pan"
                self._pan_last = event.position()
            return

        if btn == Qt.MouseButton.LeftButton:
            if self.eraser_tool or event.modifiers() & Qt.KeyboardModifier.AltModifier:
                self.mode = "erase"
                self._begin_erase()
                self.erase_at(pos)
                return
            if self.selected_image is not None:
                e = self.selected_image
                if self._over_rotate_handle(e, pos):
                    cx, cy = self._image_center(e)
                    ptr = math.degrees(math.atan2(pos.y() - cy, pos.x() - cx))
                    self.mode = "rotate"
                    self._rotate_info = (e, ptr - e.get("angle", 0))
                    self._rotate_start = e.get("angle", 0.0)
                    return
                if self._over_resize_handle(e, pos):
                    self._begin_resize(e, pos)
                    return
            img = self._image_at(pos)
            if img is not None:
                self.selected_image = img
                self.selected_entries = []
                self.mode = "drag"
                self.drag = (img, pos - img["pos"])
                self._drag_start = QPointF(img["pos"])
                self.update()
                return
            # ЛКМ — выделение/перемещение или рисование (по переключателю)
            if (self.selected_entries and not self.mouse_draw and any(
                    self._bbox_contains(self._eb(e), pos)
                    for e in self.selected_entries)):
                self.mode = "move_sel"
                self._move_last = QPointF(pos)
                self._move_total = QPointF(0, 0)
                self._move_entries = list(self.selected_entries)
                return
            if (event.modifiers() & Qt.KeyboardModifier.ShiftModifier
                    or self._q_held):
                self.mode = "line"
                self._line_snap = bool(
                    event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                self._line_arrow = self._q_held
                self.line_anchor = QPointF(pos)
                self.update()
            elif self.circle_tool:
                self.mode = "circle"
                self._line_snap = bool(
                    event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                self.line_anchor = QPointF(pos)
                self._circle_center_fixed = (
                    self._snap(pos) if self._line_snap else None)
                self.update()
            elif self.mouse_draw:
                self.mode = "stroke"
                self._active_tl = self._ensure_stroke_layer()["tl"]
                self.cur_points = [(pos.x(), pos.y(), 1.0)]
            else:
                self.mode = "select"
                self._sel_anchor = QPointF(pos)
                self.selected_entries = []
                if self.selected_image is not None:
                    self.selected_image = None
                self.update()

    def mouseMoveEvent(self, event):
        if self._pen_emulation() and event.buttons():
            return
        pos = self._to_scene(event.position())
        self.cursor_scene = pos
        if self.mode is None and not event.buttons():
            self._update_hover_cursor(pos)
            return
        if self.mode == "pan":
            delta = event.position() - self._pan_last
            self._pan_last = event.position()
            self.offset -= delta / self.zoom
            self._clamp_offset()
            self.update()
        elif self.mode == "erase":
            self.erase_at(pos)
        elif self.mode == "stroke":
            self._stroke_add_point(pos, 1.0)
        elif self.mode == "line":
            self._line_snap = bool(
                event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            self.update()
        elif self.mode == "circle":
            self._line_snap = bool(
                event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            self.update()
        elif self.mode == "drag":
            self._clear_redo()
            entry, offset = self.drag
            entry["pos"] = QPointF(pos - offset)
            self._bbox_cache.pop(id(entry), None)
            self.update()
        elif self.mode == "resize":
            self._resize_image_to(pos)
        elif self.mode == "rotate":
            self._rotate_image_to(pos)
        elif self.mode == "select":
            self.update()
        elif self.mode == "move_sel":
            self._clear_redo()
            delta = pos - self._move_last
            self._move_last = QPointF(pos)
            if self._move_total is not None:
                self._move_total += delta
            ids = set()
            for e in self.selected_entries:
                self._translate_entry(e, delta)
                ids.add(id(e))
            self._rebuild_layers_with(ids)
            self.update()

    @staticmethod
    def _bbox_contains(bbox, pos):
        return bbox[0] <= pos.x() <= bbox[2] and bbox[1] <= pos.y() <= bbox[3]

    @staticmethod
    def _bbox_intersects_rect(bbox, rect):
        return (bbox[0] <= rect.right() and bbox[2] >= rect.left()
                and bbox[1] <= rect.bottom() and bbox[3] >= rect.top())

    def _translate_entry(self, e, d):
        self._bbox_cache.pop(id(e), None)
        if e["kind"] == "stroke":
            e["pts"] = [(x + d.x(), y + d.y(), f) for x, y, f in e["pts"]]
        elif e["kind"] == "line":
            e["p1"] += d
            e["p2"] += d
        elif e["kind"] == "axes":
            e["center"] += d
        elif e["kind"] == "curve":
            e["segs"] = [[(x + d.x(), y + d.y()) for x, y in seg]
                         for seg in e["segs"]]
        elif e["kind"] == "text":
            e["pos"] += d
        elif e["kind"] == "image":
            e["pos"] += d

    def _rebuild_layers_with(self, entry_ids):
        """Перестроить только те слои штрихов, где есть передвинутые записи."""
        for layer in self.layers:
            if layer["kind"] == "strokes" and any(
                    id(e) in entry_ids for e in layer["entries"]):
                tl = StrokeLayer()
                for e in layer["entries"]:
                    self._render_entry_to_layer(tl, e)
                layer["tl"] = tl

    @staticmethod
    def _clone_entry(e):
        """Независимая копия записи документа (изображения делят QPixmap,
        координаты и списки точек копируются)."""
        c = dict(e)
        k = e["kind"]
        if k == "stroke":
            c["pts"] = list(e["pts"])
        elif k == "line":
            c["p1"] = QPointF(e["p1"])
            c["p2"] = QPointF(e["p2"])
        elif k == "axes":
            c["center"] = QPointF(e["center"])
        elif k == "curve":
            c["segs"] = [list(seg) for seg in e["segs"]]
            if "seg_off" in e:
                c["seg_off"] = list(e["seg_off"])
        elif k == "text":
            c["pos"] = QPointF(e["pos"])
        elif k == "image":
            c["pos"] = QPointF(e["pos"])
        if "color" in e:
            c["color"] = QColor(e["color"])
        return c

    def _selection_for_copy(self):
        """Выделенные записи в порядке документа; если выделена только
        картинка — тоже даём её скопировать."""
        if self.selected_entries:
            selected = {id(e) for e in self.selected_entries}
            return [e for e in self.entries if id(e) in selected]
        if (self.selected_image is not None
                and self.selected_image in self.entries):
            return [self.selected_image]
        return []

    def select_all(self):
        """Ctrl+A: выделить все записи документа."""
        if not self.entries:
            return
        self.selected_entries = list(self.entries)
        self.selected_image = None
        self.update()
        self.window().statusBar().showMessage(
            f"Выбрано записей: {len(self.selected_entries)} · "
            "Del — удалить · Ctrl+C — копировать", 4000)

    def copy_selected(self):
        items = self._selection_for_copy()
        if not items:
            self.window().statusBar().showMessage(
                "Сначала выделите записи рамкой или кликните изображение", 3000)
            return False
        self.clipboard_entries = [self._clone_entry(e) for e in items]
        self._paste_counter = 0
        self.window().statusBar().showMessage(
            f"Скопировано объектов: {len(items)} · Ctrl+V — вставить", 3000)
        return True

    def _visible_scene_rect(self):
        return QRectF(self.offset.x(), self.offset.y(),
                      self.width() / self.zoom, self.height() / self.zoom)

    @staticmethod
    def _clamp_delta_to_scene(bbox, dx, dy):
        x0, y0, x1, y1 = bbox
        min_dx, max_dx = -x0, SCENE_W - x1
        min_dy, max_dy = -y0, SCENE_H - y1
        if min_dx <= max_dx:
            dx = min(max(dx, min_dx), max_dx)
        if min_dy <= max_dy:
            dy = min(max(dy, min_dy), max_dy)
        return dx, dy

    def _paste_delta(self, entries):
        """Смещение вставки: центр видимой области, смещение-серия,
        чтобы копия не совпала с оригиналом и осталась на экране."""
        boxes = [self._entry_bbox(e) for e in entries]
        bbox = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))
        view = self._visible_scene_rect()
        cx = (bbox[0] + bbox[2]) / 2.0
        cy = (bbox[1] + bbox[3]) / 2.0
        base_dx = view.center().x() - cx
        base_dy = view.center().y() - cy
        g = float(GRID_STEP)
        seq = ((0, 0), (g, 0), (0, g), (g, g),
               (-g, 0), (0, -g), (-g, -g), (g, -g), (-g, g))
        start = self._paste_counter % len(seq)
        for k in range(len(seq)):
            ox, oy = seq[(start + k) % len(seq)]
            dx, dy = self._clamp_delta_to_scene(
                bbox, base_dx + ox, base_dy + oy)
            if abs(dx) < 1e-6 and abs(dy) < 1e-6:
                continue  # нельзя положить точно поверх скопированного
            moved = QRectF(bbox[0] + dx, bbox[1] + dy,
                           bbox[2] - bbox[0], bbox[3] - bbox[1])
            if moved.intersects(view):
                return QPointF(dx, dy)
        # крайний случай: вид очень мал или объект больше сцены —
        # всё равно смещаем в пределах сцены
        dx, dy = self._clamp_delta_to_scene(
            bbox, base_dx + g, base_dy + g)
        return QPointF(dx, dy)

    def paste_entries(self):
        if not self.clipboard_entries:
            return False
        clones = [self._clone_entry(e) for e in self.clipboard_entries]
        delta = self._paste_delta(clones)
        for e in clones:
            self._translate_entry(e, delta)
        self.entries.extend(clones)
        self._push_undo(("add_many", clones))
        self.selected_entries = clones
        self.selected_image = (clones[0] if len(clones) == 1
                               and clones[0]["kind"] == "image" else None)
        self._paste_counter += 1
        self._rebuild_layers()
        self.update()
        self.window().statusBar().showMessage(
            f"Вставлено объектов: {len(clones)} · Ctrl+Z — отменить", 3000)
        return True

    def _on_clipboard_changed(self):
        """Системный буфер обмена изменился извне (в нём появилось новое
        содержимое, например изображение): скопированное выделение
        замещается новым содержимым буфера."""
        if self.clipboard_entries:
            self.clipboard_entries = []
            self._paste_counter = 0

    def paste(self):
        """Ctrl+V: выделенные записи из внутреннего буфера; если системный
        буфер обмена изменился извне — вставляется его содержимое
        (внешнее изображение замещает скопированное выделение)."""
        if self.clipboard_entries:
            return self.paste_entries()
        return self.paste_image()

    def _begin_resize(self, e, pos):
        """Начать растягивание: зафиксировать размер и центр на момент
        захвата. Это опора всего жеста: если считать масштаб от текущего
        preview-размера, центр смещается при каждом изменении, и даже
        неподвижный курсор даёт осцилляцию размера между двумя значениями."""
        w, h = self._img_size(e)
        cx, cy = self._image_center(e)
        self._resize_info = (e, float(w), float(h), cx, cy)
        self._resize_axis = None
        self.mode = "resize"

    def _resize_image_to(self, pos):
        """Пропорциональный resize: масштаб один по обеим осям, считается
        от зафиксированных при захвате центра и размера (см. _begin_resize)."""
        self._clear_redo()
        e, ow, oh, cx, cy = self._resize_info
        # локальные координаты курсора относительно опорного центра
        dx, dy = pos.x() - cx, pos.y() - cy
        a = math.radians(-e.get("angle", 0))
        ca, sa = math.cos(a), math.sin(a)
        lx = dx * ca - dy * sa
        ly = dx * sa + dy * ca
        w2, h2 = ow / 2.0, oh / 2.0
        if w2 <= 0 or h2 <= 0:
            return
        sx = lx / w2
        sy = ly / h2
        # доминирующая ось фиксируется с гистерезисом: иначе при тяге по
        # диагонали шум курсора каждый кадр переключает max(sx, sy), и
        # размер изображения дёргается туда-сюда («моргает»)
        if self._resize_axis is None:
            self._resize_axis = "x" if sx >= sy else "y"
        elif self._resize_axis == "x" and sy > sx * 1.02:
            self._resize_axis = "y"
        elif self._resize_axis == "y" and sx > sy * 1.02:
            self._resize_axis = "x"
        s = sx if self._resize_axis == "x" else sy
        # минимальный размер 20 px по меньшей стороне (и защита от
        # ухода курсора за центр — отрицательного масштаба)
        s = max(s, 20.0 / ow, 20.0 / oh)
        new_w = ow * s
        new_h = oh * s
        # только preview-размер: сам pixmap не трогаем до отпускания кнопки,
        # иначе изображение моргает на каждое движение указателя
        e["_pv"] = (max(1.0, new_w), max(1.0, new_h))
        # масштабированную копию пересоздаём редко (раз в ~6 px): качественный
        # ресайз полноразмерного оригинала на каждое событие мыши — дорого,
        # отсюда рывки кадров при растягивании
        cs = e.get("_pv_csize", (0.0, 0.0))
        if (e.get("_pv_cache") is None or abs(new_w - cs[0]) > 6
                or abs(new_h - cs[1]) > 6):
            e["_pv_cache"] = e["orig"].scaled(
                max(1, int(round(new_w))), max(1, int(round(new_h))),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.FastTransformation)
            e["_pv_csize"] = (new_w, new_h)
        self._bbox_cache.pop(id(e), None)
        self.update()

    def _rotate_image_to(self, pos):
        self._clear_redo()
        e, offset_deg = self._rotate_info
        cx, cy = self._image_center(e)
        ptr = math.degrees(math.atan2(pos.y() - cy, pos.x() - cx))
        angle = ptr - offset_deg
        # нормализация в (-180, 180]
        angle = (angle + 180) % 360 - 180
        # прилипание к углам, кратным 90°
        snap = round(angle / 90) * 90
        if abs(angle - snap) <= 7:
            angle = float(snap)
            if angle > 180:
                angle -= 360
        e["angle"] = angle
        self._bbox_cache.pop(id(e), None)
        self.update()

    def _update_hover_cursor(self, pos):
        if self.eraser_tool:
            return  # курсор ластика уже установлен
        arrow = (self.selected_image is not None
                 and (self._over_resize_handle(self.selected_image, pos)
                      or self._over_rotate_handle(self.selected_image, pos)))
        if arrow:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self._apply_pen_cursor()

    def mouseReleaseEvent(self, event):
        if self._pen_emulation():
            return
        self._finish_input(self._to_scene(event.position()))

    def mouseDoubleClickEvent(self, event):
        pass

    # ---------- клавиша Q (линия-стрелка) ----------

    @staticmethod
    def _physical_key(event, qt_key, win_vk, mac_vk):
        """Физическая клавиша независимо от раскладки: Windows VK или
        macOS ANSI virtual key; плюс обычный Qt key как запасной вариант."""
        if event.key() == qt_key:
            return True
        code = None
        if sys.platform == "win32":
            code = win_vk
        elif sys.platform == "darwin":
            code = mac_vk
        return code is not None and event.nativeVirtualKey() == code

    @staticmethod
    def _is_q(event):
        """Физическая клавиша Q в любой раскладке."""
        return Canvas._physical_key(event, Qt.Key.Key_Q, 0x51, 12)

    @staticmethod
    def _is_m(event):
        """Физическая клавиша M в любой раскладке."""
        return Canvas._physical_key(event, Qt.Key.Key_M, 0x4D, 46)

    def keyPressEvent(self, event):
        if self._is_q(event):
            if not event.isAutoRepeat():
                self._q_held = True
            event.accept()
            return
        if self._is_m(event):
            if not event.isAutoRepeat():
                btn = getattr(self.window(), "mousemode_btn", None)
                if btn is not None:
                    btn.toggle()
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if self._is_q(event):
            if not event.isAutoRepeat():
                self._q_held = False
            event.accept()
            return
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event):
        # окно потеряло фокус — считаем Q отпущенной, чтобы не «залипла»
        self._q_held = False
        super().focusOutEvent(event)

    # ---------- штрих ----------

    @staticmethod
    def _factor(pressure):
        if pressure <= 0:
            return 1.0
        # нормальное письмо (~50% нажатия) = толщина мыши
        return min(1.0, 0.3 + 1.4 * pressure)

    def _stroke_add_point(self, pos, factor):
        raw = self.cur_points
        if not raw or self._active_tl is None:
            return
        lx, ly, _ = raw[-1]
        if math.hypot(pos.x() - lx, pos.y() - ly) < MIN_POINT_DIST:
            return
        raw.append((pos.x(), pos.y(), factor))
        n = len(raw)
        if n == 1:
            return
        if n == 2:
            self._live = [(raw[0][0], raw[0][1], raw[0][2])]
            return
        a, b, cpt = raw[-3], raw[-2], raw[-1]
        s = (0.25 * a[0] + 0.5 * b[0] + 0.25 * cpt[0],
             0.25 * a[1] + 0.5 * b[1] + 0.25 * cpt[1],
             (a[2] + b[2] + cpt[2]) / 3.0)
        self._live.append(s)
        lv = self._live
        k = len(lv) - 1
        if k < 1:
            return
        ctrl, newp = lv[k - 1], lv[k]
        if k == 1:
            m0 = (ctrl[0], ctrl[1])
        else:
            m0 = ((lv[k - 2][0] + ctrl[0]) / 2.0, (lv[k - 2][1] + ctrl[1]) / 2.0)
        m1 = ((ctrl[0] + newp[0]) / 2.0, (ctrl[1] + newp[1]) / 2.0)
        w = max(self.pen_width * (ctrl[2] + newp[2]) / 2.0, MIN_STROKE_W)
        color = QColor(self.color)
        path = QPainterPath(QPointF(m0[0], m0[1]))
        path.quadTo(QPointF(ctrl[0], ctrl[1]), QPointF(m1[0], m1[1]))

        def draw(p):
            p.setPen(QPen(color, w, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.drawPath(path)

        bbox = (min(m0[0], m1[0], ctrl[0]) - w - 2, min(m0[1], m1[1], ctrl[1]) - w - 2,
                max(m0[0], m1[0], ctrl[0]) + w + 2, max(m0[1], m1[1], ctrl[1]) + w + 2)
        self._active_tl.draw(bbox, draw)
        self.update()

    def _finish_input(self, pos):
        if self.mode == "stroke" and self.cur_points:
            entry = {
                "kind": "stroke",
                "pts": _smooth_points(self.cur_points),
                "color": QColor(self.color),
                "width": self.pen_width,
            }
            layer = self._ensure_stroke_layer()
            # убрать живую отрисовку из плиток и отрисовать финальную версию,
            # чтобы слой всегда точно соответствовал записям
            raw_bbox = self._entry_bbox(
                {"kind": "stroke", "pts": self.cur_points,
                 "width": self.pen_width}, margin=self.pen_width + 4)
            layer["tl"].rebuild_region(raw_bbox, layer["entries"], self)
            layer["entries"].append(entry)
            self.entries.append(entry)
            self._push_undo(("add", entry))
            self._render_entry_to_layer(layer["tl"], entry)
            self.update()
        elif self.mode == "line" and self.line_anchor is not None:
            entry = {
                "kind": "line",
                "p1": self._snap(self.line_anchor),
                "p2": self._snap(pos),
                "color": QColor(self.color),
                "width": self.pen_width,
                "dash": self.dashed_lines,
                "arrow": self._line_arrow,
            }
            self.entries.append(entry)
            self._push_undo(("add", entry))
            layer = self._ensure_stroke_layer()
            layer["entries"].append(entry)
            self._render_entry_to_layer(layer["tl"], entry)
            self.update()
        elif self.mode == "circle" and self.line_anchor is not None:
            center, r = self._circle_params(pos)
            if r >= 2.0:
                # замкнутая ломаная ~по 3 px на сегмент — гладко и при зуме
                n = max(48, min(720, int(2 * math.pi * r / 3.0)))
                pts = [(center.x() + r * math.cos(2 * math.pi * i / n),
                        center.y() + r * math.sin(2 * math.pi * i / n))
                       for i in range(n + 1)]
                entry = {"kind": "curve", "segs": [pts],
                         "color": QColor(self.color), "width": self.pen_width,
                         "dash": self.dashed_lines}
                # центр — отдельной точкой: стирается/удаляется независимо
                dot = {"kind": "stroke",
                       "pts": [(center.x(), center.y(), 1.0)],
                       "color": QColor(self.color), "width": self.pen_width}
                self.entries.extend((entry, dot))
                self._push_undo(("add_many", [entry, dot]))
                layer = self._ensure_stroke_layer()
                layer["entries"].extend((entry, dot))
                self._render_entry_to_layer(layer["tl"], entry)
                self._render_entry_to_layer(layer["tl"], dot)
                self.update()
        elif self.mode == "resize" and self._resize_info is not None:
            # финальный пересчёт preview-размера в хорошем качестве
            e = self._resize_info[0]
            iw, ih = self._img_size(e)
            e.pop("_pv", None)
            e.pop("_pv_cache", None)
            e.pop("_pv_csize", None)
            iw, ih = max(1, int(round(iw))), max(1, int(round(ih)))
            if (iw, ih) != (e["pix"].width(), e["pix"].height()):
                old_pix = e["pix"]
                e["pix"] = e["orig"].scaled(
                    iw, ih,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                # одно действие undo на весь жест растягивания
                self._push_undo(("resize_image", (e, old_pix, e["pix"])))
            self._bbox_cache.pop(id(e), None)
            self.update()
        elif self.mode == "rotate":
            # поворот — одно действие undo на весь жест
            if (self._rotate_info is not None
                    and self._rotate_start is not None):
                e = self._rotate_info[0]
                cur = e.get("angle", 0.0)
                if abs(cur - self._rotate_start) > 1e-9:
                    self._push_undo(
                        ("rotate_image", (e, self._rotate_start, cur)))
            self.update()
        elif self.mode == "drag" and self.drag is not None:
            # перенос изображения — отменяемое действие (одно на весь жест)
            entry, _off = self.drag
            if self._drag_start is not None:
                d = entry["pos"] - self._drag_start
                if abs(d.x()) > 1e-9 or abs(d.y()) > 1e-9:
                    self._push_undo(("move", ([entry], QPointF(d))))
            self.update()
        elif self.mode == "select" and self._sel_anchor is not None:
            rect = QRectF(self._sel_anchor, pos).normalized()
            self.selected_entries = [
                e for e in self.entries
                if self._bbox_intersects_rect(self._entry_bbox(e), rect)]
            if self.selected_entries:
                self.selected_image = None
                self.window().statusBar().showMessage(
                    f"Выбрано записей: {len(self.selected_entries)} · "
                    "Del — удалить · перетаскивание — переместить", 4000)
            self.update()
        elif self.mode == "move_sel":
            # перенос выделенного — одно действие undo на весь жест:
            # записи уже сдвинуты, кладём суммарный сдвиг в стек
            if (self._move_entries and self._move_total is not None
                    and (abs(self._move_total.x()) > 1e-9
                         or abs(self._move_total.y()) > 1e-9)):
                self._push_undo(
                    ("move", (list(self._move_entries),
                              QPointF(self._move_total))))
            self.update()
        self.mode = None
        self.cur_points = []
        self._live = []
        self.line_anchor = None
        self._line_snap = False
        self._line_arrow = False
        self._circle_center_fixed = None
        self.drag = None
        self._pan_last = None
        self._erase_group = None
        self._sel_anchor = None
        self._move_last = None
        self._move_total = None
        self._move_entries = []
        self._drag_start = None
        self._resize_info = None
        self._rotate_info = None
        self._rotate_start = None
        self._resize_axis = None

    # ---------- ластик ----------

    def _stroke_at(self, pos):
        r = ERASER_RADIUS + self.pen_width
        for e in self.entries:
            if e["kind"] == "stroke":
                pts = e["pts"]
                if len(pts) == 1:
                    if math.hypot(pos.x() - pts[0][0],
                                  pos.y() - pts[0][1]) < r + e["width"] / 2:
                        return True
                    continue
                for i in range(1, len(pts)):
                    if _dist_to_segment(pos.x(), pos.y(),
                                        pts[i - 1][0], pts[i - 1][1],
                                        pts[i][0], pts[i][1]) < r + e["width"] / 2:
                        return True
            elif e["kind"] == "line":
                if _dist_to_segment(pos.x(), pos.y(),
                                    e["p1"].x(), e["p1"].y(),
                                    e["p2"].x(), e["p2"].y()) < r + e["width"] / 2:
                    return True
            elif e["kind"] == "axes":
                if self._axes_hit(e, pos, r):
                    return True
            elif e["kind"] == "curve":
                if self._curve_hit(e, pos, r):
                    return True
            elif e["kind"] == "text":
                x0, y0, x1, y1 = self._entry_bbox(e, margin=0)
                dx = max(x0 - pos.x(), 0.0, pos.x() - x1)
                dy = max(y0 - pos.y(), 0.0, pos.y() - y1)
                if math.hypot(dx, dy) < r:
                    return True
        return False

    def _axes_hit(self, e, pos, r):
        cx, cy = e["center"].x(), e["center"].y()
        return (_dist_to_segment(pos.x(), pos.y(),
                                 cx - e["hw"], cy, cx + e["hw"], cy) < r + e["width"]
                or _dist_to_segment(pos.x(), pos.y(),
                                    cx, cy - e["hh"], cx, cy + e["hh"]) < r + e["width"])

    def _curve_hit(self, e, pos, r):
        for seg in e["segs"]:
            for i in range(1, len(seg)):
                if _dist_to_segment(pos.x(), pos.y(),
                                    seg[i - 1][0], seg[i - 1][1],
                                    seg[i][0], seg[i][1]) < r + e["width"]:
                    return True
        return False

    # ---------- ластик: круглый, радиус зависит от скорости ----------

    def _begin_erase(self):
        """Начало прохода ластика: сбросить скорость (радиус от базового)."""
        self._erase_last = None
        self._erase_speed = 0.0

    def _erase_radius(self, pos, now=None):
        """Радиус ластика: базовый при медленном ведении, растёт до
        максимума только на действительно быстрых движениях. Мёртвая зона
        отсекает шум скорости: перо шлёт события с высокой частотой, и
        из-за маленьких dt даже аккуратное движение легко разгоняло
        радиус — ластик сносил линию целиком вместо куска."""
        if now is None:
            now = time.time()
        if self._erase_last is not None:
            lp, lt = self._erase_last
            dt = now - lt
            if dt > 1e-4:
                v = math.hypot(pos.x() - lp.x(), pos.y() - lp.y()) / dt
                self._erase_speed = 0.6 * self._erase_speed + 0.4 * v
        self._erase_last = (QPointF(pos), now)
        v = self._erase_speed
        if v <= ERASER_SPEED_LO:
            return ERASER_BASE
        t = min((v - ERASER_SPEED_LO) / (ERASER_SPEED_HI - ERASER_SPEED_LO), 1.0)
        return ERASER_BASE + (ERASER_MAX - ERASER_BASE) * t

    def _axes_to_lines(self, e):
        """Разложить запись «оси» на отдельные линии (оси, стрелки, деления) —
        так же, как их рисует _render_axes."""
        cx, cy = e["center"].x(), e["center"].y()
        hw, hh, w = e["hw"], e["hh"], e["width"]
        col = e["color"]
        lines = []

        def add(x1, y1, x2, y2, wid=None):
            lines.append({"kind": "line",
                          "p1": QPointF(x1, y1), "p2": QPointF(x2, y2),
                          "color": QColor(col),
                          "width": w if wid is None else wid})
        add(cx - hw, cy, cx + hw, cy)
        add(cx, cy + hh, cx, cy - hh)
        ah = 12.0 + w
        add(cx + hw + ah, cy, cx + hw, cy - ah * 0.45)
        add(cx + hw + ah, cy, cx + hw, cy + ah * 0.45)
        add(cx, cy - hh - ah, cx - ah * 0.45, cy - hh)
        add(cx, cy - hh - ah, cx + ah * 0.45, cy - hh)
        tw = max(w - 0.5, 1.0)
        tick = 5.0
        x = cx - int(hw // GRID_STEP) * GRID_STEP
        while x <= cx + hw:
            if abs(x - cx) > 1:
                add(x, cy - tick, x, cy + tick, tw)
            x += GRID_STEP
        y = cy - int(hh // GRID_STEP) * GRID_STEP
        while y <= cy + hh:
            if abs(y - cy) > 1:
                add(cx - tick, y, cx + tick, y, tw)
            y += GRID_STEP
        return lines

    def _erase_entry(self, e, pos, r):
        """Вырезать круг ластика из записи.
        None — запись не задета; иначе список записей-замен
        (пустой список — запись стёрта целиком)."""
        k = e["kind"]
        if k == "stroke":
            runs = _cut_points(e["pts"], pos.x(), pos.y(), r + e["width"] / 2)
            if runs is None:
                return None
            return [{"kind": "stroke", "pts": run,
                     "color": QColor(e["color"]), "width": e["width"]}
                    for run in runs]
        if k == "line":
            segs = _cut_line_intervals(e["p1"], e["p2"],
                                       pos.x(), pos.y(), r + e["width"] / 2)
            if segs is None:
                return None
            return [{"kind": "line", "p1": p1, "p2": p2,
                     "color": QColor(e["color"]), "width": e["width"],
                     "dash": e.get("dash", False),
                     "dash_off": (e.get("dash_off", 0.0)
                                  + math.hypot(p1.x() - e["p1"].x(),
                                               p1.y() - e["p1"].y())),
                     "arrow": e.get("arrow", False) and p2 == e["p2"]}
                    for p1, p2 in segs]
        if k == "curve":
            touched = False
            out_segs = []
            out_offs = []
            base_offs = e.get("seg_off") or [0.0] * len(e["segs"])
            for si, seg in enumerate(e["segs"]):
                base = base_offs[si] if si < len(base_offs) else 0.0
                runs = _cut_points_len(seg, pos.x(), pos.y(),
                                       r + e["width"] / 2)
                if runs is None:
                    out_segs.append(seg)
                    out_offs.append(base)
                else:
                    touched = True
                    for run, start_len in runs:
                        out_segs.append(run)
                        out_offs.append(base + start_len)
            if not touched:
                return None
            if not out_segs:
                return []
            return [{"kind": "curve", "segs": out_segs,
                     "color": QColor(e["color"]), "width": e["width"],
                     "dash": e.get("dash", False),
                     "seg_off": out_offs}]
        if k == "axes":
            if not self._axes_hit(e, pos, r):
                return None
            # частичное стирание: разложить оси на линии и резать их
            out = []
            for le in self._axes_to_lines(e):
                parts = self._erase_entry(le, pos, r)
                if parts is None:
                    out.append(le)
                else:
                    out.extend(parts)
            return out
        if k == "text":
            # надпись стирается целиком, если круг задел её рамку
            x0, y0, x1, y1 = self._entry_bbox(e, margin=0)
            dx = max(x0 - pos.x(), 0.0, pos.x() - x1)
            dy = max(y0 - pos.y(), 0.0, pos.y() - y1)
            return [] if math.hypot(dx, dy) < r else None
        return None

    def _eb(self, e):
        """bbox записи с кэшем по id (записи в документе неизменны)."""
        key = id(e)
        b = self._bbox_cache.get(key)
        if b is None:
            b = self._entry_bbox(e)
            self._bbox_cache[key] = b
        return b

    def erase_at(self, pos, now=None):
        r = self._erase_radius(pos, now)
        px, py = pos.x(), pos.y()
        removed = []             # (индекс в старом списке, запись)
        added = []
        out = []
        parts_by_id = {}
        for idx, e in enumerate(self.entries):
            # быстрый отсев: точка ластика дальше r от рамки записи —
            # запись гарантированно не задета, геометрию не режем
            x0, y0, x1, y1 = self._eb(e)
            dx = max(x0 - px, 0.0, px - x1)
            dy = max(y0 - py, 0.0, py - y1)
            if dx * dx + dy * dy > (r + 4.0) ** 2:
                out.append(e)
                continue
            parts = self._erase_entry(e, pos, r)
            if parts is None:
                out.append(e)
            else:
                removed.append((idx, e, parts))
                parts_by_id[id(e)] = parts
                added.extend(parts)
                out.extend(parts)
        if not removed:
            return
        self.entries = out
        self.selected_entries = [
            e for e in self.selected_entries if e in self.entries]
        # весь проход ластика (пока зажата кнопка/перо) — одна undo-группа
        grouping = self.mode == "erase" or self._wt_mode == "erase"
        if grouping and self._erase_group is not None:
            grp = self._erase_group
        else:
            grp = []
            self._push_undo(("erase", grp))
            if grouping:
                self._erase_group = grp
        grp.append((removed, added))
        # локальная перерисовка: перестраиваем только задетые слои и только
        # в области круга ластика (раньше — полный rebuild всех слоёв)
        maxw = max((e.get("width", 4.0) for _i, e, _p in removed), default=4.0)
        m = r + maxw + 8.0
        x0, y0, x1, y1 = px - m, py - m, px + m, py + m
        # область перерисовки — рамки ВСЕХ задетых записей: разрезанные
        # части могут рисоваться иначе, чем исходная запись (например,
        # стрелки и деления разложенных осей), поэтому вся запись должна
        # быть перерисована из новых частей, а не только круг ластика
        for _i, e, _p in removed:
            ex0, ey0, ex1, ey1 = self._eb(e)
            x0, y0 = min(x0, ex0), min(y0, ey0)
            x1, y1 = max(x1, ex1), max(y1, ey1)
        bbox = (x0, y0, x1, y1)
        for layer in self.layers:
            if layer["kind"] != "strokes":
                continue
            if not any(id(e) in parts_by_id for e in layer["entries"]):
                continue
            new_entries = []
            for e in layer["entries"]:
                p2 = parts_by_id.get(id(e))
                if p2 is None:
                    new_entries.append(e)
                else:
                    new_entries.extend(p2)
            layer["entries"] = new_entries
            layer["tl"].rebuild_region(bbox, new_entries, self)
            # плитки очищенной области, которые не покрывает ни одна запись
            # слоя, убираем: свежий рендер из entries их не создаёт
            tx0 = int(max(0.0, x0)) // TILE
            tx1 = int(min(SCENE_W - 1.0, x1)) // TILE
            ty0 = int(max(0.0, y0)) // TILE
            ty1 = int(min(SCENE_H - 1.0, y1)) // TILE
            for tx in range(tx0, tx1 + 1):
                for ty in range(ty0, ty1 + 1):
                    key = (tx, ty)
                    if key not in layer["tl"].tiles:
                        continue
                    rx0, ry0 = tx * TILE, ty * TILE
                    rx1, ry1 = rx0 + TILE, ry0 + TILE
                    covered = False
                    for e in new_entries:
                        eb = self._eb(e)
                        if (eb[0] <= rx1 and eb[2] >= rx0
                                and eb[1] <= ry1 and eb[3] >= ry0):
                            covered = True
                            break
                    if not covered:
                        del layer["tl"].tiles[key]
        self.update()

    # ---------- изображения ----------

    def _image_at(self, pos):
        for e in reversed(self.entries):
            if e["kind"] == "image":
                loc = self._to_image_local(e, pos)
                if (abs(loc.x()) <= e["pix"].width() / 2.0
                        and abs(loc.y()) <= e["pix"].height() / 2.0):
                    return e
        return None

    def paste_image(self):
        clip = QGuiApplication.clipboard()
        img = clip.image()
        if img.isNull():
            mime = clip.mimeData()
            if mime.hasUrls():
                for url in mime.urls():
                    pix = QPixmap(url.toLocalFile())
                    if not pix.isNull():
                        img = pix.toImage()
                        break
        if img.isNull():
            self.window().statusBar().showMessage(
                "В буфере обмена нет изображения", 3000)
            return False

        pix = QPixmap.fromImage(img)
        max_w = max(self.width() / self.zoom - 80, 200)
        max_h = max(self.height() / self.zoom - 80, 200)
        if pix.width() > max_w or pix.height() > max_h:
            pix = pix.scaled(int(max_w), int(max_h),
                             Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
        pos = QPointF(self.cursor_scene.x() - pix.width() / 2,
                      self.cursor_scene.y() - pix.height() / 2)
        # clamp по сцене; картинка может оказаться шире сцены (очень
        # маленький масштаб) — тогда верхний предел 0, а не отрицательный
        max_x = max(0.0, float(SCENE_W - pix.width()))
        max_y = max(0.0, float(SCENE_H - pix.height()))
        pos.setX(min(max(pos.x(), 0.0), max_x))
        pos.setY(min(max(pos.y(), 0.0), max_y))
        entry = {"kind": "image", "pix": pix, "orig": pix, "pos": pos, "angle": 0.0}
        self.entries.append(entry)
        self._push_undo(("add", entry))
        self.layers.append({"kind": "image", "entry": entry})
        self.selected_image = entry
        self.update()
        self.window().statusBar().showMessage(
            "Изображение выбрано: тяните за уголок рамки — размер, "
            "за центр — перемещение. Клик мимо — снять выбор.", 5000)
        return True

    def delete_selected_image(self):
        """Del: удалить выбранное изображение (отменяется по Ctrl+Z)."""
        e = self.selected_image
        if e is None:
            return
        if e in self.entries:
            idx = self.entries.index(e)
            self.entries.remove(e)
            self._push_undo(("erase", [([(idx, e, [])], [])]))
        self.selected_image = None
        self._rebuild_layers()
        self.update()

    def delete_selected(self):
        """Del: удалить выделенные рамкой записи; если выделения нет —
        выбранное изображение (всё отменяется по Ctrl+Z)."""
        if self.selected_entries:
            removed = [(self.entries.index(e), e, [])
                       for e in self.selected_entries if e in self.entries]
            self.selected_entries = []
            if not removed:
                return
            for _, e, _parts in removed:
                self.entries.remove(e)
                if e is self.selected_image:
                    self.selected_image = None
            self._push_undo(("erase", [(removed, [])]))
            self._rebuild_layers()
            self.update()
        else:
            self.delete_selected_image()

    # ---------- координатные оси ----------

    def add_axes(self):
        """Вставить координатные оси: начало — в ближайшем узле сетки,
        чтобы деления на осях совпадали с клетками фона."""
        vw = self.width() / self.zoom
        vh = self.height() / self.zoom
        cx = float(round((self.offset.x() + vw / 2) / GRID_STEP) * GRID_STEP)
        cy = float(round((self.offset.y() + vh / 2) / GRID_STEP) * GRID_STEP)
        hw = max(240.0, min(900.0, vw * 0.42))
        hh = max(200.0, min(700.0, vh * 0.42))
        entry = {"kind": "axes", "center": QPointF(cx, cy),
                 "hw": hw, "hh": hh,
                 "color": QColor(self.color), "width": 2.0}
        self.entries.append(entry)
        self._push_undo(("add", entry))
        layer = self._ensure_stroke_layer()
        layer["entries"].append(entry)
        self._render_entry_to_layer(layer["tl"], entry)
        self.update()
        self.window().statusBar().showMessage(
            "Оси добавлены в центр вида · Ctrl+Z — убрать", 3000)

    # ---------- график по формуле ----------

    def add_text(self, text=None, size=None, latex=None):
        """Надпись в точке курсора. Без аргументов — диалог:
        текст, размер шрифта, обычный текст или LaTeX."""
        if not isinstance(text, str):
            text = None  # clicked кнопки передаёт bool — игнорируем
        if text is None:
            dlg = TextDialog(self)
            if (dlg.exec() != QDialog.DialogCode.Accepted
                    or not dlg.text().strip()):
                return False
            text, size, latex = dlg.text(), dlg.size(), dlg.is_latex()
        entry = {"kind": "text",
                 "pos": QPointF(0, 0),
                 "text": text, "size": float(size), "latex": bool(latex),
                 "color": QColor(self.color)}
        # надпись появляется в центре видимой области
        w, asc, desc = _text_extent(entry)
        cx = self.offset.x() + self.width() / 2 / self.zoom
        cy = self.offset.y() + self.height() / 2 / self.zoom
        entry["pos"] = QPointF(cx - w / 2, cy + (asc - desc) / 2)
        # не выпускать надпись за края сцены
        entry["pos"].setX(min(max(entry["pos"].x(), 0.0),
                              max(0.0, SCENE_W - w)))
        entry["pos"].setY(min(max(entry["pos"].y(), asc),
                              max(asc, SCENE_H - desc)))
        self.entries.append(entry)
        self._push_undo(("add", entry))
        layer = self._ensure_stroke_layer()
        layer["entries"].append(entry)
        self._render_entry_to_layer(layer["tl"], entry)
        self.update()
        self.window().statusBar().showMessage(
            "Надпись добавлена · Ctrl+Z — убрать", 3000)
        return True

    def add_graph(self, formula=None):
        """Построить график f(x) или кривую по уравнению F(x, y) = 0.
        x, y — в клетках от начала координат (последних осей;
        без осей — от центра вида). Степень: ^ или **."""
        if not isinstance(formula, str):
            formula = None  # сигнал clicked кнопки передаёт bool — игнорируем
        if formula is None:
            formula, ok = QInputDialog.getText(
                self, "График / кривая",
                "f(x) или уравнение от x и y; степень — ^ или **,\n"
                "умножение можно опускать: 4x, 2sin(x), x(x+1).\n"
                "Примеры: sin(x) · x^2/10 · y = x^2 - 4x + 3\n"
                "x^2 + y^2 = 25 · x^3 - x - y = 0\n"
                "(x, y — в клетках от начала координат)",
                text="sin(x)")
            if not ok:
                return False
        formula = formula.strip()
        if not formula or "__" in formula:
            return False
        try:
            formula = _normalize_formula(formula)
        except ValueError:
            self.window().statusBar().showMessage(
                f"Ошибка в формуле: {formula}", 4000)
            return False

        ns = {name: getattr(math, name)
              for name in dir(math) if not name.startswith("_")}
        ns.update({"abs": abs, "min": min, "max": max,
                   "pow": pow, "round": round})

        # вид ввода: явная f(x) / y = f(x) / уравнение F(x, y) = 0
        explicit = None
        implicit = None
        if "=" in formula:
            lhs, rhs = (s.strip() for s in formula.split("=", 1))
            if lhs == "y" and "y" not in rhs:
                explicit = rhs
            else:
                implicit = f"({lhs}) - ({rhs})"
        elif "y" in formula:
            implicit = formula
        else:
            explicit = formula
        try:
            code = compile(explicit if explicit is not None else implicit,
                           "<formula>", "eval")
        except SyntaxError as exc:
            self.window().statusBar().showMessage(
                f"Ошибка в формуле «{formula}»: {exc.msg}", 4000)
            return False

        # система координат: последние добавленные оси, иначе центр вида
        ax = None
        for e in reversed(self.entries):
            if e["kind"] == "axes":
                ax = e
                break
        if ax is not None:
            cx, cy = ax["center"].x(), ax["center"].y()
            hw, hh = ax["hw"], ax["hh"]
        else:
            vw = self.width() / self.zoom
            vh = self.height() / self.zoom
            cx = float(round((self.offset.x() + vw / 2) / GRID_STEP) * GRID_STEP)
            cy = float(round((self.offset.y() + vh / 2) / GRID_STEP) * GRID_STEP)
            # при zoom out вид огромен — ограничиваем размер графика
            # теми же пределами, что у кнопки «Оси», иначе график
            # растягивается на весь экран
            hw = min(vw * 0.45, 900.0)
            hh = min(vh * 0.45, 700.0)

        if explicit is not None:
            # явная функция: сэмплирование, разрыв при выходе за рамки
            segments = []
            cur = []
            x = cx - hw
            while x <= cx + hw + 1e-9:
                xc = (x - cx) / GRID_STEP
                try:
                    val = eval(code, {"__builtins__": {}}, dict(ns, x=xc))
                    ys = cy - float(val) * GRID_STEP
                except Exception:
                    ys = None
                if (ys is None or not math.isfinite(ys)
                        or ys < cy - hh or ys > cy + hh):
                    if len(cur) >= 2:
                        segments.append(cur)
                    cur = []
                else:
                    cur.append((x, ys))
                x += 2.0
            if len(cur) >= 2:
                segments.append(cur)
        else:
            # marching squares по сетке — заметная работа на больших
            # областях: показываем курсор ожидания
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                segments = self._implicit_segments(code, ns, cx, cy, hw, hh)
            finally:
                QApplication.restoreOverrideCursor()

        if not segments:
            self.window().statusBar().showMessage(
                f"График не построен — проверьте формулу: {formula}", 4000)
            return False

        entry = {"kind": "curve", "segs": segments,
                 "color": QColor(self.color), "width": self.pen_width,
                 "dash": self.dashed_lines}
        self.entries.append(entry)
        self._push_undo(("add", entry))
        layer = self._ensure_stroke_layer()
        layer["entries"].append(entry)
        self._render_entry_to_layer(layer["tl"], entry)
        self.update()
        # график строится у осей, а они могут быть далеко за экраном:
        # предупреждаем, иначе кажется, что кнопка «не сработала»
        x0, y0, x1, y1 = self._entry_bbox(entry)
        if not self._visible_scene_rect().intersects(
                QRectF(x0, y0, x1 - x0, y1 - y0)):
            self.window().statusBar().showMessage(
                f"График построен вне видимой области (около осей): "
                f"{formula} · Ctrl+0 — в центр доски", 5000)
        else:
            self.window().statusBar().showMessage(
                f"График построен: {formula} · Ctrl+Z — убрать", 4000)
        return True

    def _implicit_segments(self, code, ns, cx, cy, hw, hh):
        """Отрезки кривой F(x, y) = 0 методом marching squares.
        x, y в формуле — в клетках от (cx, cy), y направлен вверх.
        На выходе — отрезки в координатах сцены."""
        cell = 4.0
        nx = int((2 * hw) / cell)
        ny = int((2 * hh) / cell)
        vals = [[None] * (nx + 1) for _ in range(ny + 1)]
        for j in range(ny + 1):
            ys = cy - hh + j * cell
            yc = (cy - ys) / GRID_STEP
            row = vals[j]
            for i in range(nx + 1):
                xs = cx - hw + i * cell
                xc = (xs - cx) / GRID_STEP
                try:
                    v = float(eval(code, {"__builtins__": {}},
                                   dict(ns, x=xc, y=yc)))
                except Exception:
                    continue
                if math.isfinite(v):
                    row[i] = v

        def cross(va, vb, ax_, ay_, bx_, by_):
            t = va / (va - vb)
            return (ax_ + t * (bx_ - ax_), ay_ + t * (by_ - ay_))

        segs = []
        for j in range(ny):
            for i in range(nx):
                v00 = vals[j][i]
                v10 = vals[j][i + 1]
                v01 = vals[j + 1][i]
                v11 = vals[j + 1][i + 1]
                if None in (v00, v10, v01, v11):
                    continue
                x0 = cx - hw + i * cell
                y0 = cy - hh + j * cell
                x1c, y1c = x0 + cell, y0 + cell
                pts = {}
                if (v00 > 0) != (v10 > 0):
                    pts["B"] = cross(v00, v10, x0, y0, x1c, y0)
                if (v10 > 0) != (v11 > 0):
                    pts["R"] = cross(v10, v11, x1c, y0, x1c, y1c)
                if (v01 > 0) != (v11 > 0):
                    pts["T"] = cross(v01, v11, x0, y1c, x1c, y1c)
                if (v00 > 0) != (v01 > 0):
                    pts["L"] = cross(v00, v01, x0, y0, x0, y1c)
                if len(pts) == 2:
                    a, b = pts.values()
                    segs.append([a, b])
                elif len(pts) == 4:
                    # седловая клетка: соединяем по знаку в центре клетки
                    xc0 = (x0 + cell / 2 - cx) / GRID_STEP
                    yc0 = (cy - (y0 + cell / 2)) / GRID_STEP
                    try:
                        vc = float(eval(code, {"__builtins__": {}},
                                        dict(ns, x=xc0, y=yc0)))
                    except Exception:
                        vc = 0.0
                    if vc > 0:
                        segs.append([pts["L"], pts["T"]])
                        segs.append([pts["B"], pts["R"]])
                    else:
                        segs.append([pts["L"], pts["B"]])
                        segs.append([pts["T"], pts["R"]])
        return segs

    # ---------- действия ----------

    def _clear_redo(self):
        """Смерть redo-ветки: записи больше нигде не удерживаются, GC может
        их собрать, а их id() — переиспользовать новыми записями. Кэш рамок
        по id() тогда врёт — сбрасываем его вместе с веткой. Если ветки
        нет, кэш не трогаем: id() живых записей переиспользованы быть не
        могут, а лишний сброс заставлял бы пересчитывать рамки."""
        if self.redo_stack:
            self.redo_stack.clear()
            self._bbox_cache.clear()

    def _push_undo(self, action):
        """Новое действие: кладём в undo и обнуляем ветку redo."""
        self.undo_stack.append(action)
        self._clear_redo()

    def undo(self):
        if not self.undo_stack:
            return
        action, payload = self.undo_stack.pop()
        if action == "add":
            if payload in self.entries:
                self.entries.remove(payload)
                if payload is self.selected_image:
                    self.selected_image = None
        elif action == "add_many":
            for e in payload:
                if e in self.entries:
                    self.entries.remove(e)
                    if e is self.selected_image:
                        self.selected_image = None
        elif action == "erase":
            # payload — список шагов:
            # removed=[(idx, исходная запись, части-замены)], added=[части].
            # Откатываем в обратном порядке: убрать добавленное, вернуть
            # удалённое на прежние позиции.
            for removed, added in reversed(payload):
                for e in added:
                    if e in self.entries:
                        self.entries.remove(e)
                        if e is self.selected_image:
                            self.selected_image = None
                for idx, e, _parts in sorted(removed, key=lambda item: item[0]):
                    self.entries.insert(min(idx, len(self.entries)), e)
        elif action == "move":
            # вернуть перенесённые записи на место: сдвиг с обратным знаком
            entries, delta = payload
            inv = QPointF(-delta.x(), -delta.y())
            for e in entries:
                if e in self.entries:
                    self._translate_entry(e, inv)
        elif action == "resize_image":
            # вернуть pixmap до растягивания
            e, old_pix, _new_pix = payload
            e["pix"] = old_pix
            e.pop("_pv", None)
            e.pop("_pv_cache", None)
            e.pop("_pv_csize", None)
            self._bbox_cache.pop(id(e), None)
        elif action == "rotate_image":
            # вернуть угол до поворота
            e, old_a, _new_a = payload
            e["angle"] = old_a
            self._bbox_cache.pop(id(e), None)
        self.selected_entries = [
            e for e in self.selected_entries if e in self.entries]
        self._rebuild_layers()
        self.update()
        self.redo_stack.append((action, payload))

    def redo(self):
        if not self.redo_stack:
            return
        action, payload = self.redo_stack.pop()
        if action == "add":
            if payload not in self.entries:
                self.entries.append(payload)
        elif action == "add_many":
            for e in payload:
                if e not in self.entries:
                    self.entries.append(e)
        elif action == "erase":
            # Повторяем стирание в прямом порядке. Для каждого шага
            # восстанавливаем соответствие «исходная запись -> её части» и
            # собираем список заново, чтобы порядок записей сохранился.
            for removed, _added in payload:
                parts_by_entry = {id(e): parts for _idx, e, parts in removed}
                sentinel = object()
                out = []
                for e in self.entries:
                    parts = parts_by_entry.get(id(e), sentinel)
                    if parts is sentinel:
                        out.append(e)
                    else:
                        out.extend(parts)
                self.entries = out
        elif action == "move":
            # повторить перенос: тот же сдвиг ещё раз
            entries, delta = payload
            for e in entries:
                if e in self.entries:
                    self._translate_entry(e, delta)
        elif action == "resize_image":
            e, _old_pix, new_pix = payload
            e["pix"] = new_pix
            e.pop("_pv", None)
            e.pop("_pv_cache", None)
            e.pop("_pv_csize", None)
            self._bbox_cache.pop(id(e), None)
        elif action == "rotate_image":
            e, _old_a, new_a = payload
            e["angle"] = new_a
            self._bbox_cache.pop(id(e), None)
        self.selected_entries = [
            e for e in self.selected_entries if e in self.entries]
        if self.selected_image is not None and self.selected_image not in self.entries:
            self.selected_image = None
        self._rebuild_layers()
        self.update()
        self.undo_stack.append((action, payload))

    def clear(self):
        if not self.entries:
            return
        if QMessageBox.question(
                self, "Очистить доску", "Удалить всё нарисованное?") != QMessageBox.StandardButton.Yes:
            return
        # очистка — обычное действие: отменяется по Ctrl+Z
        removed = [(idx, e, []) for idx, e in enumerate(self.entries)]
        self._push_undo(("erase", [(removed, [])]))
        self.entries.clear()
        self.layers.clear()
        self.selected_image = None
        self.selected_entries = []
        self.mode = None
        self.update()
        self.window().statusBar().showMessage(
            "Доска очищена · Ctrl+Z — вернуть", 4000)

    def save_png(self, path=None):
        if not self.entries:
            self.window().statusBar().showMessage("Доска пуста", 3000)
            return
        if not path:
            path, _ = QFileDialog.getSaveFileName(
                self, "Сохранить доску", "доска.png", "PNG (*.png)")
        if not path:
            return
        # область экспорта = занятая часть доски + поля (с учётом поворота картинок)
        margin = 100
        x0, y0, x1, y1 = SCENE_W, SCENE_H, 0, 0
        for e in self.entries:
            bx0, by0, bx1, by1 = self._entry_bbox(e)
            x0, y0 = min(x0, bx0), min(y0, by0)
            x1, y1 = max(x1, bx1), max(y1, by1)
        x0 = int(max(0, x0 - margin))
        y0 = int(max(0, y0 - margin))
        x1 = int(min(SCENE_W, x1 + margin))
        y1 = int(min(SCENE_H, y1 + margin))
        w, h = x1 - x0, y1 - y0
        if w <= 0 or h <= 0:
            return
        # страж от чудовищного растра: записи далеко друг от друга дали
        # бы огромный QImage (память/подвисание)
        if w * h > 64_000_000:
            QMessageBox.warning(
                self, "Сохранить PNG",
                "Область сохранения слишком велика "
                f"({w}×{h} px). Сблизьте записи или удалите далёкие.")
            return

        img = QImage(w, h, QImage.Format.Format_RGB32)
        img.fill(BG_COLOR)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.translate(-x0, -y0)
        if self.show_grid:
            p.setPen(QPen(GRID_COLOR, 1))
            gx = (x0 // GRID_STEP) * GRID_STEP
            while gx <= x1:
                p.drawLine(gx, y0, gx, y1)
                gx += GRID_STEP
            gy = (y0 // GRID_STEP) * GRID_STEP
            while gy <= y1:
                p.drawLine(x0, gy, x1, gy)
                gy += GRID_STEP
        for e in self.entries:
            if e["kind"] == "stroke":
                self._render_stroke(p, e)
            elif e["kind"] == "line":
                self._render_line(p, e)
            elif e["kind"] == "axes":
                self._render_axes(p, e)
            elif e["kind"] == "curve":
                self._render_curve(p, e)
            elif e["kind"] == "text":
                self._render_text(p, e)
            elif e["kind"] == "image":
                self._draw_image(p, e)
        p.end()
        if img.save(path, "PNG"):
            self.window().statusBar().showMessage(f"Сохранено: {path}", 5000)
        else:
            QMessageBox.critical(self, "Ошибка", "Не удалось сохранить файл")

    # ---------- инструменты ----------

    def _make_pen_cursor(self):
        """Цветное перекрестие с тёмной окантовкой — видно на любом фоне.
        Размер курсора немного растёт вместе с выбранной толщиной кисти."""
        # база −15% от прежнего размера; далее плавно по толщине
        scale = 0.85 * (0.55 + 0.45 * (self.pen_width / DEFAULT_WIDTH))
        scale = min(1.25, max(0.5, scale))
        arm = min(10.0 * scale, 13.0)      # полудлина луча перекрестия
        w_out = max(1.6, 5.0 * scale)      # тёмная окантовка
        w_in = max(1.0, 3.0 * scale)       # цветная линия
        dot = max(2.0, 4.0 * scale)        # центральная точка
        pm = QPixmap(32, 32)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        center = 16
        p.setPen(QPen(QColor(0, 0, 0), w_out, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(center - arm, center),
                   QPointF(center + arm, center))
        p.drawLine(QPointF(center, center - arm),
                   QPointF(center, center + arm))
        p.setPen(QPen(QColor(self.color), w_in, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(center - arm, center),
                   QPointF(center + arm, center))
        p.drawLine(QPointF(center, center - arm),
                   QPointF(center, center + arm))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self.color))
        p.drawEllipse(QRectF(center - dot / 2, center - dot / 2, dot, dot))
        p.end()
        return QCursor(pm, center, center)

    def _apply_pen_cursor(self):
        if not self.eraser_tool:
            self.setCursor(self._make_pen_cursor())

    def set_color(self, color: QColor):
        self.color = QColor(color)
        self.set_eraser_tool(False)

    def set_width(self, w):
        self.pen_width = max(0.5, min(40.0, float(w)))
        updater = getattr(self.window(), "_update_width_buttons", None)
        if updater is not None:
            updater()
        self._apply_pen_cursor()
        self.window().statusBar().showMessage(
            f"Толщина линии: {self.pen_width:g}", 1500)

    def set_eraser_tool(self, on: bool):
        self.eraser_tool = on
        # кнопка на панели всегда отражает режим, даже если режим
        # переключили программно (например, выбором цвета); проверка
        # состояния обрывает рекурсию toggled -> set_eraser_tool
        btn = getattr(self.window(), "eraser_btn", None)
        if btn is not None and btn.isChecked() != on:
            btn.setChecked(on)
        if on:
            pm = QPixmap(32, 32)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setPen(QPen(QColor("black"), 2))
            p.drawEllipse(3, 3, 26, 26)
            p.end()
            self.setCursor(QCursor(pm, 16, 16))
            self.window().statusBar().showMessage(
                "Режим ластика (E — выключить)", 3000)
        else:
            self._apply_pen_cursor()

    def wheelEvent(self, event):
        # колёсико без Ctrl ничего не делает; Ctrl+колёсико — масштаб
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            scene_pt = self._to_scene(event.position())
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.zoom = max(ZOOM_MIN, min(ZOOM_MAX, self.zoom * factor))
            self.offset = scene_pt - event.position() / self.zoom
            self._clamp_offset()
            self.window().statusBar().showMessage(
                f"Масштаб: {int(self.zoom * 100)}%", 1500)
            self.update()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Доска {VERSION}")
        self.resize(1280, 800)

        self.canvas = Canvas(self)
        self.setCentralWidget(self.canvas)
        if self.canvas.wintab is not None and self.canvas.wintab.ok:
            self.statusBar().showMessage(
                "Wacom подключён (WinTab) · задний конец пера — ластик · "
                "Shift — линия · Q — стрелка · ЛКМ — выделение · "
                "ПКМ пусто — двигать · Ctrl+колёсико — масштаб")
        elif sys.platform == "win32":
            self.statusBar().showMessage(
                "WinTab не найден · Shift — линия · Q — стрелка · "
                "ЛКМ — выделение · ПКМ пусто — двигать · ПКМ/Alt/E — ластик · "
                "Ctrl+колёсико — масштаб")
        else:
            self.statusBar().showMessage(
                "Планшет через Qt · задний конец пера — ластик · "
                "Shift — линия · Q — стрелка · ЛКМ — выделение · "
                "ПКМ пусто — двигать · Ctrl+колёсико — масштаб")

        tb = QToolBar("Инструменты")
        tb.setMovable(False)
        self.addToolBar(tb)

        for key, col in QUICK_COLORS:
            btn = QPushButton(key)
            btn.setFixedWidth(28)
            btn.setStyleSheet(
                f"background-color: {col}; color: white; font-weight: bold;")
            btn.clicked.connect(
                lambda _=False, c=col: self.canvas.set_color(QColor(c)))
            tb.addWidget(btn)

        pick = QPushButton("Цвет…")
        pick.clicked.connect(self._pick_color)
        tb.addWidget(pick)

        tb.addSeparator()
        tb.addWidget(QLabel(" Толщина: "))
        self.width_buttons = {}
        for w in (2, 3, 5, 8, 12, 16):
            btn = QPushButton(f"{w:g}")
            btn.setFixedWidth(32)
            btn.clicked.connect(lambda _=False, v=w: self._set_width(v))
            self.width_buttons[float(w)] = btn
            tb.addWidget(btn)
        self._update_width_buttons()

        tb.addSeparator()
        # группа: ластик и очистка
        self.eraser_btn = QPushButton("Ластик (E)")
        self.eraser_btn.setCheckable(True)
        self.eraser_btn.toggled.connect(self.canvas.set_eraser_tool)
        tb.addWidget(self.eraser_btn)
        clear_btn = QPushButton("Очистить")
        clear_btn.setToolTip("Ctrl+N")
        clear_btn.clicked.connect(self.canvas.clear)
        tb.addWidget(clear_btn)

        tb.addSeparator()
        # группа: режим ЛКМ, пунктир, сетка
        self.mousemode_btn = QPushButton("ЛКМ: выделение (M)")
        self.mousemode_btn.setCheckable(True)
        self.mousemode_btn.setToolTip(
            "Режим левой кнопки мыши (M): выделение/перемещение записей "
            "или рисование")
        self.mousemode_btn.toggled.connect(self._toggle_mouse_mode)
        tb.addWidget(self.mousemode_btn)

        self.dash_btn = QPushButton("Пунктир")
        self.dash_btn.setCheckable(True)
        self.dash_btn.setToolTip(
            "Прямые линии (Shift), окружности и графики рисуются пунктиром")
        self.dash_btn.toggled.connect(self._toggle_dash)
        tb.addWidget(self.dash_btn)

        self.grid_btn = QPushButton("Сетка")
        self.grid_btn.setCheckable(True)
        self.grid_btn.setChecked(True)
        self.grid_btn.setToolTip("Показывать/скрывать фоновую сетку")
        self.grid_btn.toggled.connect(self._toggle_grid)
        tb.addWidget(self.grid_btn)

        tb.addSeparator()
        # группа: окружность, оси, график, текст
        self.circle_btn = QPushButton("Окружность")
        self.circle_btn.setCheckable(True)
        self.circle_btn.setToolTip(
            "Окружность (C): нажать в центре и тянуть до точки на ней; "
            "Ctrl — центр и радиус прилипают к сетке")
        self.circle_btn.toggled.connect(self._toggle_circle)
        tb.addWidget(self.circle_btn)

        axes_btn = QPushButton("Оси")
        axes_btn.setToolTip(
            "Координатные оси в центре вида (начало — в узле сетки)")
        axes_btn.clicked.connect(self.canvas.add_axes)
        tb.addWidget(axes_btn)
        graph_btn = QPushButton("График")
        graph_btn.setToolTip(
            "График f(x) по формуле: x — в клетках от начала координат")
        graph_btn.clicked.connect(lambda: self.canvas.add_graph())
        tb.addWidget(graph_btn)
        self.graph_btn = graph_btn

        text_btn = QPushButton("Текст")
        text_btn.setToolTip(
            "Надпись (T): текст, размер шрифта, обычный текст или LaTeX")
        text_btn.clicked.connect(lambda: self.canvas.add_text())
        tb.addWidget(text_btn)

        tb.addSeparator()
        # группа: справка
        self.info_btn = QPushButton("Информация")
        self.info_btn.setToolTip(
            "Описание всех возможностей и горячих клавиш (F1)")
        self.info_btn.clicked.connect(self._show_help)
        tb.addWidget(self.info_btn)

        for key, slot in (
            ("Ctrl+Z", self.canvas.undo),
            ("Ctrl+Y", self.canvas.redo),
            ("Ctrl+C", self.canvas.copy_selected),
            ("Ctrl+V", self.canvas.paste),
            ("Ctrl+S", self.canvas.save_png),
            ("Ctrl+N", self.canvas.clear),
            ("Ctrl+A", self.canvas.select_all),
            ("Ctrl+0", self.canvas.reset_view),
        ):
            sc = QShortcut(QKeySequence(key), self)
            sc.activated.connect(slot)

        sc = QShortcut(QKeySequence("E"), self)
        sc.activated.connect(
            lambda: self.eraser_btn.setChecked(not self.eraser_btn.isChecked()))

        sc = QShortcut(QKeySequence("C"), self)
        sc.activated.connect(
            lambda: self.circle_btn.setChecked(not self.circle_btn.isChecked()))

        sc = QShortcut(QKeySequence("T"), self)
        sc.activated.connect(lambda: self.canvas.add_text())

        sc = QShortcut(QKeySequence(Qt.Key.Key_Delete), self)
        sc.activated.connect(self.canvas.delete_selected)

        sc = QShortcut(QKeySequence(Qt.Key.Key_F1), self)
        sc.activated.connect(self._show_help)

        for key, col in QUICK_COLORS:
            sc = QShortcut(QKeySequence(key), self)
            sc.activated.connect(
                lambda c=col: self.canvas.set_color(QColor(c)))

        self.showMaximized()
        QTimer.singleShot(0, self.canvas.center_view)

    def _set_width(self, w):
        self.canvas.set_width(w)

    def _update_width_buttons(self):
        current = float(self.canvas.pen_width)
        for w, btn in self.width_buttons.items():
            if abs(w - current) < 1e-6:
                btn.setStyleSheet(
                    "QPushButton { border: 2px solid #2b5fd9; "
                    "background-color: #eaf1ff; font-weight: bold; }")
            else:
                btn.setStyleSheet("")

    def _pick_color(self):
        col = QColorDialog.getColor(self.canvas.color, self, "Выберите цвет")
        if col.isValid():
            self.canvas.set_color(col)

    def _help_html(self):
        return """
<h3>Доска """ + VERSION + """ — возможности и горячие клавиши</h3>

<p><b>Рисование</b><br>
• Перо (наконечник) или левая кнопка мыши в режиме «ЛКМ: рисует» —
рисование; толщина линии зависит от нажатия пера.<br>
• <b>Shift</b> + перо/ЛКМ — прямая линия. <b>Shift+Ctrl</b> — линия
прилипает концами к узлам сетки; «Пунктир» — линия рисуется пунктиром.<br>
• <b>Q</b> (удерживать) + перо/ЛКМ — отрезок со стрелкой на конце;
<b>Q+Ctrl</b> — концы прилипают к узлам сетки.<br>
• «Окружность» (<b>C</b>) — окружность: нажать в центре и тянуть до точки
на ней; <b>Ctrl</b> — центр и радиус прилипают к сетке (центр, прилипнув,
таким и остаётся, даже если Ctrl отпустить). Центр отмечается точкой,
её можно стереть ластиком. «Пунктир» действует и на окружность.<br>
• Цвет — цветные кнопки (клавиши 1–4) или «Цвет…»; толщина — кнопки 2–16.
Курсор показывает выбранный цвет, а его размер — выбранную толщину.<br>
• «Текст» (<b>T</b>) — надпись в центре видимой области: в диалоге вводится
текст, выбирается размер шрифта и режим — обычный текст или LaTeX
(подмножество: ^ _ \\frac \\sqrt \\alpha…\\omega \\times \\leq \\infty
и др.).<br>
• «Сетка» — показать/скрыть фоновую сетку (влияет и на сохраняемый PNG).</p>

<p><b>Ластик</b> (задний конец пера, ПКМ по записи, Alt+ЛКМ, кнопка «Ластик»
или клавиша <b>E</b>)<br>
• Стирает круглым пятном, а не всю линию: штрихи и линии разрезаются.<br>
• Радиус растёт со скоростью движения: медленно — точно, быстро — широко.<br>
• Один проход ластика отменяется одним <b>Ctrl+Z</b>.</p>

<p><b>Выделение</b> (режим «ЛКМ: выделение», переключение клавишей <b>M</b>)<br>
• Потянуть ЛКМ — рамка выделения записей; перетащить выделенное —
переместить; <b>Del</b> — удалить.<br>
• <b>Ctrl+C</b> — скопировать выделенное; <b>Ctrl+V</b> — вставить копию
в видимую область (копия не накладывается точно на оригинал).<br>
• Клик по пустому месту — снять выделение.</p>

<p><b>Изображения</b><br>
• <b>Ctrl+V</b> — вставить изображение из буфера обмена; поверх картинок
можно писать. Если скопированы выделенные записи (<b>Ctrl+C</b>), вставляются
они, но как только в буфере появляется изображение — оно замещает
скопированное и вставляется оно.<br>
• Клик по картинке — выбрать: нижний правый уголок — размер (пропорционально),
верхняя ручка — поворот с прилипанием к 90°, <b>Del</b> — удалить,
перетаскивание — переместить.</p>

<p><b>Вид и рабочая область</b> (область гораздо больше экрана)<br>
• ПКМ по пустому месту или средняя кнопка — перемещение области.<br>
• <b>Ctrl+колёсико</b> — масштаб.<br>
• <b>Ctrl+0</b> — вернуться в центр доски.</p>

<p><b>Оси и графики</b><br>
• «Оси» — координатные оси через центр вида: начало в узле сетки,
деления совпадают с клетками.<br>
• «График» — график функции или уравнение кривой: <i>sin(x)</i>,
<i>x^2/10</i>, <i>y = x^2 - 4x + 3</i>, <i>x^2 + y^2 = 25</i>, <i>xy = 4</i>.
Степень — ^ или **, умножение можно опускать (4x, x(x+1)).
x и y — в клетках от начала координат последних осей.
«Пунктир» действует и на графики.</p>

<p><b>Прочее</b><br>
<b>M</b> — переключить ЛКМ между выделением и рисованием ·
<b>Ctrl+Z</b> — отмена · <b>Ctrl+Y</b> — повтор отменённого ·
<b>Ctrl+C</b>/<b>Ctrl+V</b> — копировать/вставить выделенное ·
<b>Ctrl+S</b> — сохранить доску в PNG · <b>Ctrl+N</b> — очистить доску ·
<b>F1</b> — эта справка.</p>
"""

    def _show_help(self):
        msg = QMessageBox(self)
        msg.setWindowTitle("Доска — справка")
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setText(self._help_html())
        msg.setIcon(QMessageBox.Icon.Information)
        msg.exec()

    def _toggle_mouse_mode(self, on):
        self.canvas.mouse_draw = on
        self.mousemode_btn.setText(
            "ЛКМ: рисует (M)" if on else "ЛКМ: выделение (M)")
        self.statusBar().showMessage(
            "Левая кнопка мыши: рисование" if on
            else "Левая кнопка мыши: выделение и перемещение", 3000)

    def _toggle_dash(self, on):
        self.canvas.dashed_lines = on
        self.statusBar().showMessage(
            "Линии, окружности и графики рисуются пунктиром" if on
            else "Линии сплошные", 3000)

    def _toggle_circle(self, on):
        self.canvas.circle_tool = on
        self.statusBar().showMessage(
            "Окружность: нажать в центре, тянуть до точки на ней" if on
            else "Инструмент «Круг» выключен", 4000)

    def _toggle_grid(self, on):
        self.canvas.show_grid = on
        self.canvas.update()
        self.statusBar().showMessage(
            "Сетка показана" if on else "Сетка скрыта", 2000)

    def closeEvent(self, event):
        self.canvas.close_wintab()
        super().closeEvent(event)


def _resource_path(name):
    """Путь к ресурсу: рядом со скриптом или внутри onefile-сборки."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def main():
    # свой AppUserModelID — чтобы в панели задач была иконка приложения,
    # а не стандартная иконка Python
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "Kimi.Doska.Whiteboard")
        except Exception:
            pass
    app = QApplication(sys.argv)
    icon = QIcon(_resource_path("doska_icon.ico"))
    if not icon.isNull():
        app.setWindowIcon(icon)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
