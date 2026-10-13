"""Onglet Farm : monnaie calculée, Kinah / minute (réglages + cadres à l'écran)."""
from __future__ import annotations

import sys

import time

from PyQt6.QtCore import QPoint, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QFrame, QGridLayout, QHBoxLayout,
                             QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QVBoxLayout,
                             QWidget)

from .currency import Stabilizer, compute, fmt_amount
from . import theme
from .farm import KinahSession, fmt_elapsed, fmt_rate
from .ui_kit import StatTile, tile_row
from .overlay import HANDLE, draw_segments, overlay_flags

ZONE_COLOR = QColor(0, 170, 255)
ALT_COLOR = QColor(255, 140, 0)
STALE_AFTER = 20.0       # s sans lecture valide : la valeur affichée est « ancienne »


def screen_key() -> str:
    """Résolution + échelle de l'écran principal (« 2560x1440@1.25 ») : clé des profils de zones."""
    from PyQt6.QtWidgets import QApplication
    scr = QApplication.primaryScreen()
    if scr is None:
        return "inconnu"
    g = scr.geometry()
    return f"{g.width()}x{g.height()}@{scr.devicePixelRatio():g}"


def apply_profile(cfg, key: str) -> bool:
    """Zones mémorisées pour cette résolution : appliquées. Vrai si un profil existait."""
    prof = (cfg.currency_profiles or {}).get(key)
    if not prof:
        return False
    if prof.get("zone"):
        cfg.currency_zone = list(prof["zone"])
    if prof.get("alt"):
        cfg.currency_zone_alt = list(prof["alt"])
    return True


def save_profile(cfg, key: str):
    cfg.currency_profiles = dict(cfg.currency_profiles or {})
    cfg.currency_profiles[key] = {"zone": list(cfg.currency_zone), "alt": list(cfg.currency_zone_alt)}


def overlay_text(fields: list, labels: dict, values: dict) -> str:
    """Texte de l'overlay Farm : champs choisis, dans l'ordre a, b, diff, rate."""
    parts = []
    for f in ("a", "b", "diff", "rate"):
        if f not in fields or values.get(f) is None:
            continue
        v = values[f]
        txt = (fmt_rate(v) + "/min") if f == "rate" else fmt_amount(v)
        lab = (labels.get(f) or "").strip()
        parts.append(f"{lab} {txt}" if lab else txt)
    return "  ·  ".join(parts)


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
        self.setWindowTitle("Aion Target - " + {"zone": "lecture monnaie", "alt": "lecture monnaie (menu)",
                                                 "value": "monnaie"}[role])
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

    def rect_cfg(self):
        return {"zone": self.cfg.currency_zone, "alt": self.cfg.currency_zone_alt,
                "value": self.cfg.frame_currency}[self.role]

    def apply(self):
        edit = self.ctl.edit_mode
        self.setWindowFlags(overlay_flags(sys.platform, edit, self.cfg.overlay_above_fullscreen))
        x, y, w, h = self.rect_cfg()
        self.setGeometry(x, y, max(w, 20), max(h, 10))
        if self.role == "alt":
            visible = edit and self.cfg.currency_alt_enabled
        else:
            visible = self.cfg.currency_enabled and (edit or (self.role == "value" and bool(self.ctl.segments())))
        self.setVisible(visible)
        self.update()

    def save_geometry(self):
        g = self.geometry()
        rect = [g.x(), g.y(), g.width(), g.height()]
        if self.role == "zone":
            self.cfg.currency_zone = rect
        elif self.role == "alt":
            self.cfg.currency_zone_alt = rect
        else:
            self.cfg.frame_currency = keep_below(rect, self.cfg.currency_zone)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect()
        if self.ctl.edit_mode:
            col = {"zone": ZONE_COLOR, "alt": ALT_COLOR}.get(self.role, QColor(self.cfg.currency_color))
            fill = QColor(col)
            fill.setAlpha(35 if self.role == "value" else 70)
            p.fillRect(r, fill)
            p.setPen(QPen(col, 2, Qt.PenStyle.DashLine))
            p.drawRect(r.adjusted(1, 1, -1, -1))
            p.fillRect(r.right() - HANDLE, r.bottom() - HANDLE, HANDLE, HANDLE, col)
            if self.role in ("zone", "alt"):
                p.setPen(QColor(255, 255, 255))
                p.setFont(QFont("Sans Serif", 8))
                p.drawText(r.adjusted(4, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter,
                           "Lecture : barre des monnaies" if self.role == "zone" else "Lecture : menu (2e emplacement)")
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
        self.rate = lambda: None            # Kinah/minute de la session (fourni par l'onglet Farm)
        self.stale = False
        self._stab = {"a": Stabilizer(), "b": Stabilizer()}
        self.key = screen_key()
        if apply_profile(cfg, self.key):
            pass                             # zones de cette résolution / échelle
        self.windows = {r: CurrencyFrame(self, r) for r in ("zone", "alt", "value")}
        worker.amount.connect(self.set_amount)
        worker.reading.connect(self._on_reading)
        self.apply()

    def _on_reading(self, rd: dict):
        changed = False
        for f in ("a", "b"):
            changed |= self._stab[f].push(rd.get(f))
        if changed and any(f in self.cfg.farm_overlay_fields for f in ("a", "b")):
            self.apply()

    def values(self) -> dict:
        return {"a": self._stab["a"].value, "b": self._stab["b"].value, "diff": self.amount,
                "rate": self.rate()}

    def segments(self):
        vals = self.values()
        if self.edit_mode:
            vals = {k: (v if v is not None else 100000) for k, v in vals.items()}
        labels = {"a": self.cfg.currency_label_a, "b": self.cfg.currency_label_b,
                  "diff": self.cfg.currency_label, "rate": "Kinah"}
        text = overlay_text(self.cfg.farm_overlay_fields or ["diff"], labels, vals)
        if not text:
            return []
        if self.stale and not self.edit_mode:
            text += "  (ancien)"
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
            if self.cfg.currency_alt_enabled:
                self.windows["alt"].save_geometry()
            self.windows["value"].save_geometry()
            save_profile(self.cfg, self.key)          # mémorisé pour cette résolution
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

        self.t_net = StatTile("Gain net observé")
        self.t_rate = StatTile("Kinah / minute", important=True)
        self.t_time = StatTile("Durée active")
        self.t_last = StatTile("Dernière mesure")
        root.addWidget(tile_row(self.t_net, self.t_rate, self.t_time, self.t_last))

        g = card(root, "MONNAIE CALCULÉE")
        self.chk = QCheckBox("Afficher en permanence à l'écran")
        self.chk.setChecked(cfg.currency_enabled)
        self.chk.toggled.connect(self._enabled)
        g.addWidget(self.chk, 0, 0, 1, 4)

        g.addWidget(QLabel("Noms"), 1, 0)
        names = QHBoxLayout()
        self.name_a = QLineEdit(cfg.currency_label_a)
        self.name_b = QLineEdit(cfg.currency_label_b)
        self.name = QLineEdit(cfg.currency_label)
        self.name_a.setPlaceholderText("valeur A")
        self.name_b.setPlaceholderText("valeur B")
        self.name.setPlaceholderText("A − B (vide = juste le nombre)")
        for w in (self.name_a, self.name_b, self.name):
            w.editingFinished.connect(self._label)
            names.addWidget(w)
        g.addLayout(names, 1, 1, 1, 3)
        check = QLabel("Les noms sont à vérifier dans l'inventaire du jeu : si A est le total et B une "
                       "partie (ex. Kinah lié), A − B est l'autre partie (ex. non lié).")
        check.setObjectName("status")
        check.setWordWrap(True)
        g.addWidget(check, 7, 0, 1, 4)

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

        # --- 2e emplacement ---
        ga = card(root, "2E EMPLACEMENT (MENU EN HAUT À DROITE)")
        self.chk_alt = QCheckBox("Lire aussi le menu quand la barre est cachée ou illisible")
        self.chk_alt.setChecked(cfg.currency_alt_enabled)
        self.chk_alt.toggled.connect(self._alt_enabled)
        ga.addWidget(self.chk_alt, 0, 0, 1, 4)
        ga.addWidget(QLabel("Valeurs"), 1, 0)
        rowa = QHBoxLayout()
        self.alt_a, self.alt_b = QSpinBox(), QSpinBox()
        for sp, val in ((self.alt_a, cfg.currency_alt_a), (self.alt_b, cfg.currency_alt_b)):
            sp.setRange(1, 8)
            sp.setValue(val)
            sp.setPrefix("valeur ")
            sp.valueChanged.connect(self._alt_formula)
        rowa.addWidget(QLabel("A ="))
        rowa.addWidget(self.alt_a)
        rowa.addWidget(QLabel("B ="))
        rowa.addWidget(self.alt_b)
        rowa.addStretch(1)
        ga.addLayout(rowa, 1, 1, 1, 3)
        hint2 = QLabel("Cadre ORANGE (« Placer les cadres ») : autour des montants du menu. Les zones "
                       f"sont mémorisées pour cette résolution ({overlay.key}). A, B et A − B viennent "
                       "toujours d'une même capture.")
        hint2.setObjectName("status")
        hint2.setWordWrap(True)
        ga.addWidget(hint2, 2, 0, 1, 4)

        # --- affichage ---
        gd = card(root, "AFFICHAGE À L'ÉCRAN")
        self.field_boxes = {}
        for i, (key, label) in enumerate((("a", "Valeur A"), ("b", "Valeur B"), ("diff", "A − B"),
                                          ("rate", "Kinah / minute"))):
            box = QCheckBox(label)
            box.setChecked(key in (cfg.farm_overlay_fields or ["diff"]))
            box.toggled.connect(self._fields)
            self.field_boxes[key] = box
            gd.addWidget(box, i // 2, i % 2)

        # --- Kinah / minute ---
        gk = card(root, "KINAH / MINUTE (SOLDE NET OBSERVÉ)")
        gk.addWidget(QLabel("Solde suivi"), 0, 0)
        self.track = QComboBox()
        for key, label in (("a", "Valeur A"), ("b", "Valeur B"), ("diff", "A − B")):
            self.track.addItem(label, key)
        self.track.setCurrentIndex(max(0, self.track.findData(cfg.farm_value)))
        self.track.currentIndexChanged.connect(self._track)
        gk.addWidget(self.track, 0, 1, 1, 3)
        btns = QHBoxLayout()
        self.btn_start = QPushButton("Démarrer")
        self.btn_pause = QPushButton("Pause")
        self.btn_reset = QPushButton("Réinitialiser")
        self.btn_reset.setObjectName("danger")
        self.btn_start.setObjectName("primary")
        self.btn_start.clicked.connect(self._start)
        self.btn_pause.clicked.connect(self._pause_resume)
        self.btn_reset.clicked.connect(self._reset)
        for b in (self.btn_start, self.btn_pause, self.btn_reset):
            btns.addWidget(b)
        gk.addLayout(btns, 1, 0, 1, 4)
        self.session_label = QLabel()
        self.session_label.setObjectName("status")
        self.session_label.setWordWrap(True)
        self.session_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        gk.addWidget(self.session_label, 2, 0, 1, 4)
        note = QLabel("Variation nette du solde (achats, ventes, marché et échanges compris : le "
                      "résultat peut être négatif). Ce n'est pas un calcul de butin. Le temps en pause "
                      "ne compte pas ; après un redémarrage, clique « Reprendre ».")
        note.setObjectName("status")
        note.setWordWrap(True)
        gk.addWidget(note, 3, 0, 1, 4)
        root.addStretch(1)

        self.session = KinahSession.from_dict(cfg.farm_session)
        overlay.rate = self.session.per_minute
        overlay.worker.extra_wanted = lambda: self.session.state == "running"
        if self.session.state == "running":
            overlay.worker.start()
        self._values: list[int] = []
        self._last_rd: dict | None = None
        self._last_valid_t: float | None = None
        overlay.worker.values.connect(self._on_values)
        overlay.worker.reading.connect(self._on_reading)
        overlay.worker.error.connect(lambda m: self.read_label.setText(f"Lecture impossible : {m}"))
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self._saved_at = time.monotonic()
        from PyQt6.QtWidgets import QApplication
        if QApplication.instance():
            QApplication.instance().aboutToQuit.connect(self._save_session)
        from . import diag
        diag.register("Farm", self._diag)
        self._render_session()

    # --- actions ---
    def _enabled(self, on: bool):
        self.cfg.currency_enabled = on
        self.cfg.save()
        if on:
            self.ov.worker.start()
        self.ov.apply()

    def _label(self):
        self.cfg.currency_label = self.name.text().strip()
        self.cfg.currency_label_a = self.name_a.text().strip()
        self.cfg.currency_label_b = self.name_b.text().strip()
        self.cfg.save()
        self.ov.apply()

    def _alt_enabled(self, on: bool):
        self.cfg.currency_alt_enabled = on
        self.cfg.save()
        self.ov.apply()

    def _alt_formula(self):
        self.cfg.currency_alt_a, self.cfg.currency_alt_b = self.alt_a.value(), self.alt_b.value()
        self.cfg.save()

    def _fields(self):
        self.cfg.farm_overlay_fields = [k for k, b in self.field_boxes.items() if b.isChecked()]
        self.cfg.save()
        self.ov.apply()

    def _track(self):
        self.cfg.farm_value = self.track.currentData()
        self.cfg.save()

    def _diag(self, _names) -> list[str]:
        """Pour le rapport : variations et âge des mesures, jamais le solde absolu."""
        s = self.session
        age = None if self._last_valid_t is None else int(time.time() - self._last_valid_t)
        rd = self._last_rd or {}
        return [f"Lecture : {'affichée' if self.cfg.currency_enabled else 'masquée'} · 2e emplacement "
                f"{'actif' if self.cfg.currency_alt_enabled else 'inactif'} · dernière zone lue : "
                f"{rd.get('zone', '—')} · dernière mesure valide il y a {age if age is not None else '—'} s",
                f"Session Kinah/minute : {s.state} · durée active {fmt_elapsed(s.elapsed())} · gain net "
                f"{fmt_amount(s.net())} · {fmt_rate(s.per_minute())}/min · lectures rejetées {s.rejected} · "
                f"mesures gardées {len(s.samples)}"]

    # --- session Kinah / minute ---
    def _save_session(self):
        self.cfg.farm_session = self.session.to_dict()
        self.cfg.save()
        self._saved_at = time.monotonic()

    def _start(self):
        if self.session.state != "idle" and QMessageBox.question(
                self, "Kinah / minute", "Recommencer une nouvelle session ? (la session actuelle est "
                "remplacée)") != QMessageBox.StandardButton.Yes:
            return
        self.session.start()
        self.ov.worker.start()
        self._save_session()
        self._render_session()

    def _pause_resume(self):
        if self.session.state == "running":
            self.session.pause()
        elif self.session.state in ("paused", "interrupted"):
            self.session.resume()
            self.ov.worker.start()
        self._save_session()
        self._render_session()

    def _reset(self):
        if QMessageBox.question(self, "Kinah / minute", "Remettre le compteur à zéro ?") \
                != QMessageBox.StandardButton.Yes:
            return
        self.session.reset()
        self._save_session()
        self._render_session()

    def _on_reading(self, rd: dict):
        self._last_rd = rd
        if rd.get(self.cfg.farm_value) is not None:
            self._last_valid_t = rd["t"]
        if self.session.feed(rd.get(self.cfg.farm_value)):
            if time.monotonic() - self._saved_at > 30:
                self._save_session()
        self._render_session()

    def _tick(self):
        age = None if self._last_valid_t is None else time.time() - self._last_valid_t
        stale = age is not None and age > STALE_AFTER
        if stale != self.ov.stale:
            self.ov.stale = stale
            self.ov.apply()
        self._render_tiles()
        if self.session.state == "running":
            self._render_session()
            if "rate" in (self.cfg.farm_overlay_fields or []):
                self.ov.apply()

    def _render_session(self):
        s = self.session
        st = {"idle": "Pas de session.", "running": "En cours.", "paused": "En pause.",
              "interrupted": "Interrompue (redémarrage) : clique « Reprendre » pour continuer."}[s.state]
        self.btn_pause.setText("Reprendre" if s.state in ("paused", "interrupted") else "Pause")
        self.btn_pause.setEnabled(s.state != "idle")
        self.btn_start.setText("Démarrer" if s.state == "idle" else "Nouvelle session")
        net = s.net()
        age = s.age()
        lines = [f"<b>{st}</b>"]
        if s.state != "idle":
            lines.append(f"Solde initial {fmt_amount(s.initial)} → actuel {fmt_amount(s.current)} · "
                         f"gain net observé <b>{fmt_amount(net)}</b>")
            lines.append(f"Durée active {fmt_elapsed(s.elapsed())} · <b>{fmt_rate(s.per_minute())} / min</b>"
                         f" · {fmt_rate(s.per_hour())} / h")
            if s.initial is None:
                lines.append("En attente de deux lectures identiques du solde…")
            if age is not None:
                old = " — " + theme.colored("ancienne", "warn") if age > STALE_AFTER else ""
                lines.append(f"Dernière mesure valide : il y a {int(age)} s{old}")
            if s.rejected:
                lines.append(f"Lectures rejetées (format invalide) : {s.rejected}")
        self.session_label.setText("<br>".join(lines))
        self._render_tiles()

    def _render_tiles(self):
        s = self.session
        net = s.net()
        state = {"idle": "pas de session", "running": "en cours", "paused": "en pause",
                 "interrupted": "interrompue"}[s.state]
        self.t_net.set(fmt_amount(net) if net is not None else None, state,
                       ("ok" if net > 0 else "err" if net < 0 else None) if net is not None else None)
        rate = s.per_minute()
        self.t_rate.set(fmt_rate(rate) if rate is not None else None,
                        f"{fmt_rate(s.per_hour())} / h" if rate is not None else "≥ 30 s actives requises")
        self.t_time.set(fmt_elapsed(s.elapsed()) if s.state != "idle" else None, "pauses exclues")
        age = None if self._last_valid_t is None else time.time() - self._last_valid_t
        if age is None:
            self.t_last.set(None, "aucune lecture valide")
        else:
            stale = age > STALE_AFTER
            self.t_last.set(f"{int(age)} s", "ancienne" if stale else "valide", "warn" if stale else None)

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
        self.btn_color.setStyleSheet(f"background:{col}; border:1px solid {theme.BORDER_STRONG}; "
                                     f"color:{theme.ON_LIGHT if dark else '#FFFFFF'};")
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
        rd = self._last_rd or {}
        if rd.get("zone") == "menu":
            res = rd.get("diff")
            where = " (menu)"
        else:
            res = compute(values, self.cfg.currency_a, self.cfg.currency_b)
            where = ""
        self.read_label.setText(f"Lu{where} : {listed}<br>→ <b>{fmt_amount(res)}</b>")
