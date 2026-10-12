"""Génère aion_target/assets/familiars.json (catalogue des familiers) — à relancer quand le jeu
ajoute des familiers ou quand A2Tools met à jour ses tables de PNJ.

Entrées (aucune donnée inventée) :
  1. tools/data/pets_global_en.txt : liste officielle (noms anglais du client global) par genre,
     relevée sur https://www.aion2guidance.com/en/pets/ (« Last checked: Oct 12, 2026 »).
  2. Tables de PNJ d'A2Tools (GPL-3.0) : src/data/i18n/npcs/en.json et fr.json, mêmes
     identifiants de PNJ dans les deux langues.

Rapprochements :
  - « exact »  : un PNJ porte EXACTEMENT le nom anglais du familier → nom français = celui de ce
                 PNJ, identifiants internes = ceux de ces PNJ (preuve : nom identique) ;
  - « espèce » : le nom anglais d'un PNJ CONTIENT le nom du familier comme mots entiers
                 (« Docile Papis » → Papis), le familier au nom le plus long l'emporte
                 (« Aberrant Odyle Spider » ne compte pas pour « Spider »). Indicatif : la source
                 ne certifie pas quels monstres donnent quelles âmes.
Usage : python tools/make_familiars.py <dossier A2Tools>/src/data/i18n/npcs
"""
import collections
import datetime
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "tools" / "data" / "pets_global_en.txt"
DST = ROOT / "aion_target" / "assets" / "familiars.json"
A2TOOLS_COMMIT = "71868460c0ee3b286427bd51d5d862e45276ee42"


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.casefold()).strip("-")


def read_list(path: Path):
    genus, out = None, []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            genus = line[1:-1]
            continue
        out.append((genus, line))
    return out


def word_re(name: str):
    return re.compile(r"(?<![\w'])" + re.escape(name.casefold()) + r"(?![\w'])")


def build(npc_dir: Path) -> dict:
    en = json.loads((npc_dir / "en.json").read_text(encoding="utf-8"))
    fr = json.loads((npc_dir / "fr.json").read_text(encoding="utf-8"))
    pets = read_list(SRC)
    by_name = collections.defaultdict(list)
    for k, v in en.items():
        by_name[v["name"].casefold()].append(k)
    patterns = sorted(((name, word_re(name)) for _, name in pets), key=lambda p: -len(p[0]))
    species: dict[str, list[str]] = collections.defaultdict(list)
    for k, v in en.items():
        n = v["name"].casefold()
        if v.get("isDummy"):
            continue
        for name, rx in patterns:                     # le plus long d'abord : le plus précis
            if rx.search(n):
                if n != name.casefold():
                    species[name].append(k)
                break
    out = []
    for genus, name in pets:
        exact = sorted(by_name.get(name.casefold(), []), key=int)
        fr_names = collections.Counter(fr.get(i, {}).get("name") for i in exact if fr.get(i))
        name_fr = fr_names.most_common(1)[0][0] if fr_names else None
        mobs = collections.OrderedDict()
        for kind, ids in (("exact", exact), ("espèce", sorted(species.get(name, []), key=int))):
            for i in ids:
                fr_name = (fr.get(i) or {}).get("name") or en[i]["name"]
                key = (fr_name, kind)
                m = mobs.setdefault(key, {"fr": fr_name, "en": en[i]["name"], "match": kind, "ids": [],
                                          "boss": False})
                m["ids"].append(int(i))
                m["boss"] = m["boss"] or bool(en[i].get("isBoss"))
        out.append({
            "id": slug(name), "genus": genus, "name_en": name, "name_fr": name_fr,
            "name_fr_source": "PNJ du même nom (A2Tools fr)" if name_fr else None,
            "special": genus == "Special",
            "mobs": list(mobs.values()),
        })
    return {
        "version": 1,
        "game": "AION 2 — client global (Europe)",
        "generated": datetime.date.today().isoformat(),
        "count": len(out),
        "announced_count": 207,
        "missing_note": "La source annonce 207 familiers (dont 7 spéciaux) ; le 7e spécial est tronqué "
                        "dans la page (« Two… ») : non ajouté plutôt qu'inventé. Ajout manuel possible.",
        "sources": [
            {"what": "liste des familiers par genre (noms anglais officiels du client global)",
             "url": "https://www.aion2guidance.com/en/pets/", "checked": "2026-10-12"},
            {"what": "noms français et identifiants des PNJ (en.json / fr.json)",
             "url": "https://github.com/taengu/A2Tools-DPS-Meter", "commit": A2TOOLS_COMMIT,
             "license": "GPL-3.0"},
        ],
        "thresholds": {
            "global": {"label": "Client global : 5 / 30 / 105 âmes cumulées (Nv 1 / 2 / 3)",
                       "levels": [5, 30, 105],
                       "source": "https://www.aion2guidance.com/en/pets/ (5 + 25 + 75)"},
            "kr_tw": {"label": "Corée / Taïwan : niveau 5 à 500 âmes cumulées (paliers 4 et 5 à vérifier)",
                      "levels": [5, 30, 105, 500],
                      "source": "https://sagye.kr/tips/pets (LV 5 = 500 âmes) — palier 4 inconnu"},
        },
        "pets": out,
    }


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    data = build(Path(sys.argv[1]))
    DST.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    n_fr = sum(1 for p in data["pets"] if p["name_fr"])
    n_mob = sum(1 for p in data["pets"] if p["mobs"])
    print(f"{data['count']} familiers → {DST} ({n_fr} avec nom français, {n_mob} avec monstres associés)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
