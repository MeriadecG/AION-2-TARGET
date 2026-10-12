"""Onglet DPS : moteur réseau passif (A2Tools), tableau en direct, ping, overlay, historiques."""
from __future__ import annotations

import sys
import time

from PyQt6.QtCore import QPoint, QRectF, Qt, QTimer, QUrl
from PyQt6.QtGui import QBrush, QColor, QDesktopServices, QFont, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLabel,
                             QLineEdit, QMessageBox, QPushButton, QSpinBox, QTabWidget, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout, QWidget)

from .engine_client import EngineClient, grant_capture_command, has_capture_capability, job_fr
from .style import fmt

GOLD = "#C9A227"
MODES = [("bossTargets", "Boss"), ("lastHitByMe", "Dernière cible frappée"),
         ("mostDamage", "Cible la plus touchée"), ("allTargets", "Toutes les cibles"),
         ("trainTargets", "Mannequins d'entraînement")]
JOB_COLORS = {"Gladiateur": "#E8A33D", "Templier": "#6FA8DC", "Assassin": "#C27BA0", "Rôdeur": "#93C47D",
              "Sorcier": "#8E7CC3", "Spiritualiste": "#76A5AF", "Clerc": "#F1E3A0", "Aède": "#E06666"}
WARNING = (
    "Le module DPS lit une COPIE du trafic réseau du jeu sur ton PC (comme Wireshark) :\n"
    "aucune injection, aucune modification du jeu, aucune écriture réseau, aucun déchiffrement.\n\n"
    "NCSoft n'a publié ni autorisation ni interdiction explicite des compteurs de dégâts ; ses "
    "règles sanctionnent largement les « programmes tiers ». Le risque pour ton compte est faible "
    "mais n'est pas nul.\n\n"
    "Windows : nécessite Npcap (npcap.com). Linux : autorisation de capture (mot de passe, une fois "
    "par version du moteur).\n\nActiver le module ?")


def fmt_duration(ms: int) -> str:
    s = max(0, int(ms // 1000))
    return f"{s // 60}:{s % 60:02d}"


def fmt_short(n: float | int | None) -> str:
    if n is None:
        return "—"
    n = float(n)
    if n >= 1e6:
        return f"{n / 1e6:.2f} M".replace(".", ",")
    if n >= 1e4:
        return f"{n / 1e3:.1f} k".replace(".", ",")
    return fmt(int(n))


def ping_text(ms) -> str:
    return f"{ms} ms" if ms else "—"


# --- overlay DPS ----------------------------------------------------------------------------

class DpsOverlay(QWidget):
    """Petit tableau par-dessus le jeu : clics traversants en jeu, déplaçable en mode réglage."""

    def __init__(self, cfg):
        super().__init__()
        from .overlay import overlay_flags
        self.cfg = cfg
        self._flags = overlay_flags
        self.edit = False
        self.data: dict | None = None
        self._drag: QPoint | None = None
        self._resize = False
        self.setWindowTitle("Aion Target - DPS")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.apply()

    def apply(self):
        self.setWindowFlags(self._flags(sys.platform, self.edit, self.cfg.overlay_above_fullscreen))
        x, y, w, h = self.cfg.dps_overlay_rect
        self.setGeometry(x, y, max(180, w), max(80, h))
        self.setVisible(self.cfg.dps_enabled and self.cfg.dps_overlay)
        self.update()

    def set_edit(self, on: bool):
        if self.edit and not on:
            g = self.geometry()
            self.cfg.dps_overlay_rect = [g.x(), g.y(), g.width(), g.height()]
            self.cfg.save()
        self.edit = on
        self.apply()

    def show_data(self, data: dict):
        self.data = data
        if self.isVisible():
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect()
        bg = QColor(15, 17, 22)
        bg.setAlphaF(max(0.15, min(1.0, self.cfg.dps_overlay_opacity)))
        p.setBrush(bg)
        p.setPen(QPen(QColor(GOLD) if self.edit else QColor(60, 64, 74), 1.5 if self.edit else 1))
        p.drawRoundedRect(QRectF(r).adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
        d = self.data or {}
        rows = (d.get("rows") or [])[: max(1, self.cfg.dps_overlay_rows)]
        f = QFont("Sans Serif")
        f.setPixelSize(12)
        f.setBold(True)
        p.setFont(f)
        fm = QFontMetrics(f)
        target = (d.get("target") or {}).get("name") or ("Réglage : glisser / coin = taille" if self.edit else "DPS")
        p.setPen(QColor(GOLD))
        p.drawText(8, 4 + fm.ascent(), fm.elidedText(target, Qt.TextElideMode.ElideRight, r.width() - 110))
        right = f"{fmt_duration(d.get('battle_ms') or 0)} · {ping_text(d.get('ping_ms'))}"
        p.setPen(QColor(170, 175, 185))
        p.drawText(r.width() - 8 - fm.horizontalAdvance(right), 4 + fm.ascent(), right)
        y = 8 + fm.height()
        row_h = max(16, (r.height() - y - 6) // max(1, self.cfg.dps_overlay_rows))
        f.setBold(False)
        f.setPixelSize(max(10, min(14, row_h - 4)))
        p.setFont(f)
        fm = QFontMetrics(f)
        top = max((x.get("damage") or 0) for x in rows) if rows else 1
        for i, row in enumerate(rows):
            job = job_fr(row.get("job", ""))
            frac = (row.get("damage") or 0) / top if top else 0
            bar = QColor(JOB_COLORS.get(job, "#888888"))
            bar.setAlpha(110 if not row.get("me") else 170)
            p.fillRect(QRectF(6, y, (r.width() - 12) * frac, row_h - 3), bar)
            p.setPen(QColor(240, 240, 240))
            name = f"{i + 1}. {row.get('name')}"
            val = f"{fmt_short(row.get('dps'))}/s  {round((row.get('share') or 0) * 100)} %"
            p.drawText(10, y + (row_h - 3 + fm.ascent() - fm.descent()) // 2,
                       fm.elidedText(name, Qt.TextElideMode.ElideRight, r.width() - fm.horizontalAdvance(val) - 30))
            p.drawText(r.width() - 10 - fm.horizontalAdvance(val), y + (row_h - 3 + fm.ascent() - fm.descent()) // 2, val)
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
            self.resize(max(180, pos.x()), max(80, pos.y()))
        else:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _):
        self._drag, self._resize = None, False


# --- onglet ---------------------------------------------------------------------------------

def _tree(headers: list[str]) -> QTreeWidget:
    t = QTreeWidget()
    t.setHeaderLabels(headers)
    t.setRootIsDecorated(False)
    t.setAlternatingRowColors(True)
    t.setUniformRowHeights(True)
    t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    t.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    for c in range(1, len(headers)):
        t.header().setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
    t.setStyleSheet("QTreeWidget { background: #1d2027; color: #d6d6d6; border: 1px solid #2b2f38;"
                    " border-radius: 6px; alternate-background-color: #20232b; }"
                    "QHeaderView::section { background: #1d2027; color: #8a8f99; border: none; padding: 3px; }")
    return t


class DpsTab(QWidget):
    def __init__(self, cfg, history, card, log, on_players=None, on_party=None):
        super().__init__()
        self.cfg, self.history, self.log = cfg, history, log
        self.on_players, self.on_party = on_players, on_party
        self.engine: EngineClient | None = None
        self.overlay = DpsOverlay(cfg)
        self.setObjectName("body")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 6, 0)
        root.setSpacing(10)

        g = card(root, "DPS METER (MODULE RÉSEAU PASSIF)")
        self.chk = QCheckBox("Activer le DPS meter")
        self.chk.setChecked(cfg.dps_enabled)
        self.chk.toggled.connect(self._toggle)
        g.addWidget(self.chk, 0, 0, 1, 3)
        self.chk_obs_only = QCheckBox("Observation réseau légère (sans DPS) : précharger les GS/CP des "
                                      "joueurs vus en jeu")
        self.chk_obs_only.setToolTip(
            "Même capture passive que le DPS meter, mais sans calcul affiché : les joueurs qui passent "
            "dans le flux du jeu sont cherchés en mémoire (immédiat) puis, si besoin, sur shugo.gg "
            "(débit limité), pour que leur score s'affiche tout de suite quand tu les cibles.\n"
            "Ce n'est PAS la liste complète des joueurs de l'instance.")
        self.chk_obs_only.setChecked(cfg.observe_enabled)
        self.chk_obs_only.toggled.connect(self._toggle_observe)
        g.addWidget(self.chk_obs_only, 6, 0, 1, 3)
        self.state = QLabel()
        self.state.setObjectName("status")
        self.state.setWordWrap(True)
        g.addWidget(self.state, 1, 0, 1, 3)
        self.btn_fix = QPushButton()
        self.btn_fix.setVisible(False)
        self.btn_fix.clicked.connect(self._fix)
        g.addWidget(self.btn_fix, 2, 0, 1, 3)
        g.addWidget(QLabel("Cible suivie"), 3, 0)
        self.mode = QComboBox()
        for key, label in MODES:
            self.mode.addItem(label, key)
        self.mode.setCurrentIndex(max(0, self.mode.findData(cfg.dps_mode)))
        self.mode.currentIndexChanged.connect(self._mode)
        g.addWidget(self.mode, 3, 1, 1, 2)
        self.btn_reset = QPushButton("Nouveau combat (remise à zéro)")
        self.btn_reset.clicked.connect(lambda: self.engine and self.engine.reset())
        g.addWidget(self.btn_reset, 4, 0, 1, 3)
        self.ping = QLabel("Ping du jeu : —")
        self.ping.setToolTip("Aller-retour MESURÉ entre ton PC et le serveur du jeu (horodatage des "
                             "trames ping du client). Le temps de traitement côté serveur n'est pas "
                             "observable et n'est pas inclus.")
        g.addWidget(self.ping, 5, 0, 1, 3)

        o = card(root, "OVERLAY DPS")
        self.chk_ov = QCheckBox("Afficher par-dessus le jeu")
        self.chk_ov.setChecked(cfg.dps_overlay)
        self.chk_ov.toggled.connect(self._overlay_toggled)
        self.btn_ov = QPushButton("Placer / redimensionner")
        self.btn_ov.setCheckable(True)
        self.btn_ov.toggled.connect(self._overlay_edit)
        o.addWidget(self.chk_ov, 0, 0, 1, 2)
        o.addWidget(self.btn_ov, 1, 0, 1, 2)
        o.addWidget(QLabel("Lignes"), 2, 0)
        self.sp_rows = QSpinBox()
        self.sp_rows.setRange(1, 12)
        self.sp_rows.setValue(cfg.dps_overlay_rows)
        self.sp_rows.valueChanged.connect(self._overlay_rows)
        o.addWidget(self.sp_rows, 2, 1)
        o.addWidget(QLabel("Opacité %"), 3, 0)
        self.sp_op = QSpinBox()
        self.sp_op.setRange(15, 100)
        self.sp_op.setValue(int(cfg.dps_overlay_opacity * 100))
        self.sp_op.valueChanged.connect(self._overlay_opacity)
        o.addWidget(self.sp_op, 3, 1)

        live = card(root, "COMBAT EN COURS")
        self.live_title = QLabel("—")
        self.live_title.setObjectName("status")
        live.addWidget(self.live_title, 0, 0)
        self.table = _tree(["Joueur", "Classe", "DPS", "Dégâts", "%"])
        self.table.setMinimumHeight(150)
        live.addWidget(self.table, 1, 0)

        hist = card(root, "HISTORIQUE")
        self.htabs = QTabWidget()
        hist.addWidget(self.htabs, 0, 0)
        # Combats
        fw = QWidget()
        fl = QVBoxLayout(fw)
        fl.setContentsMargins(0, 6, 0, 0)
        self.f_search = QLineEdit()
        self.f_search.setPlaceholderText("Rechercher un boss ou un joueur…")
        self.f_search.textChanged.connect(self.refresh_fights)
        fl.addWidget(self.f_search)
        self.fights = _tree(["Boss", "Date", "Durée", "Total", "Ping"])
        self.fights.itemSelectionChanged.connect(self._fight_selected)
        fl.addWidget(self.fights, 2)
        self.detail = _tree(["Joueur / compétence", "Classe", "Dégâts", "DPS"])
        self.detail.setRootIsDecorated(True)
        fl.addWidget(self.detail, 3)
        btn_del = QPushButton("Supprimer ce combat")
        btn_del.clicked.connect(self._delete_fight)
        fl.addWidget(btn_del)
        self.htabs.addTab(fw, "Combats")
        # Rencontres
        ew = QWidget()
        el = QVBoxLayout(ew)
        el.setContentsMargins(0, 6, 0, 0)
        row = QHBoxLayout()
        self.e_search = QLineEdit()
        self.e_search.setPlaceholderText("Nom ou serveur…")
        self.e_search.textChanged.connect(self.refresh_encounters)
        self.e_order = QComboBox()
        for key, label in (("recent", "Plus récents"), ("count", "Plus croisés"), ("cp", "CP"), ("name", "Nom")):
            self.e_order.addItem(label, key)
        self.e_order.currentIndexChanged.connect(self.refresh_encounters)
        self.e_fav = QCheckBox("Favoris")
        self.e_fav.toggled.connect(self.refresh_encounters)
        row.addWidget(self.e_search, 1)
        row.addWidget(self.e_order)
        row.addWidget(self.e_fav)
        el.addLayout(row)
        self.enc = _tree(["Joueur", "Serveur", "Vu", "Dernière fois", "GS", "CP", "Donnée", "Fiabilité"])
        el.addWidget(self.enc, 1)
        row2 = QHBoxLayout()
        b_fav = QPushButton("★ Favori")
        b_fav.clicked.connect(self._toggle_fav)
        b_del = QPushButton("Supprimer")
        b_del.clicked.connect(self._delete_enc)
        row2.addWidget(b_fav)
        row2.addWidget(b_del)
        el.addLayout(row2)
        self.chk_obs = QCheckBox("Noter aussi les joueurs seulement vus par le réseau")
        self.chk_obs.setChecked(cfg.dps_record_observed)
        self.chk_obs.toggled.connect(lambda on: self._set("dps_record_observed", on))
        el.addWidget(self.chk_obs)
        self.htabs.addTab(ew, "Rencontres")
        lim = QHBoxLayout()
        lim.addWidget(QLabel("Garder"))
        self.sp_f = QSpinBox()
        self.sp_f.setRange(10, 100000)
        self.sp_f.setValue(cfg.history_max_fights)
        self.sp_f.setSuffix(" combats")
        self.sp_e = QSpinBox()
        self.sp_e.setRange(100, 1000000)
        self.sp_e.setSingleStep(1000)
        self.sp_e.setValue(cfg.history_max_encounters)
        self.sp_e.setSuffix(" rencontres")
        self.sp_f.valueChanged.connect(self._limits)
        self.sp_e.valueChanged.connect(self._limits)
        lim.addWidget(self.sp_f)
        lim.addWidget(self.sp_e)
        hist.addLayout(lim, 1, 0)
        root.addStretch(1)

        self._last_dps = 0.0
        self._watch = QTimer(self)
        self._watch.timeout.connect(self._stale_check)
        self._watch.start(2000)
        self.refresh_fights()
        self.refresh_encounters()
        self._render_state({})
        if self._wanted():
            QTimer.singleShot(500, self._start)

    # --- moteur ---
    def _set(self, attr, value):
        setattr(self.cfg, attr, value)
        self.cfg.save()

    def _wanted(self) -> bool:
        return bool(self.cfg.dps_enabled or self.cfg.observe_enabled)

    def _ack(self, box: QCheckBox) -> bool:
        if self.cfg.dps_warning_ack:
            return True
        ok = QMessageBox.question(self, "Capture réseau passive", WARNING) == QMessageBox.StandardButton.Yes
        if not ok:
            box.blockSignals(True)
            box.setChecked(False)
            box.blockSignals(False)
            return False
        self.cfg.dps_warning_ack = True
        return True

    def _apply_engine(self):
        if self._wanted():
            self._start()
        else:
            self._stop()
        self.overlay.apply()

    def _toggle(self, on: bool):
        if on and not self._ack(self.chk):
            return
        self._set("dps_enabled", on)
        self._apply_engine()

    def _toggle_observe(self, on: bool):
        if on and not self._ack(self.chk_obs_only):
            return
        self._set("observe_enabled", on)
        self._apply_engine()

    def _start(self):
        if self.engine is None:
            self.engine = EngineClient(self.log, self.cfg.dps_mode, parent=self)
            self.engine.status.connect(self._render_state)
            self.engine.dps.connect(self._on_dps)
            self.engine.fight.connect(self._on_fight)
            self.engine.stopped.connect(lambda r: self._render_state({**self.engine.last_status, "stopped": r}))
            if self.on_players:
                self.engine.players.connect(self.on_players)
            if self.on_party:
                self.engine.party.connect(self.on_party)
        err = self.engine.start()
        self.engine.set_publish_dps(bool(self.cfg.dps_enabled))
        if err:
            self._render_state({"capture": "missing_engine", "detail": err})

    def _stop(self):
        if self.engine:
            self.engine.stop()
        self._render_state({})

    def shutdown(self):
        self._stop()
        self.overlay.hide()

    def _render_state(self, st: dict):
        cap = st.get("capture")
        self.btn_fix.setVisible(False)
        if not self._wanted():
            text = "Désactivé : aucune capture, aucune ressource utilisée."
        elif cap == "missing_engine":
            text = f"<span style='color:#E5484D'>●</span> {st.get('detail')}"
        elif cap == "missing_library":
            if sys.platform == "win32":
                text = "<span style='color:#E5484D'>●</span> Npcap n'est pas installé."
                self.btn_fix.setText("Télécharger Npcap (npcap.com)")
            else:
                text = "<span style='color:#E5484D'>●</span> libpcap manquant : sudo dnf install libpcap"
            self.btn_fix.setVisible(sys.platform == "win32")
        elif cap == "no_permission":
            text = "<span style='color:#E5484D'>●</span> Capture refusée : il manque l'autorisation."
            if sys.platform.startswith("linux"):
                self.btn_fix.setText("Autoriser la capture (mot de passe administrateur)")
                self.btn_fix.setVisible(True)
            else:
                text += " Lance Aion Target en administrateur, ou réinstalle Npcap sans restriction admin."
        elif cap == "running" and st.get("game_port"):
            text = f"<span style='color:#3DD68C'>●</span> Flux du jeu repéré (port {st['game_port']})."
            if not self.cfg.dps_enabled:
                text += " Observation seule (pas de DPS)."
        elif cap == "running":
            text = ("<span style='color:#E8B04B'>●</span> Capture active, en attente du jeu "
                    "(lance AION 2 ou change de zone).")
        elif st.get("stopped"):
            text = f"<span style='color:#E5484D'>●</span> Moteur arrêté ({st['stopped']})."
        else:
            text = "Démarrage du moteur…"
        self.state.setText(text)

    def _fix(self):
        if sys.platform == "win32":
            QDesktopServices.openUrl(QUrl("https://npcap.com/#download"))
            return
        if not self.engine or not self.engine.path:
            return
        import subprocess
        cmd = grant_capture_command(self.engine.path)
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0 and has_capture_capability(self.engine.path) is not False:
            self.log("Moteur : autorisation de capture accordée")
            self.engine.stop()
            self.engine.last_status = {}
            self._start()
        else:
            QMessageBox.warning(self, "Autorisation", "Autorisation refusée ou impossible :\n"
                                + (r.stderr or r.stdout or "pkexec indisponible"))

    def _mode(self):
        mode = self.mode.currentData()
        self._set("dps_mode", mode)
        if self.engine:
            self.engine.set_mode(mode)

    # --- données ---
    def _on_dps(self, d: dict):
        self._last_dps = time.monotonic()
        self.overlay.show_data(d)
        self.ping.setText(f"Ping du jeu : {ping_text(d.get('ping_ms'))} (aller-retour mesuré)")
        tgt = d.get("target") or {}
        self.live_title.setText(f"{tgt.get('name') or 'Cible'} · {fmt_duration(d.get('battle_ms') or 0)} · "
                                f"total {fmt_short(d.get('total'))}")
        self.table.clear()
        for row in d.get("rows") or []:
            it = QTreeWidgetItem([row.get("name", "?"), job_fr(row.get("job", "")),
                                  fmt_short(row.get("dps")), fmt_short(row.get("damage")),
                                  f"{round((row.get('share') or 0) * 100)} %"])
            if row.get("me"):
                for c in range(5):
                    it.setForeground(c, QBrush(QColor("#f2d98a")))
            self.table.addTopLevelItem(it)

    def _stale_check(self):
        if self.engine and self.engine.running and time.monotonic() - self._last_dps > 10:
            self.ping.setText("Ping du jeu : — (pas de données récentes)")

    def _on_fight(self, record: dict):
        if self.history.add_fight(record, self.engine.ping_ms if self.engine else None):
            self.log(f"Combat enregistré : {record.get('bossName') or '?'}")
            self.refresh_fights()

    # --- historique ---
    def refresh_fights(self):
        self.fights.clear()
        for f in self.history.fights(self.f_search.text().strip()):
            it = QTreeWidgetItem([f["boss"] or "?", time.strftime("%d/%m %H:%M", time.localtime(f["started_ms"] / 1000)),
                                  fmt_duration(f["duration_ms"]), fmt_short(f["total"]), ping_text(f["ping_ms"])])
            it.setData(0, Qt.ItemDataRole.UserRole, f["id"])
            self.fights.addTopLevelItem(it)

    def _fight_selected(self):
        self.detail.clear()
        items = self.fights.selectedItems()
        if not items:
            return
        d = self.history.fight_detail(items[0].data(0, Qt.ItemDataRole.UserRole))
        if not d:
            return
        for a in d["actors"]:
            top = QTreeWidgetItem([a["name"], job_fr(a["job"]), fmt_short(a["damage"]), fmt_short(a["dps"])])
            for sk in (d["skills"].get(a["id"]) or [])[:15]:
                QTreeWidgetItem(top, [f"  {sk['name']}", f"{sk['hits'] or 0} coups", fmt_short(sk["damage"]), ""])
            self.detail.addTopLevelItem(top)

    def _delete_fight(self):
        items = self.fights.selectedItems()
        if items:
            self.history.delete_fight(items[0].data(0, Qt.ItemDataRole.UserRole))
            self.refresh_fights()
            self.detail.clear()

    def refresh_encounters(self):
        self.enc.clear()
        now = time.time()
        for e in self.history.encounters(self.e_search.text().strip(), self.e_fav.isChecked(),
                                         self.e_order.currentData() or "recent"):
            age = "—" if not e.data_at else (f"{int((now - e.data_at) // 3600)} h" if now - e.data_at < 86400 * 2
                                              else f"{int((now - e.data_at) // 86400)} j")
            server = e.server.split("-", 1)[-1].title() if e.server else "?"
            it = QTreeWidgetItem([("★ " if e.favorite else "") + e.name, server, str(e.seen_count),
                                  time.strftime("%d/%m %H:%M", time.localtime(e.last_seen)),
                                  fmt(e.gs) if e.gs else "—", fmt(e.cp) if e.cp else "—", age,
                                  {"exact": "exacte", "unique": "nom unique", "ambigu": "homonymes"}.get(e.confidence, e.confidence)])
            it.setData(0, Qt.ItemDataRole.UserRole, (e.name, e.server, e.favorite))
            self.enc.addTopLevelItem(it)

    def _toggle_fav(self):
        items = self.enc.selectedItems()
        if items:
            name, server, fav = items[0].data(0, Qt.ItemDataRole.UserRole)
            self.history.set_favorite(name, server, not fav)
            self.refresh_encounters()

    def _delete_enc(self):
        items = self.enc.selectedItems()
        if items:
            name, server, _ = items[0].data(0, Qt.ItemDataRole.UserRole)
            self.history.delete_encounter(name, server)
            self.refresh_encounters()

    def _limits(self):
        self.cfg.history_max_fights, self.cfg.history_max_encounters = self.sp_f.value(), self.sp_e.value()
        self.history.max_fights, self.history.max_encounters = self.sp_f.value(), self.sp_e.value()
        self.cfg.save()

    # --- overlay ---
    def _overlay_toggled(self, on: bool):
        self._set("dps_overlay", on)
        self.overlay.apply()

    def _overlay_edit(self, on: bool):
        if on and not self.cfg.dps_overlay:
            self.chk_ov.setChecked(True)
        self.overlay.set_edit(on)
        if on:
            self.overlay.show()
        self.btn_ov.setText("Enregistrer la position" if on else "Placer / redimensionner")

    def _overlay_rows(self, n: int):
        self._set("dps_overlay_rows", n)
        self.overlay.update()

    def _overlay_opacity(self, v: int):
        self._set("dps_overlay_opacity", v / 100)
        self.overlay.update()
