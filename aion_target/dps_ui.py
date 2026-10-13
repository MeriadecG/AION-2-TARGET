"""Onglet DPS : moteur réseau passif (A2Tools), tableau en direct, ping, overlay, historiques."""
from __future__ import annotations

import sys
import time

from PyQt6.QtCore import QPoint, QRectF, Qt, QTimer, QUrl
from PyQt6.QtGui import QBrush, QColor, QDesktopServices, QFont, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLabel,
                             QLineEdit, QMessageBox, QPushButton, QSpinBox, QTabWidget, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout, QWidget)

from . import theme
from .engine_client import grant_capture_command, has_capture_capability, job_fr
from .ui_kit import StatTile, tile_row
from .style import fmt

GOLD = "#C9A227"          # overlay DPS (dessiné à la main, au-dessus du jeu) : inchangé
MODES = [("bossTargets", "Boss"), ("lastHitByMe", "Dernière cible frappée"),
         ("mostDamage", "Cible la plus touchée"), ("allTargets", "Toutes les cibles"),
         ("trainTargets", "Mannequins d'entraînement")]
JOB_COLORS = {"Gladiateur": "#E8A33D", "Templier": "#6FA8DC", "Assassin": "#C27BA0", "Rôdeur": "#93C47D",
              "Sorcier": "#8E7CC3", "Spiritualiste": "#76A5AF", "Clerc": "#F1E3A0", "Aède": "#E06666"}
from .engine_hub import WARNING  # noqa: E402,F401  (compatibilité)


def ack_capture(parent, cfg, box=None) -> bool:
    """Avertissement OBLIGATOIRE avant la première capture passive (commun à tous les modules)."""
    if cfg.dps_warning_ack:
        return True
    from .engine_hub import WARNING as HUB_WARNING
    ok = QMessageBox.question(parent, "Capture réseau passive", HUB_WARNING) == QMessageBox.StandardButton.Yes
    if not ok:
        if box is not None:
            box.blockSignals(True)
            box.setChecked(False)
            box.blockSignals(False)
        return False
    cfg.dps_warning_ack = True
    cfg.save()
    return True


def describe_state(st: dict, enabled: bool, mode: str) -> tuple[str, str | None]:
    """(texte d'état, libellé du bouton de réparation ou None) pour un onglet."""
    from .engine_hub import STATE_LABELS
    state = st.get("state", "off")
    others = [m for m in st.get("modes", []) if m != mode]
    shared = f" Moteur partagé avec : {', '.join(MODE_NAMES.get(m, m) for m in others)}." if others else ""
    if not enabled:
        return ("Désactivé ici." + (" " + shared.strip() if shared else
                                    " Aucune capture, aucune ressource utilisée.")), None
    red, amber, green = theme.dot("err"), theme.dot("warn"), theme.dot("ok")
    if state == "missing_engine":
        return f"{red} {st.get('detail') or STATE_LABELS[state]}", None
    if state == "missing_library":
        if sys.platform == "win32":
            return f"{red} Npcap n'est pas installé.", "Télécharger Npcap (npcap.com)"
        return f"{red} libpcap manquant : sudo dnf install libpcap", None
    if state == "no_permission":
        if sys.platform.startswith("linux"):
            return (f"{red} Capture refusée : il manque l'autorisation.",
                    "Autoriser la capture (mot de passe administrateur)")
        return (f"{red} Capture refusée : lance Aion Target en administrateur, ou réinstalle Npcap "
                "sans restriction admin."), None
    if state == "active":
        return f"{green} Flux du jeu repéré (port {st.get('game_port')}).{shared}", None
    if state == "searching":
        return f"{amber} Capture active, en attente du jeu (lance AION 2 ou change de zone).{shared}", None
    if state in ("stopped", "error"):
        return f"{red} Moteur arrêté ({st.get('stopped') or st.get('error')}).", None
    return "Démarrage du moteur…", None


MODE_NAMES = {"dps": "DPS meter", "observe": "observation (Target)", "pets": "familiers"}


def fix_capture(parent, hub, log):
    """Bouton de réparation : lien Npcap (Windows) ou autorisation cap_net_raw (Linux)."""
    if sys.platform == "win32":
        QDesktopServices.openUrl(QUrl("https://npcap.com/#download"))
        return
    if not hub.path:
        return
    import subprocess
    r = subprocess.run(grant_capture_command(hub.path), capture_output=True, text=True)
    if r.returncode == 0 and has_capture_capability(hub.path) is not False:
        log("Moteur : autorisation de capture accordée")
        hub.restart()
    else:
        QMessageBox.warning(parent, "Autorisation", "Autorisation refusée ou impossible :\n"
                            + (r.stderr or r.stdout or "pkexec indisponible"))


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
    t.setFont(theme.tabular(t.font()))         # colonnes de chiffres alignées (style : thème commun)
    return t


class DpsTab(QWidget):
    def __init__(self, cfg, history, card, log, hub):
        super().__init__()
        self.cfg, self.history, self.log = cfg, history, log
        self.hub = hub
        self.overlay = DpsOverlay(cfg)
        self.setObjectName("body")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 6, 0)
        root.setSpacing(10)

        self.t_ping = StatTile("Ping", tooltip="Aller-retour mesuré PC ↔ serveur du jeu.")
        self.t_time = StatTile("Combat")
        self.t_total = StatTile("Total dégâts", important=True)
        self.t_fights = StatTile("Combats enregistrés")
        root.addWidget(tile_row(self.t_ping, self.t_time, self.t_total, self.t_fights))

        g = card(root, "DPS METER (MODULE RÉSEAU PASSIF)")
        self.chk = QCheckBox("Activer le DPS meter")
        self.chk.setChecked(cfg.dps_enabled)
        self.chk.toggled.connect(self._toggle)
        g.addWidget(self.chk, 0, 0, 1, 3)
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
        self.btn_reset.clicked.connect(self.hub.reset)
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
        btn_del.setObjectName("danger")
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
        b_del.setObjectName("danger")
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
        hub.status.connect(self._render_state)
        hub.dps.connect(self._on_dps)
        hub.fight.connect(self._on_fight)
        self._render_state({})
        if cfg.dps_enabled:
            QTimer.singleShot(500, lambda: self.hub.set_mode("dps", True))

    # --- moteur ---
    def _set(self, attr, value):
        setattr(self.cfg, attr, value)
        self.cfg.save()

    def _toggle(self, on: bool):
        if on and not ack_capture(self, self.cfg, self.chk):
            return
        self._set("dps_enabled", on)
        self.hub.set_mode("dps", on)
        self.overlay.apply()

    def shutdown(self):
        self.overlay.hide()

    def _render_state(self, st: dict):
        text, fix = describe_state(st, self.cfg.dps_enabled, "dps")
        self.state.setText(text)
        self.btn_fix.setVisible(bool(fix))
        if fix:
            self.btn_fix.setText(fix)

    def _fix(self):
        fix_capture(self, self.hub, self.log)

    def _mode(self):
        mode = self.mode.currentData()
        self._set("dps_mode", mode)
        self.hub.set_dps_mode(mode)

    # --- données ---
    def _on_dps(self, d: dict):
        self._last_dps = time.monotonic()
        self.overlay.show_data(d)
        self.ping.setText(f"Ping du jeu : {ping_text(d.get('ping_ms'))} (aller-retour mesuré)")
        self.t_ping.set(ping_text(d.get("ping_ms")))
        self.t_time.set(fmt_duration(d.get("battle_ms") or 0), (d.get("target") or {}).get("name") or "")
        self.t_total.set(fmt_short(d.get("total")), f"{len(d.get('rows') or [])} joueur(s)")
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
                    it.setForeground(c, QBrush(QColor(theme.GOLD_TEXT)))
            self.table.addTopLevelItem(it)

    def _stale_check(self):
        if self.hub.client and self.hub.client.running and time.monotonic() - self._last_dps > 10:
            self.ping.setText("Ping du jeu : — (pas de données récentes)")
            self.t_ping.set(None, "pas de données récentes")

    def _on_fight(self, record: dict):
        if self.history.add_fight(record, self.hub.ping_ms):
            self.log(f"Combat enregistré : {record.get('bossName') or '?'}")
            self.refresh_fights()

    # --- historique ---
    def refresh_fights(self):
        self.fights.clear()
        fights = self.history.fights(self.f_search.text().strip())
        self.t_fights.set(str(self.history.fight_count()), "historique local")
        for f in fights:
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
