"""Composants visuels « Aether Analytics » de la fenêtre de contrôle (jamais utilisés par les overlays).

StatCard (alias StatTile), SectionCard / CollapsibleSection, StatusIndicator (alias StatusChip),
ActionButton, SettingRow, ProgressIndicator, EmptyState, PageHeader, NavButton, icônes fines.
Règle : aucune donnée inventée — « — » ou un état explicite tant qu'il n'y a pas de valeur réelle.
Animations : courtes (theme.ANIM_MS), déclenchées par une action, jamais en boucle.
"""
from __future__ import annotations

from PyQt6.QtCore import (QEasingCurve, QPointF, QPropertyAnimation, QRectF, QSize, Qt,
                          pyqtSignal)
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (QFrame, QGraphicsOpacityEffect, QGridLayout, QHBoxLayout, QLabel,
                             QProgressBar, QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget)

from . import theme

QWIDGETSIZE_MAX = (1 << 24) - 1


# --- animations discrètes -------------------------------------------------------------------

def fade_in(widget: QWidget, ms: int | None = None):
    """Apparition en fondu (120-180 ms). L'effet est retiré à la fin : aucun coût permanent."""
    eff = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(eff)
    anim = QPropertyAnimation(eff, b"opacity", widget)
    anim.setDuration(ms or theme.ANIM_MS)
    anim.setStartValue(0.35)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.finished.connect(lambda: widget.setGraphicsEffect(None))
    anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
    return anim


# --- statistiques ----------------------------------------------------------------------------

class StatCard(QFrame):
    """Carte de statistique : INTITULÉ · valeur (monospace, chiffres tabulaires) + unité · précision
    · tendance éventuelle (seulement si elle est réellement calculée)."""

    def __init__(self, caption: str, important: bool = False, tooltip: str = "", unit: str = ""):
        super().__init__()
        self.setObjectName("tile")
        self.setProperty("important", "true" if important else "false")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 11, 14, 11)
        lay.setSpacing(3)
        self.caption = QLabel(caption.upper())
        self.caption.setObjectName("tileCaption")
        row = QHBoxLayout()
        row.setSpacing(5)
        self.value = QLabel("—")
        self.value.setObjectName("tileValue")
        self.value.setFont(theme.mono_font(15))
        self.value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.unit = QLabel(unit)
        self.unit.setObjectName("tileUnit")
        self.trend = QLabel("")
        self.trend.setObjectName("tileUnit")
        row.addWidget(self.value)
        row.addWidget(self.unit, 0, Qt.AlignmentFlag.AlignBottom)
        row.addStretch(1)
        row.addWidget(self.trend, 0, Qt.AlignmentFlag.AlignBottom)
        self.sub = QLabel("")
        self.sub.setObjectName("tileSub")
        self.sub.setFont(theme.tabular(self.sub.font()))
        lay.addWidget(self.caption)
        lay.addLayout(row)
        lay.addWidget(self.sub)
        self._unit_text = unit
        if tooltip:
            self.setToolTip(tooltip)

    def set(self, value: str | None, sub: str | None = None, level: str | None = None,
            trend: str | None = None):
        empty = value in (None, "")
        self.value.setText("—" if empty else value)
        self.unit.setText("" if empty else self._unit_text)
        color = theme.LEVEL_COLORS.get(level) if level else None
        self.value.setStyleSheet(f"color:{color};" if color else "")
        self.sub.setText(sub or "")
        if trend in ("up", "down"):
            col = theme.OK if trend == "up" else theme.ERR
            self.trend.setText(f"<span style='color:{col}'>{'▲' if trend == 'up' else '▼'}</span>")
        else:
            self.trend.setText("")


StatTile = StatCard            # compatibilité


class TileRow(QWidget):
    """Rangée de cartes de statistiques : 4 colonnes, 2 quand la place manque (fenêtre étroite)."""
    NARROW = 600

    def __init__(self, tiles):
        super().__init__()
        self.tiles = list(tiles)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(10)
        self._cols = None
        self._layout(len(self.tiles))

    def _layout(self, cols: int):
        if cols == self._cols:
            return
        self._cols = cols
        for t in self.tiles:
            self.grid.removeWidget(t)
        for i, t in enumerate(self.tiles):
            self.grid.addWidget(t, i // cols, i % cols)
        for c in range(4):
            self.grid.setColumnStretch(c, 1 if c < cols else 0)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        n = len(self.tiles)
        self._layout(n if self.width() >= self.NARROW or n <= 2 else 2)

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())


def tile_row(*tiles: QWidget) -> QWidget:
    return TileRow(tiles)


# --- sections --------------------------------------------------------------------------------

class SectionCard(QFrame):
    """Carte de section : titre, description courte, contenu en grille. Repliable si demandé
    (paramètres avancés), avec une animation de hauteur courte."""
    toggled = pyqtSignal(bool)                # True = déplié

    def __init__(self, title: str, description: str | None = None, collapsible: bool = False,
                 collapsed: bool = False):
        super().__init__()
        self.setObjectName("card")
        self.collapsible = collapsible
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 14)
        outer.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(8)
        if collapsible:
            self.header = QToolButton()
            self.header.setObjectName("chevron")
            self.header.setCursor(Qt.CursorShape.PointingHandCursor)
            self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            self.header.clicked.connect(lambda: self.set_expanded(not self.expanded, animate=True))
            self._title = title.upper()
        else:
            self.header = QLabel(title.upper())
            self.header.setObjectName("cardTitle")
        head.addWidget(self.header)
        head.addStretch(1)
        outer.addLayout(head)
        self.desc = None
        if description:
            self.desc = QLabel(description)
            self.desc.setObjectName("cardDesc")
            self.desc.setWordWrap(True)
            outer.addWidget(self.desc)
        self.body = QWidget()
        self.grid = QGridLayout(self.body)
        self.grid.setContentsMargins(0, 2, 0, 0)
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(9)
        outer.addWidget(self.body)
        self.expanded = True
        self._anim = None
        if collapsible:
            self.set_expanded(not collapsed, animate=False)

    def set_expanded(self, on: bool, animate: bool = False):
        self.expanded = on
        if self.collapsible:
            self.header.setText(("▾  " if on else "▸  ") + self._title)
        if self.desc is not None and self.collapsible:
            self.desc.setVisible(on)
        if not animate:
            self.body.setMaximumHeight(QWIDGETSIZE_MAX)
            self.body.setVisible(on)
        else:
            self.body.setVisible(True)
            end = self.body.sizeHint().height() if on else 0
            start = 0 if on else self.body.height()
            anim = QPropertyAnimation(self.body, b"maximumHeight", self)
            anim.setDuration(theme.ANIM_MS)
            anim.setStartValue(start)
            anim.setEndValue(end)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)

            def done():
                self.body.setMaximumHeight(QWIDGETSIZE_MAX)
                self.body.setVisible(on)
            anim.finished.connect(done)
            anim.start()
            self._anim = anim
        self.toggled.emit(on)


def CollapsibleSection(title: str, description: str | None = None, collapsed: bool = True) -> SectionCard:
    return SectionCard(title, description, collapsible=True, collapsed=collapsed)


def card_of(grid: QGridLayout) -> QFrame | None:
    """Carte qui contient cette grille (pour la déplacer dans la page)."""
    w = grid.parentWidget()
    while w is not None and not isinstance(w, SectionCard):
        w = w.parentWidget()
    return w


# --- états -----------------------------------------------------------------------------------

class StatusIndicator(QFrame):
    """État réel d'un module : pastille colorée + nom + état ; bref fondu quand l'état change."""

    def __init__(self, label: str):
        super().__init__()
        self.setObjectName("chip")
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.label = label
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 11, 4)
        lay.setSpacing(5)
        self.text = QLabel()
        self.text.setObjectName("chipText")
        self.text.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.text)
        self.level = None
        self._state = None
        self.set_state("idle", "—")

    def set_state(self, level: str, state: str, tooltip: str = ""):
        changed = self._state is not None and (level, state) != (self.level, self._state)
        self.level, self._state = level, state
        self.text.setText(f"{theme.dot(level)}&nbsp;<b>{self.label}</b>&nbsp;"
                          f"<span style='color:{theme.MUTED}'>{state}</span>")
        self.setToolTip(tooltip or f"{self.label} : {state}")
        if changed and self.isVisible():
            fade_in(self.text)


StatusChip = StatusIndicator   # compatibilité


# --- actions, réglages, progression, vide ------------------------------------------------------

class ActionButton(QPushButton):
    """Bouton d'action : « primary » (action principale), « secondary » ou « danger » (critique)."""

    def __init__(self, text: str, kind: str = "secondary", parent=None):
        super().__init__(text, parent)
        if kind in ("primary", "danger"):
            self.setObjectName(kind)
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class SettingRow(QWidget):
    """Réglage : titre + description à gauche, commande à droite."""

    def __init__(self, title: str, description: str = "", control: QWidget | None = None):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        text = QVBoxLayout()
        text.setSpacing(1)
        t = QLabel(title)
        t.setObjectName("settingTitle")
        text.addWidget(t)
        if description:
            d = QLabel(description)
            d.setObjectName("settingDesc")
            d.setWordWrap(True)
            text.addWidget(d)
        lay.addLayout(text, 1)
        if control is not None:
            lay.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        self.control = control


class ProgressIndicator(QWidget):
    """Barre fine + valeur lisible (« 12 / 30 · Nv 1 »). Peut dépasser l'objectif (barre pleine)."""

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.label = QLabel("—")
        self.label.setObjectName("progressText")
        self.label.setFont(theme.tabular(self.label.font()))
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        self.bar.setRange(0, 1000)
        lay.addWidget(self.label)
        lay.addWidget(self.bar)

    def set(self, value: int | None, target: int | None, text: str):
        self.label.setText(text)
        if value is None or not target:
            self.bar.setValue(1000 if value else 0)
        else:
            self.bar.setValue(min(1000, int(1000 * value / target)))


class EmptyState(QLabel):
    """Absence de donnée présentée clairement (jamais de valeur fictive)."""

    def __init__(self, text: str, sub: str = ""):
        super().__init__()
        self.setObjectName("empty")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)
        self.set_text(text, sub)

    def set_text(self, text: str, sub: str = ""):
        self.setText(f"<b>{text}</b>" + (f"<br><span style='font-size:8.5pt'>{sub}</span>" if sub else ""))


class PageHeader(QWidget):
    """Titre et courte description du module."""

    def __init__(self, title: str, description: str):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 0, 0, 2)
        lay.setSpacing(2)
        self.title = QLabel(title)
        self.title.setObjectName("pageTitle")
        self.desc = QLabel(description)
        self.desc.setObjectName("pageDesc")
        self.desc.setWordWrap(True)
        lay.addWidget(self.title)
        lay.addWidget(self.desc)


# --- icônes fines (dessinées : aucun fichier, nettes en HiDPI, couleur du thème) ----------------

def _icon_path(name: str, p: QPainter):
    if name == "target":                               # réticule
        p.drawEllipse(QRectF(4, 4, 16, 16))
        p.drawEllipse(QRectF(9, 9, 6, 6))
        for a, b in (((12, 1.5), (12, 5)), ((12, 19), (12, 22.5)), ((1.5, 12), (5, 12)), ((19, 12), (22.5, 12))):
            p.drawLine(QPointF(*a), QPointF(*b))
    elif name == "dps":                                # barres d'histogramme
        for x, h in ((4, 8), (9.5, 13), (15, 17)):
            p.drawRoundedRect(QRectF(x, 21 - h, 4, h), 1.2, 1.2)
        p.drawLine(QPointF(2.5, 21.5), QPointF(21.5, 21.5))
    elif name == "bell":
        path = QPainterPath()
        path.moveTo(6, 17)
        path.lineTo(6, 11)
        path.cubicTo(6, 6.5, 9, 4, 12, 4)
        path.cubicTo(15, 4, 18, 6.5, 18, 11)
        path.lineTo(18, 17)
        path.lineTo(20, 18.5)
        path.lineTo(4, 18.5)
        path.closeSubpath()
        p.drawPath(path)
        p.drawLine(QPointF(10, 21), QPointF(14, 21))
    elif name == "pets":                               # empreinte
        p.drawEllipse(QRectF(7.5, 12, 9, 8))
        for x, y in ((4, 8.5), (8.5, 4.5), (13.5, 4.5), (18, 8.5)):
            p.drawEllipse(QRectF(x - 1.6, y - 1.6, 4.2, 4.6))
    elif name == "farm":                               # pièces empilées
        for y in (15, 10.5, 6):
            p.drawEllipse(QRectF(5, y, 14, 4.5))
        p.drawLine(QPointF(5, 8.2), QPointF(5, 17.2))
        p.drawLine(QPointF(19, 8.2), QPointF(19, 17.2))


def line_icon(name: str, color: str, size: int = 24) -> QIcon:
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(2.0)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 24, size / 24)
    p.setPen(QPen(QColor(color), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                  Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    _icon_path(name, p)
    p.end()
    return QIcon(pm)


class NavButton(QPushButton):
    """Entrée de la barre latérale : icône fine, libellé, pastille d'état éventuelle."""

    def __init__(self, key: str, label: str, icon: str):
        super().__init__()
        self.setObjectName("nav")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.key, self.label_text, self.icon_name = key, label, icon
        self.setIconSize(QSize(18, 18))
        self.dot_level: str | None = None
        self.compact = False
        self.refresh()

    def set_dot(self, level: str | None):
        self.dot_level = level
        self.refresh()

    def set_compact(self, on: bool):
        self.compact = on
        self.refresh()

    def refresh(self):
        color = theme.PRIMARY if self.isChecked() else theme.MUTED
        self.setIcon(line_icon(self.icon_name, color))
        self.setText("" if self.compact else f"  {self.label_text}")
        self.setToolTip(self.label_text)
        self.update()

    def nextCheckState(self):                 # la barre latérale gère l'état coché
        pass

    def paintEvent(self, e):
        super().paintEvent(e)
        if not self.dot_level or self.dot_level == "idle":
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.LEVEL_COLORS.get(self.dot_level, theme.MUTED)))
        r = 3.5
        if self.compact:
            x, y = self.width() - 10, 9
        else:
            x, y = self.width() - 14, self.height() / 2
        p.drawEllipse(QPointF(x, y), r, r)
        p.end()
