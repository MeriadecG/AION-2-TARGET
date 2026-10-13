"""Fenêtre « Événements » : calendrier AION 2, cases d'activation, compte à rebours (heure de Paris).

Lecture seule du calendrier ; les préférences sont enregistrées à chaque changement et le
planificateur (notify.EventNotifier) est réarmé aussitôt. Le compte à rebours n'est rafraîchi
que lorsque la fenêtre est visible (1 fois par seconde).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, QUrl
from PyQt6.QtGui import QBrush, QColor, QDesktopServices, QFont, QIcon, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
                             QLineEdit, QPushButton, QSlider, QTreeWidget, QTreeWidgetItem,
                             QVBoxLayout, QWidget)

from . import theme
from .events import CATEGORIES, DAYS, STATUS_LABELS, UTC, Event, next_occurrence

PARIS = ZoneInfo("Europe/Paris")
GOLD = theme.GOLD
SHORT_STATUS = {"confirmed": "confirmé", "to_verify": "à vérifier", "unconfirmed": "—"}
def status_color(status: str) -> str:
    """Couleur du statut dans le thème ACTIF (clair ou sombre)."""
    return {"confirmed": theme.OK, "to_verify": theme.WARN}.get(status, theme.MUTED)
DAY_FR = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
DAY_FR_LONG = {"mon": "lun", "tue": "mar", "wed": "mer", "thu": "jeu", "fri": "ven", "sat": "sam", "sun": "dim"}
REMINDER_CHOICES = [60, 720, 1440]          # 1 h, 12 h, 24 h
ID_ROLE = Qt.ItemDataRole.UserRole
PICK = "__pick__"

# Styles propres à cet onglet (le reste vient du thème commun, theme.py).
def extra_style() -> str:
    return f"""
QTreeWidget::item {{{{ padding: 3px 2px; }}}}
QLabel#next {{{{ color: {theme.GOLD_TEXT}; font-weight: 600; }}}}
""".replace("{{", "{").replace("}}", "}")


EXTRA_STYLE = extra_style()


def bell_icon(color: str = GOLD, size: int = 64) -> QIcon:
    """Cloche dessinée (pas de fichier d'image ni de module SVG nécessaire)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor(color)))
    s = size / 64
    path = QPainterPath()
    path.moveTo(32 * s, 8 * s)
    path.cubicTo(18 * s, 8 * s, 15 * s, 20 * s, 15 * s, 30 * s)
    path.lineTo(15 * s, 40 * s)
    path.lineTo(9 * s, 48 * s)
    path.lineTo(55 * s, 48 * s)
    path.lineTo(49 * s, 40 * s)
    path.lineTo(49 * s, 30 * s)
    path.cubicTo(49 * s, 20 * s, 46 * s, 8 * s, 32 * s, 8 * s)
    p.drawPath(path)
    p.drawEllipse(QPointF(32 * s, 53 * s), 6 * s, 6 * s)
    p.drawRoundedRect(QRectF(29 * s, 3 * s, 6 * s, 7 * s), 2 * s, 2 * s)
    p.end()
    return QIcon(pm)


# --- textes ----------------------------------------------------------------------------------

def fmt_when(at: datetime, now: datetime) -> str:
    """Instant → « aujourd'hui 21:00 », « demain 09:00 », « mer. 14/10 09:00 » (heure de Paris)."""
    loc, today = at.astimezone(PARIS), now.astimezone(PARIS).date()
    hm = loc.strftime("%H:%M")
    if loc.date() == today:
        return f"aujourd'hui {hm}"
    if loc.date() == today + timedelta(days=1):
        return f"demain {hm}"
    return f"{DAY_FR[loc.weekday()]} {loc.strftime('%d/%m')} {hm}"


def fmt_countdown(delta: timedelta) -> str:
    sec = max(0, int(delta.total_seconds()))
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f"{d} j {h} h"
    if h:
        return f"{h} h {m:02d}"
    return f"{m:02d}:{s:02d}"


def rule_text(e: Event) -> str:
    r = e.rule
    where = "heure serveur" if e.tz_origin == "server" else e.tz
    kind = r.get("type", "unknown")
    if kind == "hourly":
        return f"Toutes les heures à :{int(r.get('minute', 0)):02d}"
    if kind == "every_hours":
        return f"Toutes les {r['interval']} h à partir de {r.get('start', '00:00')} ({where} : {e.tz})"
    if kind == "daily":
        return f"Tous les jours à {', '.join(r.get('times', []))} ({where})"
    if kind == "weekly":
        days = ", ".join(DAY_FR_LONG[d] for d in r.get("days", []) if d in DAYS)
        return f"Chaque {days} à {r.get('time', '00:00')} ({where}{'' if where == e.tz else ' : ' + e.tz})"
    if kind == "monthly":
        return f"Le {r.get('day', 1)} du mois à {r.get('time', '00:00')} ({where})"
    return "Horaire à confirmer"


# --- fenêtre ---------------------------------------------------------------------------------

class EventsWindow(QWidget):
    COLS = ("ÉVÉNEMENT", "PROCHAIN (PARIS)", "DANS", "STATUT")

    def __init__(self, notifier, base_stylesheet: str = "", clock=None, embedded: bool = False):
        super().__init__()
        self.notifier = notifier
        self.clock = clock or (lambda: datetime.now(UTC))
        self._next: dict[str, datetime | None] = {}
        self._items: dict[str, QTreeWidgetItem] = {}
        self._cats: dict[str, QTreeWidgetItem] = {}
        self._loading = False

        self.setObjectName("events")
        self.setWindowTitle("Aion Target — Événements")
        self.setWindowIcon(bell_icon())
        self.setStyleSheet(base_stylesheet + extra_style())
        self.resize(700, 680)
        self.setMinimumSize(460, 420)

        root = QVBoxLayout(self)
        root.setContentsMargins(*((0, 4, 0, 0) if embedded else (14, 12, 14, 12)))
        root.setSpacing(8)

        # en-tête
        head = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(bell_icon().pixmap(22, 22))
        title = QLabel("ÉVÉNEMENTS")
        title.setObjectName("title")
        tf = QFont("Cinzel", 14)
        tf.setWeight(QFont.Weight.Bold)
        title.setFont(tf)
        self.count = QLabel()
        self.count.setObjectName("next")
        if not embedded:                  # dans un onglet : le titre est celui de l'onglet
            head.addWidget(icon)
            head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.count)
        root.addLayout(head)

        self.backend = QLabel()
        self.backend.setObjectName("status")
        self.backend.setWordWrap(True)
        root.addWidget(self.backend)
        self.next_label = QLabel()
        self.next_label.setObjectName("next")
        self.next_label.setWordWrap(True)
        root.addWidget(self.next_label)

        # filtres
        filt = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Rechercher…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        self.cat_box = QComboBox()
        self.cat_box.addItem("Toutes les catégories", "")
        for key, label in CATEGORIES.items():
            self.cat_box.addItem(label, key)
        self.cat_box.currentIndexChanged.connect(self._apply_filter)
        self.only_on = QCheckBox("Activés seulement")
        self.only_on.toggled.connect(self._apply_filter)
        filt.addWidget(self.search, 1)
        filt.addWidget(self.cat_box)
        filt.addWidget(self.only_on)
        root.addLayout(filt)

        # liste
        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(self.COLS))
        self.tree.setHeaderLabels(self.COLS)
        self.tree.setAlternatingRowColors(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        hdr = self.tree.header()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2, 3):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemChanged.connect(self._item_changed)
        root.addWidget(self.tree, 1)

        # rappels avant reset
        self.reminder_btns: dict[tuple[str, int], QPushButton] = {}
        for group, label in (("daily", "Rappel avant reset quotidien"), ("weekly", "Rappel avant reset hebdo")):
            row = QHBoxLayout()
            lab = QLabel(label)
            lab.setToolTip("Prévient avant la réinitialisation si le reset ou l'un de ses éléments "
                           "est coché.\nFormulation neutre : l'outil ne lit pas tes compteurs en jeu.")
            row.addWidget(lab)
            row.addStretch(1)
            for minutes in REMINDER_CHOICES:
                b = QPushButton(f"{minutes // 60} h")
                b.setObjectName("chip")
                b.setCheckable(True)
                b.toggled.connect(lambda on, g=group, m=minutes: self._reminder_toggled(g, m, on))
                self.reminder_btns[(group, minutes)] = b
                row.addWidget(b)
            root.addLayout(row)

        snd = QHBoxLayout()
        self.sound = QCheckBox("Son")
        self.sound.toggled.connect(self._sound_toggled)
        self.sound_box = QComboBox()
        self.sound_box.setMinimumWidth(180)
        self.sound_box.activated.connect(self._sound_chosen)
        self.btn_listen = QPushButton("▶ Écouter")
        self.btn_listen.clicked.connect(self._listen)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setFixedWidth(110)
        self.volume_label = QLabel()
        self.volume_label.setObjectName("status")
        self.volume_label.setMinimumWidth(36)
        self.volume.valueChanged.connect(lambda v: self.volume_label.setText(f"{v} %"))
        self.volume.sliderReleased.connect(self._volume_changed)
        self.volume.valueChanged.connect(lambda _v: None if self.volume.isSliderDown()
                                         else self._volume_changed())
        snd.addWidget(self.sound)
        snd.addWidget(self.sound_box, 1)
        snd.addWidget(self.btn_listen)
        snd.addWidget(self.volume)
        snd.addWidget(self.volume_label)
        root.addLayout(snd)

        opts = QHBoxLayout()
        opts.addStretch(1)
        self.btn_folder = QPushButton("Calendrier…")
        self.btn_folder.setToolTip("Ouvre le dossier de configuration. Pour corriger un horaire, "
                                   "crée-y events_catalog.json\n(mêmes champs que le calendrier "
                                   "embarqué, fusionné par identifiant), puis « Recharger ».")
        self.btn_folder.clicked.connect(self._open_folder)
        self.btn_reload = QPushButton("Recharger")
        self.btn_reload.clicked.connect(self._reload)
        opts.addWidget(self.btn_folder)
        opts.addWidget(self.btn_reload)
        root.addLayout(opts)

        btns = QHBoxLayout()
        b_all = QPushButton("Tout activer")
        b_none = QPushButton("Tout désactiver")
        b_test = QPushButton("Tester une notification")
        b_all.clicked.connect(lambda: self._set_all(True))
        b_none.clicked.connect(lambda: self._set_all(False))
        b_test.clicked.connect(self._test)
        self.b_test = b_test
        b_all.setToolTip("Active les entrées affichées (filtre en cours) dont l'horaire est connu.")
        for b in (b_all, b_none, b_test):
            btns.addWidget(b)
        root.addLayout(btns)

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._refresh_times)
        notifier.changed.connect(self._refresh_header)
        notifier.fired.connect(lambda _a: self._refresh_header())
        self.populate()

    # --- construction ---
    def apply_theme(self, base_stylesheet: str):
        """Changement clair / sombre : nouvelle feuille de style et couleurs des lignes."""
        self.setStyleSheet(base_stylesheet + extra_style())
        self.populate()
        self._refresh_header()

    def populate(self):
        self._loading = True
        self.tree.clear()
        self._items.clear()
        self._cats.clear()
        self._next.clear()
        prefs, cat = self.notifier.prefs, self.notifier.catalog
        bold = QFont()
        bold.setBold(True)
        count = theme.mono_font(9)                 # comptes à rebours : monospace, chiffres alignés
        for key, label in CATEGORIES.items():
            top = QTreeWidgetItem([label])
            top.setFont(0, bold)
            top.setForeground(0, QBrush(QColor(theme.AETHER_TEXT)))
            top.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.tree.addTopLevelItem(top)
            top.setFirstColumnSpanned(True)           # après l'ajout : sinon sans effet
            self._cats[key] = top
        order = {"event": 0, "reset": 1, "reset_item": 2}
        for e in sorted(cat.events, key=lambda e: (order.get(e.kind, 3), not e.schedulable)):
            parent = self._cats.get(e.category) or self._cats["daily"]
            name = ("↳ " + e.name) if e.kind == "reset_item" and e.group in ("daily", "weekly") else e.name
            it = QTreeWidgetItem(parent, [name, "", "", SHORT_STATUS.get(e.status, e.status)])
            it.setData(0, ID_ROLE, e.id)
            it.setForeground(3, QBrush(QColor(status_color(e.status))))
            it.setFont(2, count)
            it.setTextAlignment(2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            it.setToolTip(0, self._tooltip(e))
            for c in (1, 2, 3):
                it.setToolTip(c, self._tooltip(e))
            if e.schedulable:
                it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
                            | Qt.ItemFlag.ItemIsSelectable)
                it.setCheckState(0, Qt.CheckState.Checked if prefs.is_enabled(e.id)
                                 else Qt.CheckState.Unchecked)
            else:
                # Pas d'horaire fiable : affiché, mais impossible d'y programmer une alerte.
                it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                it.setText(1, "Horaire à confirmer")
                for c in (0, 1, 2):
                    it.setForeground(c, QBrush(QColor(theme.DISABLED)))
            self._items[e.id] = it
        self.tree.expandAll()
        for (group, minutes), b in self.reminder_btns.items():
            b.blockSignals(True)
            b.setChecked(minutes in prefs.reset_reminders.get(group, []))
            b.blockSignals(False)
        for w in (self.sound, self.volume):
            w.blockSignals(True)
        self.sound.setChecked(prefs.sound)
        self.volume.setValue(prefs.volume)
        self.volume_label.setText(f"{prefs.volume} %")
        for w in (self.sound, self.volume):
            w.blockSignals(False)
        self._fill_sounds()
        self._loading = False
        self._refresh_times()
        self._refresh_header()
        self._apply_filter()

    def _tooltip(self, e: Event) -> str:
        cat = self.notifier.catalog
        lines = [f"<b>{e.name}</b>"]
        if e.description:
            lines.append(e.description)
        lines.append(f"<i>Règle :</i> {rule_text(e)}")
        if e.duration_min:
            lines.append(f"<i>Durée :</i> {e.duration_min} min")
        lines.append(f"<i>Fuseau d'origine :</i> {'serveur (' + e.tz + ')' if e.tz_origin == 'server' else e.tz}"
                     " — affiché en heure de Paris")
        lines.append(f"<i>Statut :</i> {STATUS_LABELS.get(e.status, e.status)}")
        if e.note:
            lines.append(f"<i>Note :</i> {e.note}")
        src = [cat.sources.get(s, {}).get("title", s) for s in e.sources]
        if src:
            lines.append(f"<i>Sources ({cat.verified_on}) :</i> " + ", ".join(src))
        if e.kind == "reset_item" and not e.schedulable:
            lines.append("Rythme de réinitialisation non documenté : pas d'alerte.")
        return "<br>".join(lines)

    # --- rafraîchissements ---
    def _refresh_times(self):
        now = self.clock()
        for eid, it in self._items.items():
            e = self.notifier.catalog.by_id.get(eid)
            if e is None or not e.schedulable:
                continue
            at = self._next.get(eid)
            if at is None or at <= now:
                at = next_occurrence(e, now)
                self._next[eid] = at
                it.setText(1, fmt_when(at, now) if at else "—")
            it.setText(2, fmt_countdown(at - now) if at else "")
        nxt = self.notifier.next
        if nxt is not None and nxt.at > now:
            self.next_label.setText(f"Prochaine alerte : {nxt.title.removeprefix('AION 2 : ')} — "
                                    f"{fmt_when(nxt.at, now)} (dans {fmt_countdown(nxt.at - now)})")
        elif not self.notifier.primary:
            self.next_label.setText("Notifications gérées par une autre instance d'Aion Target.")
        else:
            self.next_label.setText("Aucune alerte programmée : coche des événements ci-dessous.")

    def _refresh_header(self):
        prefs, cat = self.notifier.prefs, self.notifier.catalog
        on = [e for e in cat.events if e.schedulable and prefs.is_enabled(e.id)]
        self.count.setText(f"{len(on)} activé{'s' if len(on) > 1 else ''}")
        for key, top in self._cats.items():
            total = sum(1 for e in cat.events if e.category == key)
            n_on = sum(1 for e in on if e.category == key)
            top.setText(0, f"{CATEGORIES[key]}   ·   {n_on}/{total} activé{'s' if n_on > 1 else ''}")
        svc = self.notifier.service
        ok = svc.active is not None
        dot = theme.OK if ok else theme.ERR
        self.backend.setText(f"<span style='color:{dot}'>●</span> Notifications : {svc.detail}"
                             f"<br>Heures affichées : Europe/Paris (heure d'été / d'hiver automatique) · "
                             f"calendrier vérifié le {cat.verified_on}")
        self._refresh_times()

    def _apply_filter(self, *_):
        text = self.search.text().strip().lower()
        want = self.cat_box.currentData() or ""
        only = self.only_on.isChecked()
        prefs = self.notifier.prefs
        for key, top in self._cats.items():
            visible = 0
            for i in range(top.childCount()):
                it = top.child(i)
                e = self.notifier.catalog.by_id.get(it.data(0, ID_ROLE))
                show = (not want or key == want) \
                    and (not text or text in e.name.lower() or text in e.description.lower()) \
                    and (not only or (e.schedulable and prefs.is_enabled(e.id)))
                it.setHidden(not show)
                visible += show
            top.setHidden(visible == 0)

    # --- actions ---
    def _item_changed(self, it: QTreeWidgetItem, col: int):
        if self._loading or col != 0:
            return
        eid = it.data(0, ID_ROLE)
        if not eid:
            return
        on = it.checkState(0) == Qt.CheckState.Checked
        if self.notifier.prefs.is_enabled(eid) == on:
            return
        self.notifier.prefs.set_enabled(eid, on)
        self._save(f"{'activé' if on else 'désactivé'} : {eid}")

    def _set_all(self, on: bool):
        self._loading = True
        for eid, it in self._items.items():
            e = self.notifier.catalog.by_id[eid]
            if not e.schedulable or (on and it.isHidden()):
                continue
            self.notifier.prefs.set_enabled(eid, on)
            it.setCheckState(0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self._loading = False
        self._save("tout activé" if on else "tout désactivé")
        self._apply_filter()

    def _reminder_toggled(self, group: str, minutes: int, on: bool):
        lst = set(self.notifier.prefs.reset_reminders.get(group, []))
        (lst.add if on else lst.discard)(minutes)
        self.notifier.prefs.reset_reminders[group] = sorted(lst)
        self._save(f"rappels {group} : {sorted(lst)}")

    def _fill_sounds(self):
        from . import sounds
        prefs = self.notifier.prefs
        box = self.sound_box
        box.blockSignals(True)
        box.clear()
        items = sounds.choices()
        if prefs.sound_id not in {sid for sid, _ in items}:
            items.append((prefs.sound_id, sounds.label(prefs.sound_id)))
        for sid, lab in items:
            box.addItem(lab, sid)
        box.insertSeparator(box.count())
        box.addItem("Choisir un fichier…", PICK)
        box.setCurrentIndex(max(0, box.findData(prefs.sound_id)))
        box.blockSignals(False)

    def _sound_chosen(self, index: int):
        from . import sounds
        sid = self.sound_box.itemData(index)
        if sid == PICK:
            filt = sounds.CUSTOM_EXT_WIN if sys.platform == "win32" else sounds.CUSTOM_EXT_LINUX
            path, _ = QFileDialog.getOpenFileName(self, "Son des notifications", str(Path.home()), filt)
            if not path:
                self._fill_sounds()
                return
            sid = f"file:{path}"
        if sid == self.notifier.prefs.sound_id:
            return
        self.notifier.prefs.sound_id = sid
        self._fill_sounds()
        self._save(f"son : {sounds.label(sid)}")
        self._listen()

    def _volume_changed(self):
        v = self.volume.value()
        if v == self.notifier.prefs.volume:
            return
        self.notifier.prefs.volume = v
        self._save(f"volume : {v} %")
        self._listen()

    def _listen(self):
        ok, detail = self.notifier.play_sound(force=True)
        if not ok:
            self.next_label.setText(f"Son non joué : {detail}")

    def _sound_toggled(self, on: bool):
        self.notifier.prefs.sound = on
        self._save(f"son {'activé' if on else 'désactivé'}")
        if on:
            self._listen()

    def _save(self, what: str):
        try:
            self.notifier.prefs.save()
        except OSError as e:
            self.notifier.log(f"Événements : préférences NON enregistrées ({e})")
        self.notifier.log(f"Événements : {what}")
        self.notifier.reschedule()
        self._refresh_header()

    def _test(self):
        self.backend.setText("Envoi d'une notification de test…")
        self.b_test.setEnabled(False)                 # anti double-clic (KDE bloque les rafales)
        QTimer.singleShot(2500, lambda: self.b_test.setEnabled(True))

        def done(ok, err):
            self._refresh_header()
            if not ok:
                self.backend.setText(self.backend.text()
                                     + f"<br><span style='color:{theme.ERR}'>Échec du test : {err}</span>")

        self.notifier.test(done)

    def _open_folder(self):
        path = self.notifier.override_path
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    def _reload(self):
        msg = self.notifier.reload_catalog()
        self.populate()
        self.next_label.setText(msg)

    # --- visibilité : le compte à rebours ne tourne que fenêtre ouverte ---
    def showEvent(self, ev):
        self._refresh_header()
        self.timer.start()
        super().showEvent(ev)

    def hideEvent(self, ev):
        self.timer.stop()
        super().hideEvent(ev)

    def show_and_raise(self):
        self.show()
        self.raise_()
        self.activateWindow()
