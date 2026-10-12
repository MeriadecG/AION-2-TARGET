"""Contenu de chaque cadre d'affichage (logique pure, testable sans écran)."""
from __future__ import annotations

from pathlib import Path

ASSETS = Path(__file__).parent / "assets"
RED = "#E5484D"
TOP_LIMIT = 1000
GREEN = "#3DD68C"

# Contenu d'un cadre : liste de segments (texte, couleur « #RRGGBB »)
Segments = list[tuple[str, str]]


def fmt(n: int | None) -> str:
    return f"{n:,}".replace(",", " ") if n is not None else "?"


def threat(target_cp: int | None, my_cp: int | None) -> str | None:
    """« stronger » (pastille rouge), « weaker » (verte) ou None si on ne sait pas."""
    if not target_cp or not my_cp:
        return None
    return "stronger" if target_cp > my_cp else "weaker"


def frame_contents(info, cfg, my_cp: int | None = None) -> dict[str, Segments]:
    """Texte de chaque cadre pour un profil. Rien du tout si pas de profil (PNJ, recherche…)."""
    from .shugo import CharacterInfo
    if not isinstance(info, CharacterInfo):
        return {"gs": [], "cp": [], "details": [], "rank": []}
    details: Segments = []
    if cfg.show_details:
        server = info.server.split("-", 1)[-1].replace("-", " ").title()
        text = " · ".join(p for p in (info.char_class,
                                       f"Nv {info.level}" if info.level else None, server) if p)
        details = [(text, cfg.detail_color)]
    return {
        "gs": [(f"GS {fmt(info.gear_score)}", cfg.gs_color)],
        "cp": [(f"CP {fmt(info.combat_power)}", cfg.cp_color)],
        "details": details,
        "rank": ([(f"TOP - {info.rank}", cfg.rank_color)]
                 if cfg.show_top and info.rank and info.rank <= TOP_LIMIT else []),
    }


def sample_info():
    """Exemple affiché pendant le placement des cadres et le réglage du style."""
    from .shugo import CharacterInfo
    return CharacterInfo("Corgoth", "eu-israphel", 3369, 229116, 45, "Templar", rank=23)


_registered: dict[str, str] = {}


def register_bundled_fonts() -> list[str]:
    """Charge les polices de assets/ dans Qt. À appeler après QApplication()."""
    from PyQt6.QtGui import QFontDatabase
    families = []
    for ttf in sorted(ASSETS.glob("*.ttf")):
        if ttf.name not in _registered:
            fid = QFontDatabase.addApplicationFont(str(ttf))
            fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
            _registered[ttf.name] = fams[0] if fams else ""
        if _registered[ttf.name]:
            families.append(_registered[ttf.name])
    return families
