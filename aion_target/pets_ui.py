"""Onglet Familiers : catalogue recherchable, progression (morts / âmes), overlay de suivi,
comptage automatique des monstres tués (si le moteur réseau le prouve) et journal des décisions."""
from __future__ import annotations

import sys
import time

from PyQt6.QtCore import QPoint, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                             QMessageBox, QPushButton, QSpinBox, QTreeWidgetItem, QVBoxLayout, QWidget)

from .dps_ui import GOLD, _tree, ack_capture, describe_state, fix_capture
from .pets import GENUS_FR, level_of, parse_levels

AUTO_UNAVAILABLE = ("Comptage automatique indisponible : le moteur réseau doit tourner (onglet DPS ou "
                    "observation dans Target, ou la case ci-dessus) et signaler des morts en combat "
                    "attribuables. Le compteur manuel fonctionne toujours.")


def progress_text(prog: dict, levels: list[int]) -> str:
    lvl, nxt = level_of(prog.get("souls", 0), levels)
    target = f"{prog.get('souls', 0)}/{nxt}" if nxt else f"{prog.get('souls', 0)} (max {levels[-1]})"
    return f"Nv {lvl} · âmes {target}"


class PetOverlay(QWidget):
    """Petit HUD transparent : familiers suivis (âmes, niveau, morts)."""

    def __init__(self, cfg, tab):
        super().__init__()
        from .overlay import overlay_flags
        self.cfg, self.tab, self._flags = cfg, tab, overlay_flags
        self.edit = False
        self._drag: QPoint | None = None
        self._resize = False
        self.setWindowTitle("Aion Target - Familiers")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.apply()

    def apply(self):
        self.setWindowFlags(self._flags(sys.platform, self.edit, self.cfg.overlay_above_fullscreen))
        x, y, w, h = self.cfg.pets_overlay_rect
        self.setGeometry(x, y, max(160, w), max(40, h))
        self.setVisible(self.cfg.pets_overlay and (self.edit or bool(self.tab.store.tracked())))
        self.update()

    def set_edit(self, on: bool):
        if self.edit and not on:
            g = self.geometry()
            self.cfg.pets_overlay_rect = [g.x(), g.y(), g.width(), g.height()]
            self.cfg.save()
        self.edit = on
        self.apply()

    def lines(self) -> list[tuple[str, str]]:
        tab = self.tab
        levels = tab.store.levels(tab.catalog)
        out = []
        for pid in tab.store.tracked():
            pet = tab.catalog.by_id.get(pid)
            if not pet:
                continue
            prog = tab.store.get(pid)
            out.append((pet.name, f"{progress_text(prog, levels)} · morts {prog['kills']}"))
        if not out and self.edit:
            out = [("Papis", "Nv 1 · âmes 12/30 · morts 140")]
        return out

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect()
        bg = QColor(15, 17, 22)
        bg.setAlphaF(max(0.15, min(1.0, self.cfg.pets_overlay_opacity)))
        p.setBrush(bg)
        p.setPen(QPen(QColor(GOLD) if self.edit else QColor(60, 64, 74), 1.5 if self.edit else 1))
        p.drawRoundedRect(QRectF(r).adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
        rows = self.lines()
        f = QFont("Sans Serif")
        row_h = max(14, (r.height() - 8) // max(1, len(rows)))
        f.setPixelSize(max(10, min(14, row_h - 4)))
        p.setFont(f)
        fm = QFontMetrics(f)
        y = 4
        for name, val in rows:
            base = y + (row_h + fm.ascent() - fm.descent()) // 2
            p.setPen(QColor(GOLD))
            vw = fm.horizontalAdvance(val)
            p.drawText(8, base, fm.elidedText(name, Qt.TextElideMode.ElideRight, max(20, r.width() - vw - 24)))
            p.setPen(QColor(230, 230, 230))
            p.drawText(r.width() - 8 - vw, base, val)
            y += row_h

    def mousePressEvent(self, e):
        if not self.edit:
            return
        pos = e.position().toPoint()
        self._resize = pos.x() > self.width() - 16 and pos.y() > self.height() - 16
        self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if not self.edit or self._drag is None:
            return
        if self._resize:
            pos = e.position().toPoint()
            self.resize(max(160, pos.x()), max(40, pos.y()))
        else:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _):
        self._drag, self._resize = None, False


class PetsTab(QWidget):
    def __init__(self, cfg, catalog, store, counter, hub, card, log):
        super().__init__()
        self.cfg, self.catalog, self.store, self.counter = cfg, catalog, store, counter
        self.hub, self.log = hub, log
        self.overlay = PetOverlay(cfg, self)
        self._current: str | None = None
        self._loading = False
        self.setObjectName("body")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 6, 0)
        root.setSpacing(10)

        # --- catalogue ---
        g = card(root, "CATALOGUE DES FAMILIERS")
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Rechercher un familier ou un monstre (français ou anglais)…")
        self.search.textChanged.connect(self.refresh)
        self.genus = QComboBox()
        self.genus.addItem("Tous", None)
        self.genus.addItem("Suivis", "__tracked__")
        for gk, label in GENUS_FR.items():
            self.genus.addItem(label, gk)
        self.genus.currentIndexChanged.connect(self.refresh)
        top.addWidget(self.search, 1)
        top.addWidget(self.genus)
        g.addLayout(top, 0, 0, 1, 4)
        self.table = _tree(["Familier", "Genre", "Niveau / âmes", "Morts", "Suivi"])
        self.table.setMinimumHeight(220)
        self.table.itemSelectionChanged.connect(self._selected)
        g.addWidget(self.table, 1, 0, 1, 4)
        meta = catalog.meta
        src = " · ".join(s.get("url", "") for s in meta.get("sources", []))
        self.source = QLabel(f"{len(catalog.pets)} familiers ({meta.get('game', '?')}, catalogue du "
                             f"{meta.get('generated', '?')}). {meta.get('missing_note', '')}<br>Sources : {src}")
        self.source.setObjectName("status")
        self.source.setWordWrap(True)
        self.source.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        g.addWidget(self.source, 2, 0, 1, 4)
        btn_add = QPushButton("Ajouter un familier (nouveauté du jeu)…")
        btn_add.clicked.connect(self._add_custom)
        g.addWidget(btn_add, 3, 0, 1, 4)

        # --- fiche ---
        f = card(root, "FICHE ET PROGRESSION")
        self.title = QLabel("Choisis un familier dans la liste.")
        self.title.setWordWrap(True)
        f.addWidget(self.title, 0, 0, 1, 4)
        self.mobs = QLabel()
        self.mobs.setObjectName("status")
        self.mobs.setWordWrap(True)
        f.addWidget(self.mobs, 1, 0, 1, 4)
        self.spin_souls, self.spin_kills = QSpinBox(), QSpinBox()
        for row, (label, sp, key) in enumerate((("Âmes (manuel)", self.spin_souls, "souls"),
                                                 ("Monstres tués", self.spin_kills, "kills")), start=2):
            sp.setRange(0, 10_000_000)
            sp.valueChanged.connect(lambda v, k=key: self._set(k, v))
            f.addWidget(QLabel(label), row, 0)
            f.addWidget(sp, row, 1)
            minus, plus = QPushButton("−1"), QPushButton("+1")
            minus.clicked.connect(lambda _=False, k=key: self._add(k, -1))
            plus.clicked.connect(lambda _=False, k=key: self._add(k, +1))
            f.addWidget(minus, row, 2)
            f.addWidget(plus, row, 3)
        self.progress = QLabel()
        self.progress.setObjectName("status")
        f.addWidget(self.progress, 4, 0, 1, 4)
        self.chk_track = QCheckBox("Suivre sur l'overlay")
        self.chk_track.toggled.connect(self._track)
        self.chk_auto_pet = QCheckBox("Compter automatiquement les morts de ce familier")
        self.chk_auto_pet.toggled.connect(self._auto_pet)
        f.addWidget(self.chk_track, 5, 0, 1, 2)
        f.addWidget(self.chk_auto_pet, 5, 2, 1, 2)
        self.btn_reset = QPushButton("Remettre ce familier à zéro…")
        self.btn_reset.clicked.connect(self._reset)
        f.addWidget(self.btn_reset, 6, 0, 1, 4)
        warn = QLabel("Les âmes restent manuelles : une mort ne donne pas forcément une âme.")
        warn.setObjectName("status")
        warn.setWordWrap(True)
        f.addWidget(warn, 7, 0, 1, 4)

        # --- paliers ---
        lv = card(root, "PALIERS DE NIVEAU (ÂMES CUMULÉES)")
        self.edition = QComboBox()
        for key, th in catalog.thresholds().items():
            self.edition.addItem(th.get("label", key), key)
        self.edition.addItem("Personnalisé", "custom")
        self.edition.setCurrentIndex(max(0, self.edition.findData(store.data.get("edition") or "global")))
        self.edition.currentIndexChanged.connect(self._edition)
        lv.addWidget(self.edition, 0, 0, 1, 3)
        self.custom_levels = QLineEdit(", ".join(map(str, store.data.get("custom_levels") or [5, 30, 105])))
        self.custom_levels.setPlaceholderText("ex. 5, 30, 105")
        self.custom_levels.editingFinished.connect(self._edition)
        lv.addWidget(self.custom_levels, 0, 3)
        note = QLabel("Paliers à confirmer en jeu : modifiables à tout moment (aucune donnée perdue).")
        note.setObjectName("status")
        note.setWordWrap(True)
        lv.addWidget(note, 1, 0, 1, 4)

        # --- overlay ---
        o = card(root, "OVERLAY DES FAMILIERS SUIVIS")
        self.chk_ov = QCheckBox("Afficher par-dessus le jeu")
        self.chk_ov.setChecked(cfg.pets_overlay)
        self.chk_ov.toggled.connect(self._overlay_toggled)
        self.btn_ov = QPushButton("Placer / redimensionner")
        self.btn_ov.setCheckable(True)
        self.btn_ov.toggled.connect(self._overlay_edit)
        o.addWidget(self.chk_ov, 0, 0, 1, 2)
        o.addWidget(self.btn_ov, 1, 0, 1, 2)
        o.addWidget(QLabel("Opacité %"), 2, 0)
        self.sp_op = QSpinBox()
        self.sp_op.setRange(15, 100)
        self.sp_op.setValue(int(cfg.pets_overlay_opacity * 100))
        self.sp_op.valueChanged.connect(self._opacity)
        o.addWidget(self.sp_op, 2, 1)

        # --- automatique ---
        a = card(root, "COMPTAGE AUTOMATIQUE DES MONSTRES TUÉS (RÉSEAU)")
        self.chk_auto = QCheckBox("Activer (capture passive, même moteur que le DPS meter)")
        self.chk_auto.setChecked(bool(store.data.get("auto")))
        self.chk_auto.toggled.connect(self._toggle_auto)
        a.addWidget(self.chk_auto, 0, 0, 1, 2)
        self.auto_state = QLabel()
        self.auto_state.setObjectName("status")
        self.auto_state.setWordWrap(True)
        a.addWidget(self.auto_state, 1, 0, 1, 2)
        self.btn_fix = QPushButton()
        self.btn_fix.setVisible(False)
        self.btn_fix.clicked.connect(lambda: fix_capture(self, self.hub, self.log))
        a.addWidget(self.btn_fix, 2, 0, 1, 2)
        rule = QLabel("Compté une seule fois par monstre : mort EN COMBAT (signalée par le jeu) d'un monstre "
                      "que TU as frappé. Une disparition, des dégâts seuls ou la mort d'un monstre frappé "
                      "seulement par d'autres ne comptent pas.")
        rule.setObjectName("status")
        rule.setWordWrap(True)
        a.addWidget(rule, 3, 0, 1, 2)

        # --- journal ---
        j = card(root, "JOURNAL DES MORTS (DIAGNOSTIC)")
        self.journal = _tree(["Heure", "Monstre", "Décision", "Raison"])
        self.journal.setMinimumHeight(140)
        j.addWidget(self.journal, 0, 0)
        root.addStretch(1)

        hub.status.connect(self._render_auto)
        hub.kill.connect(self._on_kill)
        self._render_auto({"state": hub.state(), "modes": sorted(hub.modes)})
        self.refresh()
        self._refresh_journal()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_journal)
        self._timer.start(5000)
        if store.data.get("auto"):
            QTimer.singleShot(700, lambda: self.hub.set_mode("pets", True))

    # --- liste ---
    def refresh(self):
        genus = self.genus.currentData()
        tracked = set(self.store.tracked())
        if genus == "__tracked__":
            pets = [p for p in self.catalog.search(self.search.text()) if p.id in tracked]
        else:
            pets = self.catalog.search(self.search.text(), genus)
        levels = self.store.levels(self.catalog)
        self.table.setUpdatesEnabled(False)
        self.table.clear()
        for pet in pets:
            prog = self.store.data["progress"].get(pet.id) or {}
            name = pet.name + (f"  ({pet.alias})" if pet.alias else "")
            it = QTreeWidgetItem([name, GENUS_FR.get(pet.genus, pet.genus),
                                  progress_text(prog, levels) if prog else "—",
                                  str(prog.get("kills", 0)) if prog else "—",
                                  "★" if pet.id in tracked else ""])
            it.setData(0, Qt.ItemDataRole.UserRole, pet.id)
            self.table.addTopLevelItem(it)
            if pet.id == self._current:
                it.setSelected(True)
        self.table.setUpdatesEnabled(True)

    def _selected(self):
        items = self.table.selectedItems()
        if not items:
            return
        self._current = items[0].data(0, Qt.ItemDataRole.UserRole)
        self._show_current()

    def _show_current(self):
        pet = self.catalog.by_id.get(self._current)
        if not pet:
            return
        prog = self.store.get(pet.id)
        self._loading = True
        self.title.setText(f"<b>{pet.name}</b>" + (f" — {pet.alias}" if pet.alias else "")
                           + f" · {GENUS_FR.get(pet.genus, pet.genus)}"
                           + (" · spécial (pas de farm d'âmes connu)" if pet.special else ""))
        exact = [m["fr"] for m in pet.mobs if m.get("match") == "exact"]
        other = [m["fr"] for m in pet.mobs if m.get("match") != "exact"]
        txt = []
        if exact:
            txt.append("Monstre du même nom : " + ", ".join(exact))
        if other:
            more = f" (+{len(other) - 12})" if len(other) > 12 else ""
            txt.append("Même espèce (indicatif) : " + ", ".join(other[:12]) + more)
        self.mobs.setText("<br>".join(txt) or "Aucun monstre associé connu : comptage manuel.")
        self.spin_souls.setValue(prog["souls"])
        self.spin_kills.setValue(prog["kills"])
        self.chk_track.setChecked(prog["tracked"])
        self.chk_auto_pet.setChecked(prog.get("auto", True))
        self.progress.setText(progress_text(prog, self.store.levels(self.catalog))
                              + f" · paliers {'/'.join(map(str, self.store.levels(self.catalog)))}")
        self._loading = False

    # --- édition ---
    def _set(self, key, value):
        if self._loading or not self._current:
            return
        self.store.set_value(self._current, key, value)
        self._changed()

    def _add(self, key, delta):
        if not self._current:
            return
        self.store.add(self._current, key, delta)
        self._show_current()
        self._changed()

    def _reset(self):
        if not self._current:
            return
        if QMessageBox.question(self, "Familiers", "Remettre morts et âmes de ce familier à zéro ?") \
                != QMessageBox.StandardButton.Yes:
            return
        self.store.reset(self._current)
        self._show_current()
        self._changed()

    def _track(self, on):
        if self._loading or not self._current:
            return
        self.store.set_tracked(self._current, on)
        self._changed()

    def _auto_pet(self, on):
        if self._loading or not self._current:
            return
        self.store.get(self._current)["auto"] = bool(on)
        self.store.save()

    def _changed(self):
        self.refresh()
        self.overlay.apply()

    def _edition(self):
        ed = self.edition.currentData()
        self.store.data["edition"] = ed
        if ed == "custom":
            levels = parse_levels(self.custom_levels.text())
            if levels is None:
                self.custom_levels.setStyleSheet("border: 1px solid #E5484D;")
                return
            self.custom_levels.setStyleSheet("")
            self.store.data["custom_levels"] = levels
        self.store.save()
        self._show_current()
        self._changed()

    def _add_custom(self):
        name, ok = QInputDialog.getText(self, "Ajouter un familier", "Nom du familier :")
        if not ok or not name.strip():
            return
        mobs, ok = QInputDialog.getText(self, "Ajouter un familier",
                                        "Monstre(s) associé(s), séparés par des virgules (nom exact en jeu, "
                                        "facultatif) :")
        pid = self.store.add_custom(name.strip(), [m.strip() for m in (mobs or "").split(",") if m.strip()]
                                    if ok else [])
        from .pets import Catalog
        new = Catalog.load(self.store.data["custom"])
        self.catalog.pets, self.catalog.by_id = new.pets, new.by_id
        self.catalog._reindex()
        self._current = pid
        self.refresh()
        self._show_current()

    # --- overlay ---
    def _overlay_toggled(self, on):
        self.cfg.pets_overlay = on
        self.cfg.save()
        self.overlay.apply()

    def _overlay_edit(self, on):
        if on and not self.cfg.pets_overlay:
            self.chk_ov.setChecked(True)
        self.overlay.set_edit(on)
        self.btn_ov.setText("Enregistrer la position" if on else "Placer / redimensionner")

    def _opacity(self, v):
        self.cfg.pets_overlay_opacity = v / 100
        self.cfg.save()
        self.overlay.update()

    # --- automatique ---
    def _toggle_auto(self, on):
        if on and not ack_capture(self, self.cfg, self.chk_auto):
            return
        self.store.data["auto"] = bool(on)
        self.store.save()
        self.hub.set_mode("pets", on)

    def _render_auto(self, st: dict):
        text, fix = describe_state(st, bool(self.store.data.get("auto")), "pets")
        c = self.counter.counts
        if self.store.data.get("auto"):
            text += (f"<br>Morts reçues : comptées {c['counted']} · ignorées {c['ignored']} · incertaines "
                     f"{c['uncertain']}")
            if st.get("state") not in ("active", "searching"):
                text += "<br>" + AUTO_UNAVAILABLE
        self.auto_state.setText(text)
        self.btn_fix.setVisible(bool(fix))
        if fix:
            self.btn_fix.setText(fix)

    def _on_kill(self, ev: dict):
        if self.counter.on_kill(ev):
            self._changed()
            if self._current:
                self._show_current()
        self._render_auto({"state": self.hub.state(), "modes": sorted(self.hub.modes)})

    def _refresh_journal(self):
        self.journal.setUpdatesEnabled(False)
        self.journal.clear()
        for e in list(self.counter.journal)[-200:][::-1]:
            hhmm = time.strftime("%d/%m %H:%M:%S", time.localtime(e.get("t") or 0))
            self.journal.addTopLevelItem(QTreeWidgetItem([hhmm, e.get("monstre", "?"), e.get("decision", "?"),
                                                          e.get("raison", "")]))
        self.journal.setUpdatesEnabled(True)

    def shutdown(self):
        self.overlay.hide()
