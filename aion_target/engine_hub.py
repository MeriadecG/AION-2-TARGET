"""UN SEUL moteur réseau passif, partagé par les modules qui en ont besoin.

Modes demandeurs (chacun activé par l'utilisateur dans son onglet) :
  « dps »      : DPS meter (onglet DPS)
  « observe »  : observation légère des joueurs, préchargement GS/CP (onglet Target)
  « pets »     : comptage automatique des monstres tués (onglet Familiers)
Le moteur démarre quand au moins un mode le demande et s'arrête quand plus aucun ne le
demande ; jamais deux captures. Le calcul DPS n'est envoyé que si « dps » est actif.
L'avertissement d'usage (capture passive) est commun et obligatoire à la première activation.
"""
from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from .engine_client import EngineClient

WARNING = (
    "Ce module lit une COPIE du trafic réseau du jeu sur ton PC (comme Wireshark) :\n"
    "aucune injection, aucune modification du jeu, aucune écriture réseau, aucun déchiffrement.\n\n"
    "NCSoft n'a publié ni autorisation ni interdiction explicite des compteurs de dégâts ; ses "
    "règles sanctionnent largement les « programmes tiers ». Le risque pour ton compte est faible "
    "mais n'est pas nul.\n\n"
    "Windows : nécessite Npcap (npcap.com). Linux : autorisation de capture (mot de passe, une fois "
    "par version du moteur).\n\nActiver la capture passive ?")

STATE_LABELS = {
    "off": "arrêté",
    "starting": "démarrage…",
    "searching": "recherche du flux du jeu",
    "active": "actif",
    "missing_engine": "moteur introuvable",
    "missing_library": "Npcap / libpcap manquant",
    "no_permission": "permission de capture manquante",
    "error": "erreur",
    "stopped": "arrêté (erreur)",
}


def engine_state(wanted: bool, st: dict) -> str:
    """État simple à partir du dernier statut du moteur."""
    if not wanted:
        return "off"
    cap = st.get("capture")
    if cap in ("missing_engine", "missing_library", "no_permission"):
        return cap
    if st.get("stopped"):
        return "stopped"
    if st.get("error"):
        return "error"
    if cap == "running":
        return "active" if st.get("game_port") else "searching"
    return "starting"


class EngineHub(QObject):
    status = pyqtSignal(dict)        # dernier statut (+ « state »)
    dps = pyqtSignal(dict)
    players = pyqtSignal(list)
    party = pyqtSignal(list)
    fight = pyqtSignal(dict)
    kill = pyqtSignal(dict)
    modes_changed = pyqtSignal(set)

    def __init__(self, cfg, log, client_factory=None, parent=None):
        super().__init__(parent)
        self.cfg, self.log = cfg, log
        self._factory = client_factory or (lambda: EngineClient(log, cfg.dps_mode, parent=self))
        self.client = None
        self.modes: set[str] = set()
        self.last_status: dict = {}

    # --- modes ---
    def wanted(self) -> bool:
        return bool(self.modes)

    def set_mode(self, mode: str, on: bool):
        before = set(self.modes)
        if on:
            self.modes.add(mode)
        else:
            self.modes.discard(mode)
        if self.modes == before:
            return
        if self.modes:
            self._ensure_started()
            self.client.set_publish_dps("dps" in self.modes)
        else:
            self._stop()
        self.modes_changed.emit(set(self.modes))
        self._emit_status()

    def _ensure_started(self):
        if self.client is None:
            c = self.client = self._factory()
            c.status.connect(self._on_status)
            c.dps.connect(self.dps)
            c.players.connect(self.players)
            c.party.connect(self.party)
            c.fight.connect(self.fight)
            if hasattr(c, "kill"):
                c.kill.connect(self.kill)
            c.stopped.connect(lambda r: self._on_status({**c.last_status, "stopped": r}))
        err = self.client.start()
        if err:
            self._on_status({"capture": "missing_engine", "detail": err})

    def _stop(self):
        if self.client:
            self.client.stop()
        self.last_status = {}

    def restart(self):
        """Après l'octroi d'une permission : relance propre."""
        if self.client:
            self.client.stop()
            self.client.last_status = {}
        self.last_status = {}
        if self.modes:
            self._ensure_started()
            self.client.set_publish_dps("dps" in self.modes)

    def shutdown(self):
        self.modes.clear()
        self._stop()

    # --- commandes DPS ---
    def reset(self):
        if self.client:
            self.client.reset()

    def set_dps_mode(self, mode: str):
        if self.client:
            self.client.set_mode(mode)

    @property
    def ping_ms(self):
        return self.client.ping_ms if self.client else None

    @property
    def path(self):
        return self.client.path if self.client else None

    # --- statut ---
    def _on_status(self, st: dict):
        self.last_status = dict(st)
        self._emit_status()

    def _emit_status(self):
        st = dict(self.last_status)
        st["state"] = engine_state(self.wanted(), st)
        st["modes"] = sorted(self.modes)
        self.status.emit(st)

    def state(self) -> str:
        return engine_state(self.wanted(), self.last_status)
