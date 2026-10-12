"""Rapport de diagnostic exportable, lisible APRÈS la session (pas besoin de surveiller en jeu).

Par défaut il ne contient AUCUNE donnée sensible : pas de capture de trafic, pas de nom de
joueur, pas d'interface réseau, pas de chemin personnel, pas de solde de Kinah absolu (seulement
les variations). Les modules s'y ajoutent via `register(titre, fonction)` ; la fonction reçoit
`include_names` (faux par défaut) et renvoie des lignes de texte.
"""
from __future__ import annotations

import os
import platform
import re
import time

SECTIONS: list[tuple[str, object]] = []
_HOME = os.path.expanduser("~")


def register(title: str, fn):
    SECTIONS.append((title, fn))


def scrub(line: str) -> str:
    """Retire chemins personnels et adresses IP d'une ligne."""
    if _HOME and _HOME != "/":
        line = line.replace(_HOME, "~")
    return re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "x.x.x.x", line)


def build(include_names: bool = False) -> str:
    from . import __version__
    out = [f"Aion Target {__version__} — rapport de diagnostic du {time.strftime('%d/%m/%Y %H:%M')}",
           f"Système : {platform.system()} {platform.release()} · Python {platform.python_version()} · "
           f"{os.cpu_count()} cœurs",
           "Contenu : mesures et compteurs uniquement (aucune capture réseau, "
           + ("noms de joueurs INCLUS" if include_names else "aucun nom de joueur") + ").", ""]
    for title, fn in SECTIONS:
        out.append(f"== {title} ==")
        try:
            lines = fn(include_names) or ["(rien)"]
        except Exception as e:                       # le rapport ne doit jamais planter
            lines = [f"(indisponible : {type(e).__name__})"]
        out += [scrub(str(x)) for x in lines]
        out.append("")
    return "\n".join(out)


def speed_section(include_names: bool) -> list[str]:
    """Percentiles de vitesse + (option) lignes du journal, qui contiennent des noms."""
    from .metrics import STATS, extra_lines
    lines = STATS.report() + extra_lines()
    if include_names:
        from .overlay import speed_report
        lines += ["", *speed_report().splitlines()[1:]]
    return lines


register("Vitesse Target (p50 / p95 / p99)", speed_section)
