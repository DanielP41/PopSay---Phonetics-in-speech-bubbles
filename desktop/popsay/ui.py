"""Interfaz Qt: burbuja de pronunciación, iconito de selección y menú de la bandeja."""

from __future__ import annotations

import html
import logging
import math
import sys
from typing import Callable

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QCursor, QFont, QGuiApplication, QIcon, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLayout, QMenu, QPushButton, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget,
)

from . import syllables, textutil
from .settings import SPEEDS

log = logging.getLogger(__name__)

UI_FONT = "Segoe UI" if sys.platform == "win32" else "DejaVu Sans"

THEMES = {
    "dark": dict(bg="#2a2a2a", panel="#222222", line="#3a3a3a", line_strong="#4a4a4a", text="#ececec", muted="#a8a8a8",
                 accent="#0b6fd8", accent_ink="#ffffff", accent_soft="#0d3f8f", accent_line="#2f6fd6", bar="#555555",
                 bar_lit="#5aa0ff", hover="#333333"),
    "light": dict(bg="#ffffff", panel="#f5f6f8", line="#e1e3e8", line_strong="#c9ccd3", text="#1b1d21", muted="#5d6270",
                  accent="#0b63c4", accent_ink="#ffffff", accent_soft="#e5efff", accent_line="#0b63c4", bar="#c9ccd3",
                  bar_lit="#0b63c4", hover="#eef0f3"),
}

# Íconos de trazo (24x24), dibujados con QPainterPath para no depender de archivos.
ICONS = {
    "speaker": "M11 5 L6 9 H2 V15 H6 L11 19 Z M15.5 8.5 A5 5 0 0 1 15.5 15.5 M19 5 A10 10 0 0 1 19 19",
    "close": "M18 6 L6 18 M6 6 L18 18",
    "repeat": "M17 2 L21 6 L17 10 M3 11 V10 A4 4 0 0 1 7 6 H21 M7 22 L3 18 L7 14 M21 13 V14 A4 4 0 0 1 17 18 H3",
    "copy": "M9 9 H21 V21 H9 Z M5 15 H4 A2 2 0 0 1 2 13 V4 A2 2 0 0 1 4 2 H13 A2 2 0 0 1 15 4 V5",
    "gear": "M4 7 H8 M12 7 H20 M10 5 A2 2 0 1 0 10.001 5 M4 17 H14 M18 17 H20 M16 15 A2 2 0 1 0 16.001 15",
}


def _svg_path(d: str) -> QPainterPath:
    """Mini intérprete de paths SVG (M L H V Z A con arcos circulares)."""
    import re

    tokens = re.findall(r"[MLHVZA]|-?\d*\.?\d+", d)
    path, i, cur, start, cmd = QPainterPath(), 0, QPoint(), None, None
    x = y = 0.0

    def num():
        nonlocal i
        v = float(tokens[i])
        i += 1
        return v

    while i < len(tokens):
        if tokens[i] in "MLHVZA":
            cmd = tokens[i]
            i += 1
        if cmd == "M":
            x, y = num(), num()
            path.moveTo(x, y)
            start = (x, y)
            cmd = "L"
        elif cmd == "L":
            x, y = num(), num()
            path.lineTo(x, y)
        elif cmd == "H":
            x = num()
            path.lineTo(x, y)
        elif cmd == "V":
            y = num()
            path.lineTo(x, y)
        elif cmd == "Z":
            if start:
                path.lineTo(*start)
                x, y = start
        elif cmd == "A":
            r, _ry, _rot, large, sweep, x2, y2 = (num() for _ in range(7))
            # arco circular aproximado con cuadráticas
            mx, my = (x + x2) / 2, (y + y2) / 2
            dx, dy = x2 - x, y2 - y
            dist = math.hypot(dx, dy) or 1
            h = math.sqrt(max(r * r - (dist / 2) ** 2, 0))
            sgn = 1 if sweep == large else -1
            cx, cy = mx - sgn * h * dy / dist, my + sgn * h * dx / dist
            a1 = math.atan2(y - cy, x - cx)
            a2 = math.atan2(y2 - cy, x2 - cx)
            span = a2 - a1
            if sweep and span < 0:
                span += 2 * math.pi
            if not sweep and span > 0:
                span -= 2 * math.pi
            steps = max(4, int(abs(span) / 0.3))
            for s in range(1, steps + 1):
                a = a1 + span * s / steps
                path.lineTo(cx + r * math.cos(a), cy + r * math.sin(a))
            x, y = x2, y2
    return path


class IconButton(QPushButton):
    def __init__(self, icon: str, tooltip: str, size: int = 32, parent=None):
        super().__init__(parent)
        self._icon = icon
        self._color = QColor("#a8a8a8")
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setObjectName("iconbtn")

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = min(self.width(), self.height()) * 0.55
        p.translate((self.width() - s) / 2, (self.height() - s) / 2)
        p.scale(s / 24, s / 24)
        p.setPen(QPen(self._color, 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(_svg_path(ICONS[self._icon]))


class PlayButton(QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = "idle"
        self.angle = 0
        self.setFixedSize(46, 46)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.colors = ("#0b6fd8", "#ffffff")
        self._spin = QTimer(self, interval=30, timeout=self._tick)

    def _tick(self):
        self.angle = (self.angle + 12) % 360
        self.update()

    def set_state(self, state: str) -> None:
        self.state = state
        self.setAccessibleName("Detener" if state != "idle" else "Reproducir pronunciación")
        self._spin.start() if state == "loading" else self._spin.stop()
        self.update()

    def paintEvent(self, _e):
        bg, ink = (QColor(c) for c in self.colors)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.underMouse():
            bg = bg.lighter(112)
        p.setBrush(bg)
        p.setPen(Qt.NoPen)
        p.drawEllipse(self.rect().adjusted(1, 1, -1, -1))
        c = self.rect().center()
        p.setBrush(ink)
        if self.state == "idle":
            path = QPainterPath()
            path.moveTo(c.x() - 5, c.y() - 8)
            path.lineTo(c.x() + 9, c.y())
            path.lineTo(c.x() - 5, c.y() + 8)
            path.closeSubpath()
            p.drawPath(path)
        elif self.state == "playing":
            p.drawRoundedRect(QRectF(c.x() - 7, c.y() - 7, 14, 14), 2, 2)
        else:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(ink, 2.6, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(QRectF(c.x() - 9, c.y() - 9, 18, 18), -self.angle * 16, 270 * 16)


class Wave(QWidget):
    BARS = 30

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(32)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.progress = 0.0
        self.animated = False
        self.phase = 0.0
        self.colors = ("#555555", "#5aa0ff")
        self._timer = QTimer(self, interval=60, timeout=self._tick)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def set_animated(self, on: bool) -> None:
        self.animated = on
        self._timer.start() if on else self._timer.stop()
        self.update()

    def _tick(self):
        self.phase += 0.35
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        n, gap = self.BARS, 3
        w = (self.width() - gap * (n - 1)) / n
        lit = round(self.progress * n)
        for i in range(n):
            base = 0.22 + 0.7 * abs(math.sin(i * 1.7) * math.cos(i * 0.45))
            if self.animated:
                base *= 0.6 + 0.4 * abs(math.sin(self.phase + i * 0.5))
            h = max(4, base * self.height())
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(self.colors[1] if i < lit else self.colors[0]))
            p.drawRoundedRect(QRectF(i * (w + gap), (self.height() - h) / 2, w, h), 1.5, 1.5)


class Pill(QPushButton):
    def __init__(self, text: str, tooltip: str = "", parent=None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setObjectName("pill")
        if tooltip:
            self.setToolTip(tooltip)


class Card(QWidget):
    """Contenedor redondeado con sombra (la ventana en sí es transparente)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.colors = THEMES["dark"]
        self.setAttribute(Qt.WA_StyledBackground, False)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(self.colors["line"]), 1))
        p.setBrush(QColor(self.colors["bg"]))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)


class SizeGrip(QWidget):
    """Agarre de puntitos en la esquina inferior derecha: arrastrar cambia el tamaño; doble clic lo restablece."""

    resized = Signal(int, int)   # dx, dy desde que empezó el arrastre
    started = Signal()
    finished = Signal()
    reset = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(18, 18)
        self.setCursor(Qt.SizeFDiagCursor)
        self.setToolTip("Arrastrá para cambiar el ancho y el alto máximo del texto · doble clic: automático")
        self.color = QColor("#8a8a8a")
        self._origin: QPoint | None = None

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(self.color)
        # triángulo de 6 puntos, como el de las ventanas de Windows
        for row in range(3):
            for col in range(row + 1):
                x = self.width() - 4 - (row - col) * 4.5 - 1.2
                y = self.height() - 4 - col * 4.5 - 1.2
                p.drawEllipse(QRectF(x - 1.2, y - 1.2, 2.4, 2.4))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._origin = e.globalPosition().toPoint()
            self.started.emit()

    def mouseMoveEvent(self, e):
        if self._origin is not None:
            d = e.globalPosition().toPoint() - self._origin
            self.resized.emit(d.x(), d.y())

    def mouseReleaseEvent(self, _e):
        if self._origin is not None:
            self._origin = None
            self.finished.emit()

    def mouseDoubleClickEvent(self, _e):
        self.reset.emit()


def _floating_flags() -> Qt.WindowType:
    return (Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus
            | Qt.NoDropShadowWindowHint)


class Bubble(QWidget):
    """La burbuja de pronunciación. No toma el foco: la app de origen sigue activa."""

    play_requested = Signal()
    stop_requested = Signal()
    accent_changed = Signal(str)
    speed_changed = Signal(float)
    settings_requested = Signal(QPoint)
    closed = Signal()

    WIDTH = 392
    MARGIN = 14  # espacio para la sombra
    MIN_W, MAX_W = 320, 900
    MIN_TEXT_H, MAX_TEXT_H, AUTO_TEXT_H = 48, 640, 150

    def __init__(self, settings):
        super().__init__(None, _floating_flags())
        self.settings = settings
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle("PopSay")
        self.text = ""
        self.words: list[textutil.Word] = []
        self.active_word = -1
        self.active_frac = 0.0
        self._syl = (-1, -1)          # (sílaba IPA, sílaba escrita) que se están resaltando
        self.state = "idle"
        self.repeat = False
        self._build()
        self.apply_theme()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(self.MARGIN, self.MARGIN - 4, self.MARGIN, self.MARGIN + 4)
        # la ventana siempre toma el tamaño de su contenido (si no, al crecer el texto se pisa con lo de abajo)
        outer.setSizeConstraint(QLayout.SetFixedSize)
        self.card = Card(self)
        self.card.setFixedWidth(max(self.MIN_W, min(self.MAX_W, int(self.settings.card_width or 364))))
        shadow = QGraphicsDropShadowEffect(self.card, blurRadius=28, offset=QPoint(0, 6), color=QColor(0, 0, 0, 120))
        self.card.setGraphicsEffect(shadow)
        outer.addWidget(self.card)

        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(6)
        self.brand_icon = IconButton("speaker", "PopSay", 22)
        self.brand_icon.setEnabled(False)
        self.brand = QLabel("PopSay")
        self.brand.setToolTip("Arrastrá la barra superior para mover la burbuja")
        self.brand.setObjectName("brand")
        head.addWidget(self.brand_icon)
        head.addWidget(self.brand)
        head.addStretch(1)
        self.btn_us = Pill("US", "Inglés de EE. UU.")
        self.btn_gb = Pill("UK", "Inglés británico")
        for b, acc in ((self.btn_us, "en-US"), (self.btn_gb, "en-GB")):
            b.clicked.connect(lambda _=False, a=acc: self._pick_accent(a))
            head.addWidget(b)
        self.btn_settings = IconButton("gear", "Ajustes", 32)
        self.btn_settings.clicked.connect(lambda: self.settings_requested.emit(QCursor.pos()))
        self.btn_close = IconButton("close", "Cerrar", 32)
        self.btn_close.clicked.connect(self.close_bubble)
        head.addWidget(self.btn_settings)
        head.addWidget(self.btn_close)
        lay.addLayout(head)

        self.panel = QWidget(objectName="panel")
        self.panel.setAttribute(Qt.WA_StyledBackground, True)
        pl = QVBoxLayout(self.panel)
        pl.setContentsMargins(12, 10, 12, 12)
        pl.setSpacing(6)
        self.words_label = QLabel(objectName="words")
        self.words_label.setWordWrap(True)
        self.words_label.setTextFormat(Qt.RichText)
        self.words_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.words_scroll = QScrollArea(objectName="wordsScroll")
        self.words_scroll.setWidget(self.words_label)
        self.words_scroll.setWidgetResizable(True)
        self.words_scroll.setFrameShape(QFrame.NoFrame)
        self.words_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.words_scroll.viewport().setAutoFillBackground(False)
        self.words_label.setAutoFillBackground(False)
        pl.addWidget(self.words_scroll)

        ipa_row = QHBoxLayout()
        ipa_row.setSpacing(4)
        self.ipa_label = QLabel(objectName="ipa")
        self.ipa_label.setWordWrap(True)
        self.ipa_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        ipa_row.addWidget(self.ipa_label, 1)
        self.ipa_wrap = QWidget()
        self.ipa_wrap.setLayout(ipa_row)
        ipa_row.setContentsMargins(0, 0, 0, 0)
        pl.addWidget(self.ipa_wrap)

        player = QHBoxLayout()
        player.setSpacing(12)
        self.btn_play = PlayButton()
        self.btn_play.clicked.connect(self._toggle_play)
        self.wave = Wave()
        player.addWidget(self.btn_play)
        player.addWidget(self.wave, 1)
        pl.addLayout(player)
        lay.addWidget(self.panel)

        foot = QHBoxLayout()
        foot.setSpacing(4)
        self.speed_btns: list[tuple[float, Pill]] = []
        for value, label in SPEEDS:
            b = Pill(label)
            b.clicked.connect(lambda _=False, v=value: self._pick_speed(v))
            foot.addWidget(b)
            self.speed_btns.append((value, b))
        foot.addStretch(1)
        self.btn_repeat = Pill("⟳  Repetir", "Repetir en bucle")
        self.btn_repeat.clicked.connect(self._toggle_repeat)
        foot.addWidget(self.btn_repeat)
        lay.addLayout(foot)

        self.notice = QLabel(objectName="notice")
        self.notice.setWordWrap(True)
        self.notice.hide()
        lay.addWidget(self.notice)

        self._notice_timer = QTimer(self, singleShot=True, timeout=lambda: self.set_notice(""))

        # agarre para cambiar el tamaño (va encima de la tarjeta, en la esquina)
        self.grip = SizeGrip(self.card)
        self.grip.started.connect(self._grip_start)
        self.grip.resized.connect(self._grip_move)
        self.grip.finished.connect(self._grip_end)
        self.grip.reset.connect(self._grip_reset)
        self.card.installEventFilter(self)
        self._grip_from = (0, 0)

    def _in_header(self, pos: QPoint) -> bool:
        p = self.card.mapFrom(self, pos)
        return 0 <= p.y() < self.panel.geometry().top() and self.card.rect().contains(p)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self._in_header(e.position().toPoint()):
            self._drag_from = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.setCursor(Qt.ClosedHandCursor)
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if getattr(self, "_drag_from", None) is not None:
            self.move(e.globalPosition().toPoint() - self._drag_from)
            e.accept()
        else:
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if getattr(self, "_drag_from", None) is not None:
            self._drag_from = None
            self.unsetCursor()
            e.accept()
        else:
            super().mouseReleaseEvent(e)

    def eventFilter(self, obj, ev):
        if obj is self.card and ev.type() == ev.Type.Resize:
            self.grip.move(self.card.width() - self.grip.width() - 3, self.card.height() - self.grip.height() - 3)
            self.grip.raise_()
        return super().eventFilter(obj, ev)

    def _fit_text(self):
        """Alto del cuadro de texto: el que eligió el usuario, o automático (hasta 150 px) según el contenido."""
        vw = max(50, self.words_scroll.viewport().width() or (self.card.width() - 48))
        content = self.words_label.heightForWidth(vw)
        if content <= 0:
            content = self.words_label.sizeHint().height()
        # El alto elegido con el agarre es un MÁXIMO: la burbuja siempre se abre ajustada al texto
        # (una oración corta no hereda el tamaño de un párrafo largo); lo que no entra, se desplaza.
        cap = int(self.settings.text_height or 0) or self.AUTO_TEXT_H
        h = max(24, min(content, cap))
        self.words_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded if content > h else Qt.ScrollBarAlwaysOff)
        if self.words_scroll.height() != h or self.words_scroll.minimumHeight() != h:
            self.words_scroll.setFixedHeight(h)
            self._relayout()

    def _relayout(self):
        """Recalcula el diseño de adentro hacia afuera y ajusta la ventana (si no, el texto pisa lo de abajo)."""
        for lay in (self.panel.layout(), self.card.layout(), self.layout()):
            lay.invalidate()
            lay.activate()
        self.adjustSize()

    def _scroll_to_word(self):
        sb = self.words_scroll.verticalScrollBar()
        if sb.maximum() <= 0 or self.active_word < 0 or not self.words:
            return
        frac = self.active_word / max(1, len(self.words) - 1)
        target = int(frac * (sb.maximum() + self.words_scroll.viewport().height())) - self.words_scroll.viewport().height() // 3
        sb.setValue(max(0, min(sb.maximum(), target)))

    def _grip_start(self):
        self._grip_from = (self.card.width(), int(self.settings.text_height or 0) or self.AUTO_TEXT_H)

    def _grip_move(self, dx: int, dy: int):
        screen = (QGuiApplication.screenAt(self.pos()) or QGuiApplication.primaryScreen()).availableGeometry()
        max_w = min(self.MAX_W, screen.right() - self.card.mapToGlobal(QPoint(0, 0)).x() - 8)
        w = max(self.MIN_W, min(max_w, self._grip_from[0] + dx))
        h = max(self.MIN_TEXT_H, min(self.MAX_TEXT_H, self._grip_from[1] + dy))
        self.settings.card_width, self.settings.text_height = int(w), int(h)
        self.card.setFixedWidth(int(w))
        self._relayout()   # el ancho nuevo cambia cuántas líneas ocupa el texto
        self._fit_text()
        self._relayout()

    def _grip_end(self):
        self.settings.save()

    def _grip_reset(self):
        self.settings.card_width, self.settings.text_height = 364, 0
        self.settings.save()
        self.card.setFixedWidth(364)
        self._relayout()
        self._fit_text()
        self._relayout()

    def apply_theme(self, name: str | None = None):
        from . import winsys

        if name is None:
            t = self.settings.theme
            name = t if t in THEMES else ("dark" if winsys.system_prefers_dark() else "light")
        c = THEMES[name]
        self.colors = c
        self.card.colors = c
        self.btn_play.colors = (c["accent"], c["accent_ink"])
        self.wave.colors = (c["bar"], c["bar_lit"])
        for b in (self.btn_settings, self.btn_close):
            b.set_color(c["muted"])
        self.brand_icon.set_color(c["accent"])
        self.grip.color = QColor(c["muted"])
        self.grip.update()
        # Qt no vuelve a aplicar la hoja de estilo a hijos ocultos si sólo se reemplaza: la vaciamos antes.
        self.card.setStyleSheet("")
        self.card.setStyleSheet(f"""
            QWidget {{ font-family: "{UI_FONT}"; font-size: 13px; color: {c['text']}; }}
            QLabel#brand {{ color: {c['muted']}; font-weight: 600; }}
            QWidget#panel {{ background: {c['panel']}; border: 1px solid {c['line']}; border-radius: 10px; }}
            QLabel#words {{ font-size: 17px; font-weight: 500; background: transparent; }}
            QScrollArea#wordsScroll {{ background: transparent; border: 0; }}
            QScrollBar:vertical {{ width: 8px; background: transparent; margin: 0; }}
            QScrollBar::handle:vertical {{ background: {c['line_strong']}; border-radius: 4px; min-height: 24px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
            QLabel#ipa {{ font-size: 14px; background: transparent; }}
            QLabel#notice {{ color: {c['muted']}; font-size: 12px; }}
            QPushButton#pill {{ min-height: 30px; padding: 0 10px; border-radius: 8px; border: 1px solid {c['line_strong']};
                               background: transparent; font-weight: 500; }}
            QPushButton#pill:hover {{ border-color: {c['accent_line']}; }}
            QPushButton#pill:checked {{ background: {c['accent_soft']}; border-color: {c['accent_line']};
                                        color: {'#ffffff' if name == 'dark' else c['text']}; }}
            QPushButton#iconbtn {{ border: 0; border-radius: 8px; background: transparent; }}
            QPushButton#iconbtn:hover {{ background: {c['hover']}; }}
            QToolTip {{ color: {c['text']}; background: {c['bg']}; border: 1px solid {c['line']}; }}
        """)
        self._render_words()
        self.update()

    def show_text(self, text: str, anchor: QPoint, notice: str = ""):
        self.text = text
        self.words = textutil.split_words(text)
        self.active_word = -1
        self.repeat = False
        self.set_state("idle")
        self.wave.progress = 0
        self.ipa_words = []
        self.ipa_label.setText("Buscando transcripción…")
        self.ipa_label.setProperty("value", "")
        self.ipa_wrap.setVisible(self.settings.show_ipa)
        self.sync_controls()
        self._render_words()
        self.words_scroll.verticalScrollBar().setValue(0)
        self.set_notice(notice)
        self.adjustSize()
        self.move(self._place(anchor))
        self.show()
        self.raise_()
        # al abrir, el ancho real del texto recién se conoce ya visible: volver a medir para no cortar renglones
        self._fit_text()
        QTimer.singleShot(0, self._fit_text)

    def set_ipa(self, ipa, approximate: bool = False):
        """ipa: lista con la transcripción de cada palabra (None si no tiene), o un texto suelto."""
        if isinstance(ipa, str):
            ipa = [ipa] if ipa else []
        self.ipa_words = list(ipa or [])
        self.ipa_approx = approximate
        shown = [x for x in self.ipa_words if x]
        if shown:
            self.ipa_label.setProperty("value", "/" + " ".join(shown) + "/")
            self.ipa_label.setToolTip("Transcripción aproximada (reglas o espeak-ng)" if approximate else "Fuente: CMUdict")
            self._render_ipa()
        else:
            self.ipa_label.setText("Sin transcripción disponible")
            self.ipa_label.setProperty("value", "")
        self._fit_text()
        self._relayout()

    def _syllable_state(self) -> tuple[int, int, list[str], list[str]]:
        """Para la palabra activa: (sílaba IPA, sílaba escrita, sílabas IPA, sílabas escritas)."""
        i = self.active_word
        if self.state != "playing" or not (0 <= i < len(self.words)):
            return -1, -1, [], []
        ortho = syllables.ortho(self.words[i].text)
        words = getattr(self, "ipa_words", [])
        ipa_w = words[i] if len(words) == len(self.words) and words[i] else None
        if ipa_w:
            ipa_syl = syllables.ipa(ipa_w)
            if len(ipa_syl) == 1:  # una sola sílaba sonora: la palabra escrita se resalta entera
                return 0, 0, ipa_syl, [self.words[i].text]
            j = syllables.active(self.active_frac, ipa_syl)
            return j, syllables.map_index(j, len(ipa_syl), len(ortho)), ipa_syl, ortho
        k = min(len(ortho) - 1, int(self.active_frac * len(ortho)))
        return -1, k, [], ortho

    def _render_ipa(self):
        """Resalta la transcripción de la palabra que está sonando, a la par del texto."""
        words = getattr(self, "ipa_words", [])
        if not any(words):
            return
        c = self.colors
        playing = self.state == "playing" and len(words) == len(self.words)
        parts = []
        for i, w in enumerate(words):
            if not w:
                continue
            t = self._ipa_escape(w)
            if playing and i == self.active_word:
                j, _k, ipa_syl, _o = self._syllable_state()
                if len(ipa_syl) > 1 and j >= 0:
                    t = "".join(
                        (f"<span style='background:{c['accent']};color:{c['accent_ink']};'>{self._ipa_escape(sy)}</span>"
                         if n == j else self._ipa_escape(sy))
                        for n, sy in enumerate(ipa_syl))
                parts.append(f"<span style='white-space:nowrap;background:{c['accent_soft']};color:{c['text']};'>&nbsp;{t}&nbsp;</span>")
            else:
                parts.append(f"<span style='white-space:nowrap;'>{t}</span>")  # nunca partir una palabra
        tail = f"  <span style='color:{c['muted']}'>≈</span>" if self.ipa_approx else ""
        self.ipa_label.setText("/" + " ".join(parts) + "/" + tail)
        need = self.ipa_label.heightForWidth(max(50, self.ipa_label.width()))
        if need > 0 and need != self.ipa_label.height():
            self._relayout()   # el resaltado puede sumar un renglón: que no quede cortado

    @staticmethod
    def _ipa_escape(x: str) -> str:
        # U+2060 (unión de palabra) antes de las marcas de acento: Qt partía la palabra en "ˈ"
        return html.escape(x).replace("ˈ", "\u2060ˈ").replace("ˌ", "\u2060ˌ")

    def _render_words(self):
        if not hasattr(self, "colors"):
            return
        c = self.colors
        parts = []
        playing = self.state == "playing"
        for i, w in enumerate(self.words):
            t = html.escape(w.text)
            if playing and i == self.active_word:
                _j, k, _i, ortho = self._syllable_state()
                if len(ortho) > 1 and k >= 0:
                    t = "".join(
                        (f"<span style='background:{c['accent']};color:{c['accent_ink']};'>{html.escape(sy)}</span>"
                         if n == k else html.escape(sy))
                        for n, sy in enumerate(ortho))
                    parts.append(f"<span style='white-space:nowrap;background:{c['accent_soft']};color:{c['text']};'>&nbsp;{t}&nbsp;</span>")
                else:
                    parts.append(f"<span style='background:{c['accent']};color:{c['accent_ink']};'>&nbsp;{t}&nbsp;</span>")
            elif playing and i > self.active_word:
                parts.append(f"<span style='color:{c['muted']};'>{t}</span>")
            else:
                parts.append(t)
        self.words_label.setText(" ".join(parts))
        self._fit_text()
        if playing:
            self._scroll_to_word()

    def set_progress(self, progress: float, word_index: int, word_fraction: float = 0.0):
        self.wave.progress = progress
        self.wave.update()
        self.active_frac = word_fraction
        if word_index == self.active_word:
            j, k, _a, _b = self._syllable_state()
            if (j, k) != self._syl:  # misma palabra, otra sílaba: sólo se repintan los textos
                self._syl = (j, k)
                self._render_words()
                self._render_ipa()
            return
        if word_index != self.active_word:
            self.active_word = word_index
            j, k, _a, _b = self._syllable_state()
            self._syl = (j, k)
            self._render_words()
            self._render_ipa()
            self._relayout()   # el resaltado puede cambiar cuántos renglones ocupan el texto y el IPA
            QTimer.singleShot(0, self._relayout)  # y otra vez cuando Qt terminó de aplicar el tamaño nuevo

    def set_state(self, state: str):
        self.state = state
        self.btn_play.set_state(state)
        self.wave.set_animated(state == "playing")
        if state == "idle":
            self.active_word = -1
            self.wave.progress = 0
        self._render_words()
        self._render_ipa()

    def set_notice(self, msg: str, seconds: float = 0):
        self.notice.setText(msg)
        self.notice.setVisible(bool(msg))
        if msg and seconds:
            self._notice_timer.start(int(seconds * 1000))
        self.adjustSize()

    def sync_controls(self):
        self.btn_us.setChecked(self.settings.accent == "en-US")
        self.btn_gb.setChecked(self.settings.accent == "en-GB")
        for value, b in self.speed_btns:
            b.setChecked(abs(value - self.settings.speed) < 1e-6)
        self.btn_repeat.setChecked(self.repeat)
        self.ipa_wrap.setVisible(self.settings.show_ipa)

    def _place(self, anchor: QPoint) -> QPoint:
        screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
        area: QRect = screen.availableGeometry()
        size = self.sizeHint()
        x = anchor.x() - size.width() // 2
        y = anchor.y() + 14
        if y + size.height() > area.bottom():
            y = anchor.y() - size.height() - 14
        x = max(area.left() + 4, min(x, area.right() - size.width() - 4))
        y = max(area.top() + 4, min(y, area.bottom() - size.height() - 4))
        return QPoint(x, y)

    def contains_global(self, pos: QPoint) -> bool:
        return self.isVisible() and self.frameGeometry().contains(pos)

    def _toggle_play(self):
        if self.state == "idle":
            self.play_requested.emit()
        else:
            self.repeat = False
            self.btn_repeat.setChecked(False)
            self.stop_requested.emit()

    def _toggle_repeat(self):
        self.repeat = not self.repeat
        self.btn_repeat.setChecked(self.repeat)

    def _pick_accent(self, accent: str):
        self.sync_controls()  # los Pill se des-marcan solos al hacer clic; los resincronizamos
        if accent != self.settings.accent:
            self.accent_changed.emit(accent)

    def _pick_speed(self, value: float):
        self.sync_controls()
        if abs(value - self.settings.speed) > 1e-6:
            self.speed_changed.emit(value)

    def close_bubble(self):
        if self.isVisible():
            self.hide()
            self.closed.emit()


class TriggerIcon(QWidget):
    """Botoncito redondo que aparece junto al cursor tras seleccionar con el mouse."""

    clicked = Signal()

    def __init__(self):
        super().__init__(None, _floating_flags())
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(34, 34)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Escuchar pronunciación")
        self.hover = False
        self._hide_timer = QTimer(self, singleShot=True, timeout=self.hide)

    def popup(self, pos: QPoint, seconds: float = 4.0):
        self.move(pos + QPoint(10, 12))
        self.show()
        self.raise_()
        self._hide_timer.start(int(seconds * 1000))

    def enterEvent(self, _e):
        self.hover = True
        self._hide_timer.stop()
        self.update()

    def leaveEvent(self, _e):
        self.hover = False
        self._hide_timer.start(2500)
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.hide()
            self.clicked.emit()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        bg = QColor("#0b6fd8") if self.hover else QColor("#ffffff")
        fg = QColor("#ffffff") if self.hover else QColor("#0b6fd8")
        p.setPen(QPen(QColor("#c9ccd3"), 1))
        p.setBrush(bg)
        p.drawEllipse(QRectF(1, 1, 32, 32))
        p.translate(8, 8)
        p.scale(18 / 24, 18 / 24)
        p.setPen(QPen(fg, 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPath(_svg_path(ICONS["speaker"]))


def build_menu(settings, voices: list[dict], on_change: Callable[[str], None], parent=None,
               actions: dict[str, Callable[[], None]] | None = None) -> QMenu:
    """Menú compartido por la bandeja y el engranaje de la burbuja."""
    menu = QMenu(parent)
    actions = actions or {}
    if "pronounce" in actions:
        menu.addAction(f"Pronunciar selección\t{settings.hotkey}", actions["pronounce"])
        menu.addAction("Pronunciar portapapeles", actions["clipboard"])
        menu.addAction("Escribir un texto…", actions["type"])
        menu.addSeparator()

    def radio(title: str, options: list[tuple[str, object]], current, apply: Callable[[object], None]):
        sub = menu.addMenu(title)
        group = QActionGroup(sub)
        for label, value in options:
            a = QAction(label, sub, checkable=True, checked=(value == current))
            a.triggered.connect(lambda _=False, v=value: apply(v))
            group.addAction(a)
            sub.addAction(a)

    def setter(attr):
        def apply(v):
            setattr(settings, attr, v)
            settings.save()
            on_change(attr)
        return apply

    radio("Acento", [("Inglés de EE. UU.", "en-US"), ("Inglés británico", "en-GB")], settings.accent, setter("accent"))
    radio("Voz (EE. UU.)", [(v["label"], v["id"]) for v in voices if v["accent"] == "en-US"], settings.voice_us, setter("voice_us"))
    radio("Voz (Reino Unido)", [(v["label"], v["id"]) for v in voices if v["accent"] == "en-GB"], settings.voice_gb, setter("voice_gb"))
    radio("Velocidad", [(label, value) for value, label in SPEEDS], settings.speed, setter("speed"))
    radio("Tema", [("Según Windows", "system"), ("Oscuro", "dark"), ("Claro", "light")], settings.theme, setter("theme"))
    menu.addSeparator()

    def toggle(label: str, attr: str):
        a = QAction(label, menu, checkable=True, checked=bool(getattr(settings, attr)))
        a.toggled.connect(lambda v: setter(attr)(bool(v)))
        menu.addAction(a)

    toggle("Ícono al seleccionar con el mouse", "mouse_mode")
    toggle("Reproducir al abrir", "auto_play")
    toggle("Mostrar IPA", "show_ipa")
    toggle("Ignorar texto que no parece inglés", "only_english")
    if sys.platform == "win32":
        toggle("Iniciar con Windows", "start_with_windows")
    if "quit" in actions:
        menu.addSeparator()
        menu.addAction("Abrir carpeta de datos", actions["data"])
        menu.addAction("Acerca de", actions["about"])
        menu.addAction("Salir", actions["quit"])
    return menu
