"""Onglet Farm : monnaie calculée (réglages + cadres à l'écran)."""
from __future__ import annotations

import sys

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (QCheckBox, QColorDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from .currency import compute, fmt_amount
from .overlay import HANDLE, draw_segments, overlay_flags

ZONE_COLOR = QColor(0, 170, 255)


def rects_overlap(a, b) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def keep_below(frame: list[int], zone: list[int]) -> list[int]:
    """Le cadre d'affichage ne doit JAMAIS recouvrir la zone lue (l'OCR lirait notre texte)."""
    if rects_overlap(frame, zone):
        return [frame[0], zone[1] + zone[3] + 4, frame[2], frame[3]]
    return frame


class CurrencyFrame(QWidget):
    """Cadre de la monnaie : « zone » (lecture, visible seulement en réglage) ou « value »."""

    def __init__(self, ctl: "CurrencyOverlay", role: str):
        super().__init__()
        self.ctl, self.role, self.cfg = ctl, role, ctl.cfg
        self._drag: QPoint | None = None
        self._resizing = False
        self.setWindowTitle("Aion Target - " + ("lecture monnaie" if role == "zone" else "monnaie"))
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

    def rect_cfg(self):
        return self.cfg.currency_zone if self.role == "zone" else self.cfg.frame_currency

    def apply(self):
        edit = self.ctl.edit_mode
        self.setWindowFlags(overlay_flags(sys.platform, edit, self.cfg.overlay_above_fullscreen))
        x, y, w, h = self.rect_cfg()
        self.setGeometry(x, y, max(w, 20), max(h, 10))
        visible = self.cfg.currency_enabled and (edit or (self.role == "value" and self.ctl.amount is not None))
        self.setVisible(visible)
        self.update()

    def save_geometry(self):
        g = self.geometry()
        rect = [g.x(), g.y(), g.width(), g.height()]
        if self.role == "zone":
            self.cfg.currency_zone = rect
        else:
            self.cfg.frame_currency = keep_below(rect, self.cfg.currency_zone)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect()
        if self.ctl.edit_mode:
            col = ZONE_COLOR if self.role == "zone" else QColor(self.cfg.currency_color)
            fill = QColor(col)
            fill.setAlpha(70 if self.role == "zone" else 35)
            p.fillRect(r, fill)
            p.setPen(QPen(col, 2, Qt.PenStyle.DashLine))
            p.drawRect(r.adjusted(1, 1, -1, -1))
            p.fillRect(r.right() - HANDLE, r.bottom() - HANDLE, HANDLE, HANDLE, col)
            if self.role == "zone":
                p.setPen(QColor(255, 255, 255))
                p.setFont(QFont("Sans Serif", 8))
                p.drawText(r.adjusted(4, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter,
                           "Lecture : barre des monnaies")
                return
        draw_segments(p, r, self.ctl.segments(), self.cfg.font_family)

    def mousePressEvent(self, e):
        if not self.ctl.edit_mode:
            return
        pos = e.position().toPoint()
        r = self.rect()
        self._resizing = pos.x() >= r.right() - HANDLE - 2 and pos.y() >= r.bottom() - HANDLE - 2
        self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if not self.ctl.edit_mode or self._drag is None:
            return
        if self._resizing:
            pos = e.position().toPoint()
            self.resize(max(30, pos.x()), max(10, pos.y()))
        else:
            self.move(e.globalPosition().toPoint() - self._drag)
        self.update()

    def mouseReleaseEvent(self, _):
        self._drag, self._resizing = None, False


class CurrencyOverlay:
    """Les deux cadres de la monnaie + l'état affiché."""

    def __init__(self, cfg, worker):
        self.cfg, self.worker = cfg, worker
        self.edit_mode = False
        self.amount: int | None = cfg.currency_last
        self.windows = {r: CurrencyFrame(self, r) for r in ("zone", "value")}
        worker.amount.connect(self.set_amount)
        self.apply()

    def segments(self):
        amount = 100000 if self.edit_mode and self.amount is None else self.amount
        if amount is None:
            return []
        label = self.cfg.currency_label.strip()
        text = f"{label} {fmt_amount(amount)}" if label else fmt_amount(amount)
        return [(text, self.cfg.currency_color)]

    def set_amount(self, value):
        self.amount = value
        if self.cfg.currency_last != value:
            self.cfg.currency_last = value
            self.cfg.save()
        self.apply()

    def apply(self):
        for w in self.windows.values():
            w.apply()

    def set_edit(self, on: bool):
        if on == self.edit_mode:
            return
        if self.edit_mode:
            self.windows["zone"].save_geometry()
            self.windows["value"].save_geometry()
            self.cfg.save()
        self.edit_mode = on
        self.worker.paused = on                  # pas de lecture pendant le réglage
        self.apply()

    def raise_value(self):
        w = self.windows["value"]
        if w.isVisible() and not self.edit_mode:
            w.raise_()


class FarmTab(QWidget):
    """Contenu de l'onglet Farm."""

    def __init__(self, cfg, overlay: CurrencyOverlay, card):
        super().__init__()
        self.cfg, self.ov = cfg, overlay
        self.setObjectName("body")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 6, 0)
        root.setSpacing(10)

        g = card(root, "MONNAIE CALCULÉE")
        self.chk = QCheckBox("Afficher en permanence à l'écran")
        self.chk.setChecked(cfg.currency_enabled)
        self.chk.toggled.connect(self._enabled)
        g.addWidget(self.chk, 0, 0, 1, 4)

        g.addWidget(QLabel("Nom"), 1, 0)
        self.name = QLineEdit(cfg.currency_label)
        self.name.setPlaceholderText("ex. Kinah lié (vide = juste le nombre)")
        self.name.editingFinished.connect(self._label)
        g.addWidget(self.name, 1, 1, 1, 3)

        g.addWidget(QLabel("Calcul"), 2, 0)
        row = QHBoxLayout()
        self.spin_a, self.spin_b = QSpinBox(), QSpinBox()
        for sp, val in ((self.spin_a, cfg.currency_a), (self.spin_b, cfg.currency_b)):
            sp.setRange(1, 8)
            sp.setValue(val)
            sp.setPrefix("valeur ")
            sp.valueChanged.connect(self._formula)
        row.addWidget(self.spin_a)
        row.addWidget(QLabel("−"))
        row.addWidget(self.spin_b)
        row.addStretch(1)
        g.addLayout(row, 2, 1, 1, 3)

        self.read_label = QLabel("Lecture : pas encore de lecture.")
        self.read_label.setObjectName("status")
        self.read_label.setWordWrap(True)
        g.addWidget(self.read_label, 3, 0, 1, 4)

        self.btn_color = QPushButton()
        self.btn_color.setObjectName("swatch")
        self.btn_color.clicked.connect(self._color)
        g.addWidget(QLabel("Couleur"), 4, 0)
        g.addWidget(self.btn_color, 4, 1, Qt.AlignmentFlag.AlignLeft)
        self._paint_color()

        self.btn_place = QPushButton("Placer les cadres")
        self.btn_place.setCheckable(True)
        self.btn_place.toggled.connect(self._place)
        g.addWidget(self.btn_place, 5, 0, 1, 4)
        hint = QLabel("Cadre BLEU : autour des montants en haut à gauche de l'écran (lus toutes les "
                      "2 s). Second cadre : où afficher le résultat. Re-clique pour enregistrer.")
        hint.setObjectName("status")
        hint.setWordWrap(True)
        g.addWidget(hint, 6, 0, 1, 4)

        g2 = card(root, "FARM")
        soon = QLabel("À venir : kinah gagnés par heure, suivi de session, butin…")
        soon.setObjectName("status")
        soon.setWordWrap(True)
        g2.addWidget(soon, 0, 0)
        root.addStretch(1)

        self._values: list[int] = []
        overlay.worker.values.connect(self._on_values)
        overlay.worker.error.connect(lambda m: self.read_label.setText(f"Lecture impossible : {m}"))

    # --- actions ---
    def _enabled(self, on: bool):
        self.cfg.currency_enabled = on
        self.cfg.save()
        if on:
            self.ov.worker.start()
        self.ov.apply()

    def _label(self):
        self.cfg.currency_label = self.name.text().strip()
        self.cfg.save()
        self.ov.apply()

    def _formula(self):
        self.cfg.currency_a, self.cfg.currency_b = self.spin_a.value(), self.spin_b.value()
        self.cfg.save()
        self._on_values(self._values)

    def _color(self):
        c = QColorDialog.getColor(QColor(self.cfg.currency_color), self, "Couleur de la monnaie")
        if c.isValid():
            self.cfg.currency_color = c.name()
            self.cfg.save()
            self._paint_color()
            self.ov.apply()

    def _paint_color(self):
        col = self.cfg.currency_color
        dark = QColor(col).lightness() > 140
        self.btn_color.setStyleSheet(f"background:{col}; border:1px solid #555; color:{'#111' if dark else '#eee'};")
        self.btn_color.setText(col.upper())

    def _place(self, on: bool):
        if on and not self.cfg.currency_enabled:
            self.chk.setChecked(True)
        self.ov.set_edit(on)
        self.btn_place.setText("Enregistrer les cadres" if on else "Placer les cadres")

    def _on_values(self, values: list):
        self._values = list(values)
        if not values:
            self.read_label.setText("Lecture : aucun montant trouvé dans le cadre bleu.")
            return
        listed = "  ·  ".join(f"{i + 1}: {fmt_amount(v)}" for i, v in enumerate(values))
        res = compute(values, self.cfg.currency_a, self.cfg.currency_b)
        self.read_label.setText(f"Lu : {listed}<br>→ <b>{fmt_amount(res)}</b>")
