"""Onglet Target, section « Détection et préchargement des joueurs ».

Active le mode « observe » du moteur partagé (EngineHub) : capture passive SANS DPS meter, pour
précharger les GS/CP des joueurs vus dans le flux du jeu. Les compteurs sont rafraîchis toutes
les 2 s (jamais à chaque message du moteur : aucune charge sur l'interface).
"""
from __future__ import annotations

import time

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QCheckBox, QLabel, QPushButton, QVBoxLayout, QWidget

from .dps_ui import ack_capture, describe_state, fix_capture

REFRESH_MS = 2000


def counters_text(prefetcher, now: float | None = None) -> str:
    """Résumé lisible des compteurs du préchargement (sans nom de joueur)."""
    if prefetcher is None:
        return "Compteurs indisponibles."
    s = prefetcher.stats
    now = prefetcher.clock() if now is None else now
    seen = prefetcher.seen
    recent = sum(1 for o in seen.values() if now - o.last < 120)
    with_server = sum(1 for o in seen.values() if o.server)
    in_memory = sum(1 for o in seen.values() if o.local in ("récent", "ancien", "connu"))
    return (f"Joueurs observés cette session : <b>{len(seen)}</b> (vus il y a moins de 2 min : {recent})<br>"
            f"Avec serveur identifié : <b>{with_server}</b> · trouvés en mémoire : <b>{in_memory}</b><br>"
            f"Préchargements en attente : <b>{len(prefetcher.queue)}</b> · requêtes envoyées : {s['sent']}"
            + (f" · reportées (refus du site) : {s['deferred_limit']}" if s.get("deferred_limit") else "")
            + "<br><i>Joueurs vus passer dans le flux du jeu, pas la liste complète de l'instance.</i>")


class ObservationSection(QWidget):
    def __init__(self, cfg, hub, prefetcher, card, log):
        super().__init__()
        self.cfg, self.hub, self.prefetcher, self.log = cfg, hub, prefetcher, log
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        g = card(root, "DÉTECTION ET PRÉCHARGEMENT DES JOUEURS")
        self.chk = QCheckBox("Observation réseau légère (sans DPS)")
        self.chk.setToolTip(
            "Capture passive du trafic du jeu (même moteur que le DPS meter, un seul pour les deux) : "
            "les joueurs qui passent dans le flux sont cherchés en mémoire (immédiat) puis, si besoin, "
            "sur shugo.gg (débit limité), pour que leur score s'affiche tout de suite quand tu les "
            "cibles. Ce n'est PAS la liste complète des joueurs de l'instance.")
        self.chk.setChecked(cfg.observe_enabled)
        self.chk.toggled.connect(self._toggle)
        g.addWidget(self.chk, 0, 0, 1, 2)
        self.chk_pf = QCheckBox("Précharger aussi sur shugo.gg (20 requêtes/min au plus)")
        self.chk_pf.setChecked(cfg.dps_prefetch)
        self.chk_pf.toggled.connect(self._prefetch)
        g.addWidget(self.chk_pf, 1, 0, 1, 2)
        self.state = QLabel()
        self.state.setObjectName("status")
        self.state.setWordWrap(True)
        g.addWidget(self.state, 2, 0, 1, 2)
        self.btn_fix = QPushButton()
        self.btn_fix.setVisible(False)
        self.btn_fix.clicked.connect(lambda: fix_capture(self, self.hub, self.log))
        g.addWidget(self.btn_fix, 3, 0, 1, 2)
        self.counters = QLabel()
        self.counters.setObjectName("status")
        self.counters.setWordWrap(True)
        g.addWidget(self.counters, 4, 0, 1, 2)
        self._last_status: dict = {}
        hub.status.connect(self._on_status)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(REFRESH_MS)
        self._on_status({})
        if cfg.observe_enabled:
            QTimer.singleShot(600, lambda: self.hub.set_mode("observe", True))

    def _toggle(self, on: bool):
        if on and not ack_capture(self, self.cfg, self.chk):
            return
        self.cfg.observe_enabled = on
        self.cfg.save()
        self.hub.set_mode("observe", on)

    def _prefetch(self, on: bool):
        self.cfg.dps_prefetch = on
        self.cfg.save()

    def _on_status(self, st: dict):
        self._last_status = st or {"state": self.hub.state(), "modes": sorted(self.hub.modes)}
        self.refresh()

    def refresh(self):
        st = self._last_status
        text, fix = describe_state(st, self.cfg.observe_enabled, "observe")
        self.state.setText(text)
        self.btn_fix.setVisible(bool(fix))
        if fix:
            self.btn_fix.setText(fix)
        running = st.get("state") in ("active", "searching")
        if self.cfg.observe_enabled or running:
            self.counters.setText(counters_text(self.prefetcher, time.monotonic()))
            self.counters.setVisible(True)
        else:
            self.counters.setVisible(False)
