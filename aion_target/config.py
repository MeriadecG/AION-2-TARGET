"""Configuration persistée dans un JSON (zone de capture, serveurs, raccourcis)."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Serveurs EU vus sur shugo.gg. Mets ton serveur en premier : il est essayé d'abord.
EU_SERVERS = [
    "eu-israphel", "eu-nezekan", "eu-lumiel", "eu-siel", "eu-kaisinel",
    "eu-zikel", "eu-vaizel", "eu-triniel", "eu-marchutan", "eu-azphel",
]


CONFIG_VERSION = 3


def migrate(data: dict) -> dict:
    """Met à jour une ancienne config sans écraser les réglages choisis par l'utilisateur."""
    data = dict(data)
    if data.get("version", 1) < 2:
        # v1 : lecture toutes les 400 ms (OCR lent). v2 : OCR ~10x plus rapide → 100 ms.
        if data.get("poll_interval_ms") == 400:
            data["poll_interval_ms"] = 100
    if data.get("version", 1) < 3:
        # v3 : recherche d'abord sur les 3 serveurs les plus fréquents + API officielle.
        data.pop("font_size", None)   # la taille du texte suit désormais la hauteur des cadres
    data["version"] = CONFIG_VERSION
    return data


def default_frames(capture: list[int]) -> dict[str, list[int]]:
    """Cadres GS / CP / détails par défaut : juste sous la zone de lecture."""
    x, y, w, h = capture
    top = y + h + 4
    return {
        "frame_gs": [x, top, 130, 24],
        "frame_cp": [x + 136, top, 150, 24],
        "frame_details": [x, top + 26, 286, 16],
        "frame_rank": [x + 292, top, 110, 24],
    }


def rects_overlap(a: list[int], b: list[int]) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def keep_clear_of_zone(frame: list[int], zone: list[int]) -> list[int]:
    """Un cadre d'affichage ne doit JAMAIS recouvrir la zone de lecture : l'OCR lirait
    notre propre texte. S'il la chevauche, on le pousse juste en dessous."""
    if not rects_overlap(frame, zone):
        return list(frame)
    return [frame[0], zone[1] + zone[3] + 2, frame[2], frame[3]]


def config_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "aion-target" / "config.json"


@dataclass
class Config:
    # Zone de capture en pixels écran (x, y, largeur, hauteur).
    capture: list[int] = field(default_factory=lambda: [860, 40, 240, 32])
    servers: list[str] = field(default_factory=lambda: list(EU_SERVERS))
    poll_interval_ms: int = 100
    poll_idle_ms: int = 40          # image inchangée : revérification rapide (sans OCR)
    show_freshness: bool = True     # « il y a 3 j » quand le score affiché est ancien
    # Le nom doit être lu identique N fois de suite avant la recherche (anti-bruit OCR).
    stable_reads: int = 2
    ocr_min_confidence: float = 0.6
    hotkey_edit: str = "F8"        # basculer le mode réglage de la zone
    hotkey_quit: str = "Ctrl+F8"
    # Désactivés par défaut : sous Windows ils reposent sur un « hook » clavier système,
    # mal vu par certains anti-triche. La fenêtre de contrôle fait tout sans eux.
    global_hotkeys: bool = False
    blacklist: list[str] = field(default_factory=list)  # noms à ignorer (PNJ fréquents)
    debug_save_captures: bool = False
    # "auto" = portail PipeWire sous Wayland, mss sinon. Forçable : "mss" ou "portal".
    capture_backend: str = "auto"
    # Linux : fenêtre « override-redirect » (comme une infobulle) pour rester au-dessus
    # d'un jeu en plein écran, que KWin place sinon au-dessus des fenêtres « toujours devant ».
    overlay_above_fullscreen: bool = True
    portal_restore_token: str = ""

    # Apparence du panneau (couleurs « #RRGGBB »)
    gs_color: str = "#F2C14E"      # or
    cp_color: str = "#6FE3F5"      # cyan, comme l'icône CP de shugo.gg
    detail_color: str = "#C8C8C8"  # ligne classe / niveau / serveur
    font_family: str = "Cinzel"    # police embarquée (style Aion), ou toute police installée
    show_details: bool = True

    # Cadres d'affichage [x, y, largeur, hauteur] ; vides = calculés sous la zone de lecture.
    # La taille du texte suit la hauteur du cadre.
    frame_gs: list[int] = field(default_factory=list)
    frame_cp: list[int] = field(default_factory=list)
    frame_details: list[int] = field(default_factory=list)
    frame_rank: list[int] = field(default_factory=list)
    show_top: bool = True           # « TOP - 23 » si le joueur est dans le top 1 000 régional
    rank_color: str = "#E8A6FF"

    region: str = "eu"
    lookup_fanout: int = 3          # serveurs interrogés d'office (les plus fréquents)
    my_character: str = ""          # ton personnage : pastille plus fort / plus faible
    # Mémoire des joueurs croisés : limitée en TAILLE seulement (pas de durée). ~150 o/joueur.
    cache_max_mb: int = 100
    # Import du classement shugo.gg de la région : -1 = tout (≈ 21 000 EU), 0 = désactivé.
    import_max_players: int = -1
    import_state: dict = field(default_factory=dict)   # reprise / date du dernier import

    auto_update: bool = True        # mise à jour depuis GitHub à chaque lancement

    # Module DPS (moteur réseau passif, désactivé par défaut) et historiques.
    dps_enabled: bool = False
    dps_warning_ack: bool = False   # avertissement (règles NCSoft) lu et accepté
    dps_mode: str = "bossTargets"
    dps_overlay: bool = False
    dps_overlay_rect: list[int] = field(default_factory=lambda: [40, 260, 300, 170])
    dps_overlay_opacity: float = 0.85
    dps_overlay_rows: int = 6
    dps_record_observed: bool = False   # noter aussi les joueurs seulement « vus » par le réseau
    observe_enabled: bool = False       # capture passive SANS DPS meter (préchargement seul)
    dps_prefetch: bool = True           # précharger GS/CP des joueurs observés (serveur connu)
    history_max_fights: int = 500
    history_max_encounters: int = 20000
    ui_theme: str = "light"            # fenêtre de contrôle : light, dark ou system
    ui_collapsed: dict = field(default_factory=dict)   # sections repliées (titre → bool)
    control_tab: int = 0            # dernier onglet ouvert dans la fenêtre de contrôle

    # Monnaie calculée (onglet Farm) : valeur A − valeur B de la barre des monnaies du jeu.
    currency_enabled: bool = False
    currency_zone: list[int] = field(default_factory=lambda: [40, 0, 310, 26])   # lecture
    frame_currency: list[int] = field(default_factory=lambda: [44, 30, 230, 24])  # affichage
    currency_a: int = 2             # positions (1 = première valeur à gauche)
    currency_b: int = 3
    currency_label: str = ""
    currency_color: str = "#7CE0A3"
    currency_last: int | None = None   # dernière valeur lue (affichée dès le lancement)
    # 2e emplacement (menu en haut à droite) : lu seulement si la barre principale est illisible
    currency_alt_enabled: bool = False
    currency_zone_alt: list[int] = field(default_factory=lambda: [1500, 60, 380, 30])
    currency_alt_a: int = 1
    currency_alt_b: int = 2
    # zones mémorisées par résolution / échelle d'affichage (« 2560x1440@1.0 » → zones)
    currency_profiles: dict = field(default_factory=dict)
    # étiquettes (à vérifier dans l'inventaire du jeu ; neutres par défaut)
    currency_label_a: str = "Valeur A"
    currency_label_b: str = "Valeur B"
    # affichage à l'écran : champs parmi a, b, diff, rate
    farm_overlay_fields: list = field(default_factory=lambda: ["diff"])
    farm_value: str = "a"              # solde suivi par le compteur : a, b ou diff
    farm_session: dict = field(default_factory=dict)   # compteur Kinah/minute (sauvegarde)
    pets_overlay: bool = False
    pets_overlay_rect: list[int] = field(default_factory=lambda: [40, 440, 300, 90])
    pets_overlay_opacity: float = 0.85

    version: int = CONFIG_VERSION

    def __post_init__(self):
        defaults = default_frames(self.capture)
        for key, rect in defaults.items():
            if not getattr(self, key):
                setattr(self, key, rect)

    def frames(self) -> dict[str, list[int]]:
        return {"gs": self.frame_gs, "cp": self.frame_cp, "details": self.frame_details,
                "rank": self.frame_rank}

    def set_frame(self, role: str, rect: list[int]):
        if role == "zone":
            self.capture = [int(v) for v in rect]
        else:
            setattr(self, f"frame_{role}", keep_clear_of_zone([int(v) for v in rect], self.capture))

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or config_path()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
            cfg = cls(**migrate(known))
            if data.get("version") != CONFIG_VERSION:
                cfg.save(path)
            return cfg
        cfg = cls()
        cfg.save(path)
        return cfg

    def save(self, path: Path | None = None):
        path = path or config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
