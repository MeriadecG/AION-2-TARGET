"""Fenêtre de contrôle : onglets Target (GS/CP), Notifications, Familiers, Farm ; pause, quitter."""
from __future__ import annotations

from PyQt6.QtCore import QObject, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QFont
from PyQt6.QtWidgets import (QSizePolicy, QCheckBox, QColorDialog, QComboBox, QFontComboBox, QFrame, QGridLayout,
                             QHBoxLayout, QLabel, QLineEdit, QProgressBar, QPushButton, QScrollArea,
                             QSystemTrayIcon, QTabWidget, QVBoxLayout, QWidget)

from . import theme
from .style import fmt
from .ui_kit import (ActionButton, EmptyState, NavButton, PageHeader, SectionCard, StatTile, StatusChip,
                     fade_in, tile_row)

# Navigation : ordre de la barre latérale → index de la page (l'ordre interne des pages ne change
# pas : préférences, raccourcis et code existant restent valables).
NAV = [("target", "Target", "target", 0), ("dps", "DPS", "dps", 4), ("events", "Notifications", "bell", 1),
       ("pets", "Familiers", "pets", 2), ("farm", "Farm", "farm", 3)]
PAGES = {
    0: ("Target", "Score de la cible (GS / CP), lecture à l'écran, mémoire des joueurs et préchargement."),
    1: ("Notifications", "Calendrier des événements, comptes à rebours et alertes."),
    2: ("Familiers", "Collection, progression des âmes et monstres tués."),
    3: ("Farm", "Monnaie suivie, Kinah / minute et session de farm."),
    4: ("DPS", "Dégâts en direct, ping et historiques (capture réseau passive)."),
}
COMPACT_BELOW = 760          # largeur de fenêtre sous laquelle la barre latérale n'a que des icônes
NAV_STATUS = {"farm": 3, "pets": 2, "dps": 4}   # états affichés en pastille dans la barre latérale

GOLD = theme.GOLD
CACHE_SIZES_MB = [25, 100, 500, 1024, 5120]
IMPORT_CHOICES = [("Import shugo.gg : toute la région", -1), ("Import shugo.gg : top 5 000", 5000),
                  ("Import shugo.gg : top 1 000", 1000), ("Import shugo.gg : désactivé", 0)]
BYTES_PER_PLAYER = 150     # mesuré


def players_label(mb: float) -> str:
    n = mb * 1024 * 1024 / BYTES_PER_PLAYER
    return f"{n / 1e6:.0f} M" if n >= 1e6 else f"{n / 1e3:.0f} k"


def size_label(mb: float) -> str:
    if mb >= 1024:
        return f"{mb / 1024:.0f} Go" if mb % 1024 == 0 else f"{mb / 1024:.1f} Go".replace(".", ",")
    return f"{mb:.0f} Mo" if mb >= 10 or mb == int(mb) else f"{mb:.1f} Mo".replace(".", ",")

# Thème « Tactical Analytics » (fenêtre de contrôle seulement) : voir theme.py.
STYLESHEET = theme.STYLESHEET


class _UpdateSignal(QObject):
    done = pyqtSignal(str, str)       # (statut, message) de la vérification de mise à jour


class _ReadTestSignal(QObject):
    done = pyqtSignal(str)
    color = pyqtSignal(object)        # carré du test de latence : True / False / None (masqué)
    latency_done = pyqtSignal(str)


class _Probe(QWidget):
    """Petit carré noir / blanc affiché par-dessus tout, pour mesurer le retard de capture."""

    def __init__(self):
        super().__init__()
        W = Qt.WindowType
        self.setWindowFlags(W.ToolTip | W.FramelessWindowHint | W.WindowStaysOnTopHint
                            | W.X11BypassWindowManagerHint | W.WindowTransparentForInput)
        self.white = False

    def paintEvent(self, _):
        from PyQt6.QtGui import QPainter
        QPainter(self).fillRect(self.rect(), QColor(255, 255, 255) if self.white else QColor(0, 0, 0))


def describe_read_test(name: str, tag: str | None, brightness: float, image_path: str,
                       raw: str = "", mode: str = "") -> str:
    """Message clair pour « Tester la lecture » (logique pure, testée)."""
    detail = f"\nTexte brut : « {raw} » ({mode})" if raw or mode else ""
    if brightness < 2:
        return ("✗ Capture NOIRE : l'écran n'est pas lisible. Sous Linux, accepte le partage "
                "d'écran (écran du jeu) ; sous Windows, mets le jeu en plein écran fenêtré.")
    if not name:
        return (f"✗ Aucun nom lu. Vérifie que le cadre BLEU entoure la ligne du nom "
                f"(« Placer sur une capture ») et qu'une cible joueur est sélectionnée. "
                f"Image : {image_path}{detail}")
    srv = f" · serveur [{tag}]" if tag else ""
    return f"✓ Lu : « {name} »{srv} — la lecture fonctionne. Image : {image_path}{detail}"


class ControlWindow(QWidget):
    def __init__(self, cfg, overlay, worker, grabber, quit_app):
        super().__init__()
        self.cfg, self.overlay, self.worker = cfg, overlay, worker
        self.grabber = grabber
        self.quit_app = quit_app
        self._picker = None
        self._countdown = 0

        self.setObjectName("root")
        self.setWindowTitle("Aion Target")
        theme.set_mode(cfg.ui_theme)
        self.setStyleSheet(theme.full_stylesheet())
        self.setFont(theme.ui_font(9.5))
        self.setMinimumWidth(460)

        # En-tête, onglets et boutons du bas TOUJOURS visibles ; le contenu des onglets défile.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(8)
        body = QWidget()
        body.setObjectName("body")
        root = QVBoxLayout(body)
        root.setContentsMargins(0, 0, 6, 0)
        root.setSpacing(10)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(body)

        # --- en-tête : marque, état de l'application et du moteur, version ---
        from . import __version__
        header = QFrame()
        header.setObjectName("header")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(14, 10, 14, 10)
        hl.setSpacing(12)
        bar = QFrame()
        bar.setObjectName("accentBar")
        bar.setFixedWidth(3)
        hl.addWidget(bar)
        brand = QVBoxLayout()
        brand.setSpacing(0)
        title = QLabel("AION TARGET")
        title.setObjectName("title")
        tf = QFont(theme.BRAND_FONT, 15)
        tf.setWeight(QFont.Weight.Bold)
        title.setFont(tf)
        sub = QLabel("AETHER ANALYTICS")
        sub.setObjectName("brandSub")
        brand.addWidget(title)
        brand.addWidget(sub)
        hl.addLayout(brand)
        hl.addSpacing(8)
        self._status_slot = QVBoxLayout()           # état de l'application (cible, pause, réglage)
        hl.addLayout(self._status_slot, 1)
        self._chips: dict[str, tuple[StatusChip, object]] = {}
        self._nav_status: dict[str, tuple[int, object]] = {}
        self._chip_box = QHBoxLayout()
        self._chip_box.setSpacing(6)
        hl.addLayout(self._chip_box)
        self.ver_label = QLabel(f"v{__version__}")
        self.ver_label.setObjectName("version")
        self.ver_label.setFont(theme.mono_font(8, bold=False))
        hl.addWidget(self.ver_label)
        self.btn_theme = QPushButton()
        self.btn_theme.setObjectName("chip")
        self.btn_theme.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_theme.clicked.connect(self._toggle_theme)
        hl.addWidget(self.btn_theme)
        self.status = QLabel("Démarrage…")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        self.btn_sheet = QPushButton("Ouvrir la fiche de la cible")
        self.btn_sheet.setEnabled(False)
        self.btn_sheet.clicked.connect(self._open_sheet)
        self.status.setWordWrap(False)
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._status_slot.addWidget(self.status)
        outer.addWidget(header)
        # barre latérale (navigation) + contenu
        mid = QHBoxLayout()
        mid.setSpacing(12)
        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        sl = QVBoxLayout(self.sidebar)
        sl.setContentsMargins(8, 10, 8, 10)
        sl.setSpacing(3)
        self.nav_title = QLabel("MODULES")
        self.nav_title.setObjectName("navSection")
        sl.addWidget(self.nav_title)
        self.nav_buttons: dict[int, NavButton] = {}
        for key, label, icon, index in NAV:
            btn = NavButton(key, label, icon)
            btn.clicked.connect(lambda _=False, i=index: self.tabs.setCurrentIndex(i))
            self.nav_buttons[index] = btn
            sl.addWidget(btn)
        sl.addStretch(1)
        self._side = sl
        mid.addWidget(self.sidebar)
        content = QVBoxLayout()
        content.setSpacing(8)
        mid.addLayout(content, 1)
        outer.addLayout(mid, 1)

        # --- onglets : Target (GS/CP) · Notifications · Familiers · Farm ---
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setDrawBase(False)
        self.tabs.tabBar().hide()                 # navigation : barre latérale
        target = QWidget()
        target.setObjectName("body")
        tl = QVBoxLayout(target)
        tl.setContentsMargins(0, 8, 0, 0)
        tl.setSpacing(8)
        self.t_target = StatTile("Cible")
        self.t_gs = StatTile("GS", important=True)
        self.t_cp = StatTile("CP", important=True)
        self.t_speed = StatTile("Affichage p50", tooltip="Temps médian réel entre l'apparition du nom "
                                "et les cadres peints (joueurs en mémoire), sur cette session.")
        tl.addWidget(tile_row(self.t_target, self.t_gs, self.t_cp, self.t_speed))
        tl.addWidget(self.btn_sheet)
        tl.addWidget(scroll, 1)
        self.tabs.addTab(self._page(0, target), "Target")
        self._notifier = None
        self._events_tab = self._placeholder("Module Événements en cours de démarrage…")
        self.tabs.addTab(self._page(1, self._events_tab), "Notifications")
        self._pets_tab = self._placeholder("Familiers : chargement…")
        self.tabs.addTab(self._page(2, self._pets_tab), "Familiers")
        self._farm_tab = self._placeholder("Farm : chargement…")
        self.tabs.addTab(self._page(3, self._farm_tab), "Farm")
        self._dps_tab = self._placeholder("DPS : chargement…")
        self.tabs.addTab(self._page(4, self._dps_tab), "DPS")
        content.addWidget(self.tabs, 1)

        # --- zone ---
        zone = self._card(root, "CADRES (LECTURE · GS · CP · DÉTAILS · TOP)")
        self.btn_pick = QPushButton("Placer sur une capture (3 s)")
        self.btn_pick.setObjectName("primary")
        self.btn_move = QPushButton("Ajuster en direct")
        self.btn_move.setCheckable(True)
        self.btn_move.toggled.connect(self._toggle_move)
        self.btn_pick.clicked.connect(self._start_pick)
        zone.addWidget(self.btn_pick, 0, 0, 1, 2)
        zone.addWidget(self.btn_move, 1, 0, 1, 2)
        hint = QLabel("La taille du texte suit la hauteur de chaque cadre.")
        hint.setObjectName("status")
        hint.setWordWrap(True)
        zone.addWidget(hint, 2, 0, 1, 2)

        # --- mon personnage (pastille plus fort / plus faible) ---
        me = self._card(root, "MON PERSONNAGE")
        self.me_edit = QLineEdit(cfg.my_character)
        self.me_edit.setPlaceholderText("Nom de ton perso")
        self.me_edit.editingFinished.connect(self._me_changed)
        self.me_cp = QLabel("CP : —")
        me.addWidget(self.me_edit, 0, 0)
        me.addWidget(self.me_cp, 0, 1)
        dots = QLabel("<span style='color:#E5484D'>●</span> plus fort que toi · "
                      "<span style='color:#3DD68C'>●</span> plus faible")
        dots.setObjectName("status")
        me.addWidget(dots, 1, 0, 1, 2)

        # --- mémoire des joueurs (taille seulement, pas de durée) ---
        mem = self._card(root, "MÉMOIRE DES JOUEURS")
        self.size_box = QComboBox()
        for mb in CACHE_SIZES_MB:
            self.size_box.addItem(f"Max {size_label(mb)}  ·  ~{players_label(mb)} joueurs", mb)
        idx = self.size_box.findData(cfg.cache_max_mb)
        if idx < 0:
            self.size_box.addItem(size_label(cfg.cache_max_mb), cfg.cache_max_mb)
            idx = self.size_box.count() - 1
        self.size_box.setCurrentIndex(idx)
        self.size_box.currentIndexChanged.connect(self._cache_size_changed)
        self.mem_stats = QLabel("…")
        self.mem_stats.setObjectName("status")
        self.import_box = QComboBox()
        for label, value in IMPORT_CHOICES:
            self.import_box.addItem(label, value)
        i = self.import_box.findData(cfg.import_max_players)
        self.import_box.setCurrentIndex(i if i >= 0 else 0)
        self.import_box.currentIndexChanged.connect(self._import_changed)
        self.import_stats = QLabel("")
        self.import_stats.setObjectName("status")
        self.import_stats.setWordWrap(True)
        btn_clear = QPushButton("Vider")
        btn_clear.setObjectName("danger")
        btn_clear.clicked.connect(self._clear_cache)
        self.size_box.setToolTip("Taille maximale de la mémoire (pas de limite de durée)")
        mem.addWidget(self.size_box, 0, 0, 1, 2)
        mem.addWidget(self.mem_stats, 1, 0)
        mem.addWidget(btn_clear, 1, 1, Qt.AlignmentFlag.AlignRight)
        self.import_bar = QProgressBar()
        self.import_bar.setVisible(False)
        self.btn_import = QPushButton("Télécharger maintenant")
        self.btn_import.clicked.connect(self._import_now)
        mem.addWidget(self.btn_import, 5, 0, 1, 2)
        mem.addWidget(self.import_box, 2, 0, 1, 2)
        mem.addWidget(self.import_bar, 3, 0, 1, 2)
        mem.addWidget(self.import_stats, 4, 0, 1, 2)
        worker.import_progress.connect(self._on_import)
        self._show_import_state()
        self._mem_timer = QTimer(self)
        self._mem_timer.timeout.connect(self._refresh_mem)
        self._mem_timer.start(5000)
        QTimer.singleShot(1500, self._refresh_mem)

        # --- apparence ---
        # Détection et préchargement des joueurs (observation réseau légère) : section ajoutée
        # par set_observation() quand le moteur partagé est prêt.
        self._observe_box = QVBoxLayout()
        self._observe_box.setSpacing(10)
        root.addLayout(self._observe_box)

        look = self._card(root, "APPARENCE", collapsed=True,
                          desc="Couleurs et police des cadres en jeu, thème de cette fenêtre.")
        look.addWidget(QLabel("Thème de la fenêtre"), 8, 0)
        self.theme_box = QComboBox()
        for key, label in (("light", "Clair"), ("dark", "Sombre"), ("system", "Comme le système")):
            self.theme_box.addItem(label, key)
        self.theme_box.setCurrentIndex(max(0, self.theme_box.findData(cfg.ui_theme)))
        self.theme_box.currentIndexChanged.connect(lambda _: self.set_theme(self.theme_box.currentData()))
        self.theme_box.setToolTip("Fenêtre de contrôle seulement : les overlays du jeu ne changent pas.")
        look.addWidget(self.theme_box, 8, 1)
        self.sw_gs = self._swatch(look, 0, "Couleur GS", "gs_color")
        self.sw_cp = self._swatch(look, 1, "Couleur CP", "cp_color")
        self.sw_det = self._swatch(look, 2, "Couleur détails", "detail_color")
        self.sw_rank = self._swatch(look, 6, "Couleur TOP", "rank_color")
        self.chk_top = QCheckBox("Afficher le top (TOP - 23, jusqu'au top 1 000)")
        self.chk_top.setChecked(cfg.show_top)
        self.chk_top.toggled.connect(self._top_changed)
        look.addWidget(self.chk_top, 7, 0, 1, 2)

        look.addWidget(QLabel("Police"), 3, 0)
        self.font_box = QFontComboBox()
        self.font_box.setCurrentFont(QFont(cfg.font_family))
        self.font_box.currentFontChanged.connect(self._font_changed)
        look.addWidget(self.font_box, 3, 1)

        self.chk_details = QCheckBox("Afficher classe · niveau · serveur")
        self.chk_details.setChecked(cfg.show_details)
        self.chk_details.toggled.connect(self._details_changed)
        look.addWidget(self.chk_details, 5, 0, 1, 2)

        # --- bas ---
        # --- diagnostic ---
        diag = self._card(root, "DIAGNOSTIC", collapsed=True,
                          desc="Tests d'affichage et de lecture, retard de capture, rapports à envoyer.")
        btn_show = QPushButton("Test affichage")
        btn_show.clicked.connect(self._test_display)
        self.btn_read = QPushButton("Test lecture")
        self.btn_read.clicked.connect(self._start_read_test)
        self.diag_text = QLabel("Test affichage : exemple 5 s dans tes cadres. Test lecture : 3 s pour cibler un joueur, puis résultat.")
        self.diag_text.setObjectName("status")
        self.diag_text.setWordWrap(True)
        self.diag_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.btn_report = QPushButton("Copier le rapport de vitesse")
        self.btn_report.clicked.connect(self._copy_report)
        self.btn_diag = QPushButton("Exporter le rapport de diagnostic…")
        self.btn_diag.setToolTip("Fichier texte à lire après la session (vitesse, moteur, préchargement, "
                                 "Farm, familiers). Sans capture réseau ni nom de joueur.")
        self.btn_diag.clicked.connect(self._export_diag)
        self.btn_latency = QPushButton("Mesurer le retard de capture")
        self.btn_latency.clicked.connect(self._start_latency_test)
        diag.addWidget(btn_show, 0, 0)
        diag.addWidget(self.btn_read, 0, 1)
        diag.addWidget(self.btn_latency, 1, 0, 1, 2)
        diag.addWidget(self.btn_report, 2, 0, 1, 2)
        diag.addWidget(self.btn_diag, 3, 0, 1, 2)
        diag.addWidget(self.diag_text, 4, 0, 1, 2)
        self._read_sig = _ReadTestSignal()
        self._read_sig.done.connect(self._read_test_done)
        self._read_sig.color.connect(self._probe_color)
        self._read_sig.latency_done.connect(self._latency_done)
        self._probe = None
        self._latency_running = False
        self._read_countdown = 0

        app_card = self._card(root, "APPLICATION", collapsed=True,
                              desc="Raccourci et mises à jour automatiques.")
        self.btn_shortcut = QPushButton("Créer un raccourci (menu + bureau)")
        self.btn_shortcut.clicked.connect(self._make_shortcut)
        app_card.addWidget(self.btn_shortcut, 0, 0)
        self.chk_update = QCheckBox("Mise à jour automatique au lancement (GitHub)")
        self.chk_update.setChecked(cfg.auto_update)
        self.chk_update.toggled.connect(self._auto_update_changed)
        app_card.addWidget(self.chk_update, 1, 0)
        self.btn_check_update = QPushButton("Rechercher une mise à jour maintenant")
        self.btn_check_update.clicked.connect(lambda: self._check_update(manual=True))
        app_card.addWidget(self.btn_check_update, 2, 0)
        import os as _os
        self.update_label = QLabel(_os.environ.get("AION_TARGET_UPDATE_MSG", "") or
                                   "Mise à jour : pas encore vérifiée.")
        self.update_label.setObjectName("status")
        self.update_label.setWordWrap(True)
        self.update_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        app_card.addWidget(self.update_label, 3, 0)
        self.btn_restart = ActionButton("Redémarrer pour utiliser la nouvelle version", "primary")
        self.btn_restart.setVisible(False)
        self.btn_restart.clicked.connect(self._restart)
        app_card.addWidget(self.btn_restart, 4, 0)

        bottom = QHBoxLayout()
        self.btn_pause = ActionButton("Pause")
        self.btn_pause.setCheckable(True)
        self.btn_pause.toggled.connect(self._update_pause)
        btn_quit = ActionButton("Quitter", "danger")
        btn_quit.setToolTip("Ferme Aion Target (overlays et modules compris).")
        btn_quit.setObjectName("danger")
        btn_quit.clicked.connect(self.quit_app)
        # Pause / Quitter : en bas de la barre latérale (toujours visibles, hors du défilement).
        self.btn_quit = btn_quit
        self._side.addWidget(self.btn_pause)
        self._side.addWidget(btn_quit)
        root.addStretch(1)
        import os
        self.version = self.ver_label             # version réelle, dans l'en-tête
        self.version.setToolTip(os.environ.get("AION_TARGET_UPDATE_MSG", "") or f"Version {__version__}")
        self._fit_to_screen(body)

        self.tabs.setCurrentIndex(min(max(0, cfg.control_tab), self.tabs.count() - 1))
        self.tabs.currentChanged.connect(self._tab_changed)
        self._tab_changed(self.tabs.currentIndex(), save=False)

        worker.status.connect(self._on_status)
        worker.timing.connect(self._on_timing)
        worker.my_cp.connect(self._on_my_cp)
        overlay.edit_changed.connect(self._on_edit_changed)
        self._update_pause()

        self._theme_label()
        try:                                    # « Comme le système » : suit le changement en direct
            from PyQt6.QtWidgets import QApplication
            QApplication.instance().styleHints().colorSchemeChanged.connect(
                lambda *_: self.cfg.ui_theme == "system" and self.set_theme("system"))
        except Exception:
            pass

        self._update_sig = _UpdateSignal()
        self._update_sig.done.connect(self._update_done)
        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(self._check_update)
        self._update_timer.start(self.UPDATE_EVERY_MS)
        if os.environ.get("AION_TARGET_UPDATE_STATUS") in ("skipped", "offline", "error"):
            QTimer.singleShot(90_000, self._check_update)     # le démarrage n'a pas pu mettre à jour

        # Puces d'état des modules (rafraîchies toutes les 1,5 s : aucune charge).
        self.register_status("read", "Lecture", self._read_state)
        self._chip_timer = QTimer(self)
        self._chip_timer.timeout.connect(self._refresh_chips)
        self._chip_timer.start(1500)
        self._refresh_chips()

    # --- construction ---
    def _fit_to_screen(self, body: QWidget):
        """Taille de départ : tout le contenu si possible, jamais plus haut que l'écran."""
        from PyQt6.QtWidgets import QApplication
        screen = self.screen() or QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else None
        want = body.sizeHint().height() + 170          # + en-tête et boutons du bas
        height = min(want, avail.height() - 60) if avail else want
        self.resize(900, max(560, height))

    # --- onglets ---
    TAB_WIDTH: dict = {}                       # la barre latérale s'adapte : plus d'élargissement forcé                 # largeur confortable par onglet

    def _placeholder(self, text: str) -> QWidget:
        w = QWidget()
        w.setObjectName("body")
        lay = QVBoxLayout(w)
        lab = QLabel(text)
        lab.setObjectName("status")
        lab.setWordWrap(True)
        lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addStretch(1)
        lay.addWidget(lab)
        lay.addStretch(2)
        return w

    def _page(self, index: int, widget: QWidget) -> QWidget:
        """Page d'un module : titre + description, puis le contenu du module."""
        page = QWidget()
        page.setObjectName("page")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(10)
        title, desc = PAGES[index]
        lay.addWidget(PageHeader(title, desc))
        lay.addWidget(widget, 1)
        return page

    def _replace_tab(self, index: int, old: QWidget, new: QWidget, label: str):
        current = self.tabs.currentIndex()
        self.tabs.blockSignals(True)
        wrapper = self.tabs.widget(index)
        self.tabs.removeTab(index)
        if wrapper is not None and wrapper is not old:
            wrapper.deleteLater()
        self.tabs.insertTab(index, self._page(index, new), label)
        self.tabs.setCurrentIndex(current)
        self.tabs.blockSignals(False)
        old.deleteLater()

    def _tab_changed(self, index: int, save: bool = True):
        for i, btn in self.nav_buttons.items():
            btn.setChecked(i == index)
            btn.refresh()
        page = self.tabs.widget(index)
        head = page.findChild(PageHeader) if page is not None else None
        if save and head is not None:
            fade_in(head)          # transition discrète (150 ms) sur le titre seulement : jamais
                                   # d'effet graphique sur les tableaux (coût et artefacts)
        if save:
            self.cfg.control_tab = index
            self.cfg.save()

    def _auto_update_changed(self, on: bool):
        self.cfg.auto_update = on
        self.cfg.save()

    # --- mise à jour pendant que l'appli tourne ---
    UPDATE_EVERY_MS = 60 * 60 * 1000             # vérification discrète toutes les heures

    def _check_update(self, manual: bool = False):
        """Vérifie GitHub et INSTALLE la nouvelle version sur le disque (thread à part). Le code en
        mémoire continue de tourner ; elle sert au prochain démarrage (bouton « Redémarrer »).
        Ne dépend pas d'un redémarrage réussi ni du verrou « une seule instance »."""
        import threading
        if getattr(self, "_updating", False) or (not manual and not self.cfg.auto_update):
            return
        self._updating = True
        self.btn_check_update.setEnabled(False)
        self.update_label.setText("Mise à jour : vérification…")

        def work():
            try:
                from . import updater
                res = updater.check_and_update()
                text, status = res.message, res.status
            except Exception as e:                # jamais d'erreur bloquante
                text, status = f"Mise à jour : erreur ({e})", "error"
            self._update_sig.done.emit(status, text)
        threading.Thread(target=work, name="update-check", daemon=True).start()

    def _update_done(self, status: str, text: str):
        self._updating = False
        self.btn_check_update.setEnabled(True)
        self.update_label.setText(text)
        if status == "updated":
            self.btn_restart.setVisible(True)
            self.version.setText(self.version.text().split(" ")[0] + " → redémarrer")
            self.version.setToolTip(text)
            self.status.setText("● " + text + " Redémarre Aion Target pour l'utiliser.")

    def _restart(self):
        """Ferme puis relance l'appli ; la nouvelle instance attend la fermeture de celle-ci."""
        import os
        import subprocess
        import sys
        env = dict(os.environ, AION_TARGET_WAIT_PID=str(os.getpid()))
        env.pop("AION_TARGET_UPDATE_MSG", None)
        exe = os.environ.get("AION_TARGET_EXE")
        try:
            if exe and os.path.exists(exe):                       # Windows : « Aion Target.exe »
                subprocess.Popen([exe], env=env, close_fds=True,
                                 creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
            else:
                from .updater import PROJECT_DIR
                subprocess.Popen([sys.executable, "-m", "aion_target"], cwd=str(PROJECT_DIR), env=env,
                                 start_new_session=True)
        except Exception as e:
            self.update_label.setText(f"Relance impossible ({e}) : ferme et rouvre Aion Target.")
            return
        self.quit_app()

    # --- module Événements (onglet Notifications) ---
    def set_events(self, notifier):
        from .events_ui import EventsWindow, bell_icon
        self._notifier = notifier
        win = EventsWindow(notifier, theme.full_stylesheet(), embedded=True)
        self._replace_tab(1, self._events_tab, win, "Notifications")
        self._events_tab = win
        self.tabs.setTabIcon(1, bell_icon())
        notifier.changed.connect(self._events_count)
        self._events_count()

    def _events_count(self):
        n = self._notifier
        k = sum(1 for e in n.catalog.events if e.schedulable and n.prefs.is_enabled(e.id))
        self.tabs.setTabText(1, f"Notifications ({k})" if k else "Notifications")

    # --- thème clair / sombre ---
    def _theme_label(self):
        dark = theme.MODE == "dark"
        self.btn_theme.setText("◐ Clair" if dark else "◐ Sombre")
        self.btn_theme.setToolTip("Passer en mode clair" if dark else "Passer en mode sombre")

    def _toggle_theme(self):
        self.set_theme("light" if theme.MODE == "dark" else "dark")

    def set_theme(self, choice: str):
        """Applique le thème à TOUTE la fenêtre de contrôle (jamais aux overlays) et l'enregistre."""
        self.cfg.ui_theme = choice
        self.cfg.save()
        theme.set_mode(choice)
        self.setStyleSheet(theme.full_stylesheet())
        if self.theme_box.currentData() != choice:
            self.theme_box.blockSignals(True)
            self.theme_box.setCurrentIndex(max(0, self.theme_box.findData(choice)))
            self.theme_box.blockSignals(False)
        for btn, attr in ((self.sw_gs, "gs_color"), (self.sw_cp, "cp_color"), (self.sw_det, "detail_color"),
                          (self.sw_rank, "rank_color")):
            self._paint_swatch(btn, getattr(self.cfg, attr))
        apply = getattr(self._events_tab, "apply_theme", None)
        if apply:
            apply(theme.full_stylesheet())
        for hook in list(getattr(self, "_theme_hooks", [])):
            try:
                hook()
            except Exception:
                pass
        self._theme_label()
        for btn in self.nav_buttons.values():
            btn.refresh()
        self._refresh_chips()

    def on_theme_change(self, fn):
        """Les onglets qui colorent eux-mêmes des éléments se redessinent au changement de thème."""
        if not hasattr(self, "_theme_hooks"):
            self._theme_hooks = []
        self._theme_hooks.append(fn)

    # --- puces d'état ---
    def register_status(self, key: str, label: str, fn):
        """fn() → (niveau « ok » / « warn » / « err » / « idle », texte court[, infobulle])."""
        if key in NAV_STATUS:                      # état d'un module : pastille dans la navigation
            self._nav_status[key] = (NAV_STATUS[key], fn)
            self._refresh_chips()
            return
        chip = StatusChip(label)
        self._chips[key] = (chip, fn)
        self._chip_box.addWidget(chip)
        self._refresh_chips()

    def _refresh_chips(self):
        for chip, fn in self._chips.values():
            try:
                level, text, *tip = fn()
            except Exception:
                level, text, tip = "idle", "—", []
            chip.set_state(level, text, tip[0] if tip else "")
        for index, fn in self._nav_status.values():
            try:
                level, text, *tip = fn()
            except Exception:
                level, text, tip = "idle", "—", []
            btn = self.nav_buttons.get(index)
            if btn:
                btn.set_dot(level)
                btn.setToolTip(f"{btn.label_text} : {text}")
        self._sync_nav()
        self._refresh_speed()

    def _read_state(self):
        if self.btn_pause.isChecked():
            return "warn", "en pause"
        if self.overlay.edit_mode or self._picker is not None:
            return "warn", "réglage"
        return "ok", "active"

    def _refresh_speed(self):
        from .metrics import STATS
        s = STATS.summary("paint_first_cache")
        if s:
            self.t_speed.set(f"{s['p50']:.0f} ms", f"p95 {s['p95']:.0f} ms · n={s['n']}")

    def set_observation(self, section):
        """Section « Détection et préchargement des joueurs » de l'onglet Target."""
        self._observe_box.addWidget(section)

    def set_pets(self, tab):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(tab)
        self._replace_tab(2, self._pets_tab, scroll, "Familiers")
        self._pets_tab = scroll
        self.on_theme_change(tab.refresh)
        self.on_theme_change(tab._refresh_journal)

    # --- monnaie (onglet Farm) ---
    def set_dps(self, tab):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(tab)
        self._replace_tab(4, self._dps_tab, scroll, "DPS")
        self._dps_tab = scroll
        self.on_theme_change(tab.refresh_fights)

    def set_currency(self, currency_overlay):
        from .currency_ui import FarmTab
        tab = FarmTab(self.cfg, currency_overlay, self._card)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(tab)
        self._replace_tab(3, self._farm_tab, scroll, "Farm")
        self._farm_tab = scroll
        self.on_theme_change(tab._paint_color)
        self.on_theme_change(tab._render_session)

        def farm_chip():
            st = tab.session.state
            return ({"running": "ok", "paused": "warn", "interrupted": "warn"}.get(st, "idle"),
                    {"running": "en cours", "paused": "pause", "interrupted": "à reprendre",
                     "idle": "inactif"}[st], "Compteur Kinah / minute")
        self.register_status("farm", "Farm", farm_chip)

    def _card(self, root, label: str, collapsed: bool | None = None, desc: str | None = None) -> QGridLayout:
        """Carte de section. collapsed=None : toujours ouverte ; True / False : repliable (paramètres
        avancés), état mémorisé par l'utilisateur."""
        memo = getattr(self.cfg, "ui_collapsed", None)
        if collapsed is not None and isinstance(memo, dict) and label in memo:
            collapsed = bool(memo[label])
        card = SectionCard(label, desc, collapsible=collapsed is not None, collapsed=bool(collapsed))
        if collapsed is not None:
            card.toggled.connect(lambda on, k=label: self._remember_fold(k, not on))
        root.addWidget(card)
        return card.grid

    def _remember_fold(self, key: str, folded: bool):
        memo = dict(getattr(self.cfg, "ui_collapsed", None) or {})
        if memo.get(key) != folded:
            memo[key] = folded
            self.cfg.ui_collapsed = memo
            self.cfg.save()

    def _swatch(self, grid: QGridLayout, row: int, label: str, attr: str) -> QPushButton:
        grid.addWidget(QLabel(label), row, 0)
        btn = QPushButton()
        btn.setObjectName("swatch")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._paint_swatch(btn, getattr(self.cfg, attr))
        btn.clicked.connect(lambda: self._pick_color(btn, attr, label))
        grid.addWidget(btn, row, 1, Qt.AlignmentFlag.AlignLeft)
        return btn

    @staticmethod
    def _paint_swatch(btn: QPushButton, color: str):
        btn.setStyleSheet(f"background:{color}; border:1px solid {theme.BORDER_STRONG};")
        btn.setText(color.upper())
        dark = QColor(color).lightness() > 140
        btn.setStyleSheet(btn.styleSheet() + f"color:{theme.ON_LIGHT if dark else '#FFFFFF'};")

    # --- actions ---
    def _pick_color(self, btn, attr, label):
        color = QColorDialog.getColor(QColor(getattr(self.cfg, attr)), self, label)
        if color.isValid():
            setattr(self.cfg, attr, color.name())
            self._paint_swatch(btn, color.name())
            self._save_and_preview()

    def _font_changed(self, font: QFont):
        self.cfg.font_family = font.family()
        self._save_and_preview()

    def _me_changed(self):
        name = self.me_edit.text().strip()
        if name != self.cfg.my_character:
            self.cfg.my_character = name
            self.cfg.save()
            self.me_cp.setText("CP : …" if name else "CP : —")
            self.overlay.set_my_cp(None)
            self.worker.refresh_me()

    def _on_my_cp(self, cp):
        self.me_cp.setText(f"CP : {fmt(cp)}" if cp else "CP : introuvable")

    def _make_shortcut(self):
        from .launcher import install
        try:
            msg = install()
            self.status.setText("● " + msg.splitlines()[0])
            self.btn_shortcut.setText("Raccourci créé ✓")
        except Exception as e:
            self.status.setText(f"● Raccourci impossible : {e}")

    def _cache(self):
        lookup = getattr(self.worker, "lookup", None)
        return lookup.cache if lookup else None

    def _refresh_mem(self):
        cache = self._cache()
        if cache is not None:
            self.mem_stats.setText(f"{fmt(len(cache))} joueurs · {size_label(cache.size_bytes() / 1048576)}")

    def _import_changed(self, _):
        self.cfg.import_max_players = int(self.import_box.currentData())
        self.cfg.save()
        if self.cfg.import_max_players != 0:
            self.worker.maybe_import(force=True)
        self._show_import_state()

    def _import_now(self):
        if self.cfg.import_max_players == 0:
            self.import_box.setCurrentIndex(0)            # « toute la région »
            return                                        # (le changement lance l'import)
        if getattr(self.worker, "lookup", None) is None:
            self.import_stats.setText("L'outil démarre encore… réessaie dans quelques secondes.")
            return
        self.btn_import.setEnabled(False)
        self.btn_import.setText("Téléchargement…")
        self.worker.maybe_import(force=True)

    def _on_import(self, done: int, total: int, finished: bool):
        if not finished:
            self.btn_import.setEnabled(False)
            self.btn_import.setText("Téléchargement…")
            self.import_bar.setVisible(True)
            self.import_bar.setRange(0, max(total, 1))
            self.import_bar.setValue(done)
            self.import_bar.setFormat(f"Import shugo.gg : {fmt(done)} / {fmt(total)}  (%p %)")
            self.import_stats.setText("Import en cours en arrière-plan, l'outil reste utilisable.")
            return
        self.import_bar.setVisible(False)
        self.btn_import.setEnabled(True)
        self.btn_import.setText("Télécharger maintenant")
        self._refresh_mem()
        self._show_import_state()
        complete = (self.cfg.import_state.get("next_page", 1) <= 1
                    and not self.cfg.import_state.get("last_error"))
        tray = getattr(self, "_tray", None)
        if tray is not None and QSystemTrayIcon.isSystemTrayAvailable():
            if complete:
                tray.showMessage("Aion Target", f"Import terminé : {fmt(done)} joueurs en mémoire.",
                                 QSystemTrayIcon.MessageIcon.Information, 5000)
            else:
                from .importer import explain_error
                tray.showMessage("Aion Target", "Import impossible : "
                                 + explain_error(self.cfg.import_state.get("last_error")),
                                 QSystemTrayIcon.MessageIcon.Warning, 8000)

    def _show_import_state(self):
        import time as _t
        st = self.cfg.import_state
        from .importer import explain_error
        if self.cfg.import_max_players == 0:
            self.import_stats.setText("Import désactivé : seuls les joueurs croisés sont mémorisés.")
        elif st.get("last_error"):
            self.import_stats.setText("✗ " + explain_error(st["last_error"]))
        elif st.get("next_page", 1) > 1:
            self.import_stats.setText("⏸ Import interrompu : reprise automatique au prochain essai.")
        elif st.get("last_full"):
            hhmm = _t.strftime("%H:%M", _t.localtime(st["last_full"]))
            hours = (_t.time() - st["last_full"]) / 3600
            day = "aujourd'hui" if hours < 20 else f"il y a {hours / 24:.0f} j" if hours >= 36 else "hier"
            self.import_stats.setText(f"✓ Import terminé {day} à {hhmm} · "
                                      f"{fmt(st.get('total', 0))} joueurs · prochain dans ≤ 24 h")
        else:
            self.import_stats.setText("Import au lancement (≈ 4 min en arrière-plan).")

    def _cache_size_changed(self, _):
        self.cfg.cache_max_mb = int(self.size_box.currentData())
        self.cfg.save()
        cache = self._cache()
        if cache is not None:
            cache.max_bytes = self.cfg.cache_max_mb * 1024 * 1024
            cache.purge()
        self._refresh_mem()

    def _clear_cache(self):
        cache = self._cache()
        if cache is not None:
            cache.clear()
        self._refresh_mem()

    def _start_latency_test(self):
        """Carré clignotant posé dans le coin de la zone de lecture (donc sur l'écran capturé)."""
        import threading
        from .capture import MssGrabber
        from .latency import PROBE, CaptureLatencyTest
        if self._latency_running:
            return
        self._latency_running = True
        self.btn_latency.setEnabled(False)
        self.btn_latency.setText("Mesure en cours… (≈ 10 s)")
        self.diag_text.setText("Un petit carré clignote dans le coin du cadre BLEU. "
                               "Ne bouge pas la fenêtre du jeu pendant la mesure.")
        self.overlay.set_edit(False)
        self.worker.paused = True                      # la lecture ne doit pas capturer en même temps
        x, y = self.cfg.capture[0], self.cfg.capture[1]
        if self._probe is None:
            self._probe = _Probe()
        self._probe.setGeometry(x, y, PROBE, PROBE)
        local = threading.local()

        def grab(*rect):
            g = self.grabber
            if g is None:                              # mss : une instance par thread
                if not hasattr(local, "g"):
                    local.g = MssGrabber()
                g = local.g
            return g.grab(*rect)

        # Référence gardée : PyQt ne garde pas en vie l'objet d'une méthode programmée par QTimer.
        self._latency_test = CaptureLatencyTest(
            grab, self._read_sig.color.emit, (x + 4, y + 4, PROBE - 8, PROBE - 8),
            lambda text, lat: self._read_sig.latency_done.emit(text))
        QTimer.singleShot(400, lambda: self._latency_test.start())   # fin de la lecture en cours

    def _probe_color(self, white):
        if white is None:
            self._probe.hide()
            return
        self._probe.white = bool(white)
        self._probe.show()
        self._probe.raise_()
        self._probe.update()

    def _latency_done(self, text: str):
        from .overlay import log
        log(text)
        self.diag_text.setText(text + "\n(Ce résultat est aussi dans « Copier le rapport ».)")
        self.btn_latency.setText("Mesurer le retard de capture")
        self.btn_latency.setEnabled(True)
        self._latency_running = False
        self._update_pause()

    def _copy_report(self):
        from PyQt6.QtWidgets import QApplication
        from .overlay import speed_report
        QApplication.clipboard().setText(speed_report())
        self.diag_text.setText("✓ Rapport copié : colle-le simplement dans la conversation (Ctrl+V). "
                               "Idéal juste après avoir ciblé 4-5 joueurs en jeu.")

    def _export_diag(self):
        import time
        from pathlib import Path
        from PyQt6.QtWidgets import QApplication, QFileDialog
        from . import diag
        text = diag.build(include_names=False)
        default = str(Path.home() / f"aion-target-diagnostic-{time.strftime('%Y%m%d-%H%M')}.txt")
        path, _ = QFileDialog.getSaveFileName(self, "Rapport de diagnostic", default, "Texte (*.txt)")
        QApplication.clipboard().setText(text)
        if path:
            Path(path).write_text(text, encoding="utf-8")
            self.diag_text.setText(f"Rapport enregistré : {path} (et copié dans le presse-papiers).")
        else:
            self.diag_text.setText("Rapport copié dans le presse-papiers.")

    def _test_display(self):
        self.overlay.test_display(5)
        self.diag_text.setText("Exemple affiché 5 s dans tes cadres. Si tu ne le vois pas PAR-DESSUS "
                               "le jeu, dis-le (et précise : plein écran ou sans bordure).")

    def _start_read_test(self):
        self._read_countdown = 3
        self.btn_read.setEnabled(False)
        self._read_tick()

    def _read_tick(self):
        if self._read_countdown > 0:
            self.btn_read.setText(f"Cible… {self._read_countdown}")
            self._read_countdown -= 1
            QTimer.singleShot(1000, self._read_tick)
            return
        self.btn_read.setText("Lecture…")
        import threading
        threading.Thread(target=self._read_test_work, daemon=True).start()

    def _read_test_work(self):
        try:
            from PIL import Image
            from .capture import MssGrabber
            from .config import config_path
            from .ocr import NameReader
            grabber = self.grabber or MssGrabber()
            img = grabber.grab(*self.cfg.capture)
            path = config_path().with_name("test-lecture.png")
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.fromarray(img).save(path)
            reader = NameReader(self.cfg.ocr_min_confidence)
            name = reader._ocr(img)
            msg = describe_read_test(name, reader.last_tag, float(img.mean()), str(path),
                                     reader.last_raw, reader.last_mode)
        except Exception as e:
            msg = f"✗ Erreur pendant le test : {e}"
        self._read_sig.done.emit(msg)

    def _read_test_done(self, msg: str):
        self.diag_text.setText(msg)
        self.btn_read.setText("Test lecture")
        self.btn_read.setEnabled(True)

    def _open_sheet(self):
        info = self.overlay.current_info()
        # Seulement une fiche shugo.gg en HTTPS (jamais file:, un autre site ou un programme).
        if info and info.url and info.url.startswith("https://shugo.gg/"):
            QDesktopServices.openUrl(QUrl(info.url))

    def _top_changed(self, on: bool):
        self.cfg.show_top = on
        self._save_and_preview()

    def _details_changed(self, on: bool):
        self.cfg.show_details = on
        self._save_and_preview()

    def _save_and_preview(self):
        self.cfg.save()
        self.overlay.restyle(preview=True)

    def _toggle_move(self, on: bool):
        self.overlay.set_edit(on)

    def _on_edit_changed(self, on: bool):
        self.btn_move.blockSignals(True)
        self.btn_move.setChecked(on)
        self.btn_move.setText("Valider les cadres" if on else "Ajuster en direct")
        self.btn_move.blockSignals(False)
        self._update_pause()

    def _start_pick(self):
        """Compte à rebours : laisse le temps de revenir en jeu et de cibler quelqu'un."""
        self._countdown = 3
        self.btn_pick.setEnabled(False)
        self._tick()

    def _tick(self):
        if self._countdown > 0:
            self.btn_pick.setText(f"Capture dans {self._countdown}…")
            self._countdown -= 1
            QTimer.singleShot(1000, self._tick)
            return
        self.btn_pick.setText("Placer sur une capture (3 s)")
        self.btn_pick.setEnabled(True)
        from .capture import MssGrabber
        from .zone_picker import open_picker
        grabber = self.grabber or MssGrabber()   # mss : instance propre au thread principal
        self.overlay.set_edit(False)
        self._picker = open_picker(self.cfg, grabber, self._pick_done)
        self._update_pause()

    def _pick_done(self, saved: bool):
        self._picker = None
        if saved:
            self.overlay.zone_changed()
            self.status.setText("● Cadres enregistrés")
        self._update_pause()

    def _update_pause(self, *_):
        paused = self.btn_pause.isChecked() or self.overlay.edit_mode or self._picker is not None
        self.worker.paused = paused
        compact = getattr(self, "_compact", False)
        self.btn_pause.setText(("▶" if compact else "Reprendre") if self.btn_pause.isChecked()
                               else ("‖" if compact else "Pause"))
        self.btn_pause.setToolTip("Suspendre / reprendre la lecture du nom de la cible")
        if self.btn_pause.isChecked():
            self.status.setText("● En pause")
        elif self.overlay.edit_mode:
            self.status.setText("● Réglage des cadres (lecture suspendue)")

    def _on_status(self, name: str, info):
        from .shugo import CharacterInfo
        self.btn_sheet.setEnabled(isinstance(info, CharacterInfo) and bool(info.url))
        if self.worker.paused:
            return
        self._update_tiles(name, info)
        if isinstance(info, CharacterInfo):
            self.status.setText(f"● {name} · GS {fmt(info.gear_score)} · CP {fmt(info.combat_power)}")
        elif info == "loading":
            self.status.setText(f"● Recherche de {name}…")
        elif info == "notfound":
            self.status.setText(f"● {name} · PNJ ou introuvable (rien affiché)")
        elif type(info).__name__ == "Ambiguous":
            servers = ", ".join(s.split("-", 1)[-1].title() for s in info.servers)
            self.status.setText(f"● {name} · homonymes ({servers}) : score non attribué")
        elif info == "error":
            self.status.setText(f"● {name}")
        elif not name:
            self.status.setText("● En lecture · aucune cible")

    def _update_tiles(self, name: str, info):
        from .shugo import CharacterInfo
        if isinstance(info, CharacterInfo):
            server = info.server.split("-", 1)[-1].replace("-", " ").title()
            self.t_target.set(name, " · ".join(p for p in (info.char_class, server) if p))
            self.t_gs.set(fmt(info.gear_score), f"Nv {info.level}" if info.level else "")
            self.t_cp.set(fmt(info.combat_power), f"TOP {info.rank}" if info.rank and info.rank <= 1000 else "")
        elif type(info).__name__ == "Ambiguous":
            self.t_target.set(name, f"homonymes ({len(info.servers)} serveurs)", "warn")
            self.t_gs.set(None, "non attribué")
            self.t_cp.set(None, "non attribué")
        elif info == "loading":
            self.t_target.set(name, "recherche…")
            self.t_gs.set(None)
            self.t_cp.set(None)
        elif info == "notfound":
            self.t_target.set(name, "PNJ ou introuvable")
            self.t_gs.set(None)
            self.t_cp.set(None)
        elif not name:
            self.t_target.set(None, "aucune cible")
            self.t_gs.set(None)
            self.t_cp.set(None)

    def _on_timing(self, name: str, seconds: float):
        secs = f"{seconds:.1f}".replace(".", ",")
        text = self.status.text()
        if name in text and " s" not in text[-3:]:
            self.status.setText(f"{text} · {secs} s")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        compact = self.width() < COMPACT_BELOW
        if getattr(self, "_compact", None) != compact:
            self._compact = compact
            for btn in self.nav_buttons.values():
                btn.set_compact(compact)
            self.nav_title.setVisible(not compact)
            self.sidebar.setFixedWidth(60 if compact else 178)
            self.btn_pause.setText(("‖" if compact else "Pause") if not self.btn_pause.isChecked()
                                   else ("▶" if compact else "Reprendre"))
            self.btn_quit.setText("✕" if compact else "Quitter")

    def _sync_nav(self):
        """Libellés de la navigation = titres des pages (ex. « Notifications (3) »)."""
        for i, btn in self.nav_buttons.items():
            text = self.tabs.tabText(i)
            if text and text != btn.label_text:
                btn.label_text = text
                btn.refresh()

    def show_and_raise(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, e):
        # La fenêtre EST l'application : la fermer quitte proprement.
        self.quit_app()
        e.accept()
