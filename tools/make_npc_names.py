"""Génère aion_target/assets/npc_names.txt (noms de PNJ français) depuis engine/data/npcs_fr.json.

Données issues d'A2Tools (GPL-3.0, voir NOTICE.md). Sert à reconnaître une cible PNJ
(« Vakron ») : jamais corrigée vers un joueur, jamais cherchée sur shugo.gg.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "engine" / "data" / "npcs_fr.json"
DST = ROOT / "aion_target" / "assets" / "npc_names.txt"


def main():
    data = json.loads(SRC.read_text(encoding="utf-8"))
    names = sorted({v.get("name", "").strip() for v in data.values()} - {"", "None", "???"},
                   key=str.casefold)
    names = [n for n in names if len(n) >= 3 and "\n" not in n]
    DST.write_text("# Noms de PNJ (source : A2Tools, GPL-3.0) - généré par tools/make_npc_names.py\n"
                   + "\n".join(names) + "\n", encoding="utf-8")
    print(f"{len(names)} noms → {DST}")


if __name__ == "__main__":
    sys.exit(main())
