"""Familiers : catalogue, progression par familier, comptage des monstres tués.

Deux compteurs DISTINCTS par familier :
  - « morts »  : monstres de l'espèce tués (automatique si le moteur réseau le prouve, sinon à
                 la main) ;
  - « âmes »   : progression du familier, TOUJOURS manuelle (le flux réseau étudié ne contient
                 aucun événement d'obtention d'âme) — jamais déduite des morts (1 mort ≠ 1 âme).
Paliers de niveaux : configurables (client global : 5 / 30 / 105 âmes cumulées, source dans le
catalogue) ; le compteur peut dépasser l'objectif.
Catalogue : aion_target/assets/familiars.json (généré par tools/make_familiars.py, sources et
date incluses) + ajouts manuels de l'utilisateur (pets.json, dossier de configuration).
"""
from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

ASSET = Path(__file__).with_name("assets") / "familiars.json"
GENUS_FR = {"Cogni": "Cogni (intellect)", "Fera": "Fera (sauvage)", "Natura": "Natura (nature)",
            "Varian": "Varian (variant)", "Special": "Spécial", "Perso": "Ajouté à la main"}
DEFAULT_LEVELS = [5, 30, 105]
JOURNAL_MAX = 2000


def norm(s: str) -> str:
    return " ".join((s or "").casefold().split())


@dataclass
class Pet:
    id: str
    genus: str
    name_en: str
    name_fr: str | None = None
    special: bool = False
    mobs: list = field(default_factory=list)     # [{fr, en, match, ids, boss}]
    custom: bool = False

    @property
    def name(self) -> str:
        return self.name_fr or self.name_en

    @property
    def alias(self) -> str | None:
        return self.name_en if self.name_fr and self.name_fr != self.name_en else None

    def search_text(self) -> str:
        return norm(" ".join([self.name_en, self.name_fr or "", self.genus,
                              *[m.get("fr", "") for m in self.mobs], *[m.get("en", "") for m in self.mobs]]))


class Catalog:
    def __init__(self, data: dict, custom: list | None = None):
        self.meta = {k: v for k, v in data.items() if k != "pets"}
        self.pets: list[Pet] = [Pet(p["id"], p["genus"], p["name_en"], p.get("name_fr"), p.get("special", False),
                                    p.get("mobs") or []) for p in data.get("pets", [])]
        for c in custom or []:
            self.pets.append(Pet(c["id"], c.get("genus", "Perso"), c["name_en"], c.get("name_fr"),
                                 False, c.get("mobs") or [], custom=True))
        self.by_id = {p.id: p for p in self.pets}
        self._reindex()

    def _reindex(self):
        self._by_code: dict[int, list[str]] = {}
        self._by_name: dict[str, list[str]] = {}
        for p in self.pets:
            for m in p.mobs:
                for i in m.get("ids") or []:
                    self._by_code.setdefault(int(i), []).append(p.id)
                for n in (m.get("fr"), m.get("en")):
                    if n:
                        self._by_name.setdefault(norm(n), []).append(p.id)

    @classmethod
    def load(cls, custom: list | None = None, path: Path = ASSET) -> "Catalog":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {"pets": []}
        return cls(data, custom)

    def search(self, text: str = "", genus: str | None = None) -> list[Pet]:
        q = norm(text)
        return [p for p in self.pets if (not genus or p.genus == genus) and (not q or q in p.search_text())]

    def pets_for_mob(self, code: int | None, name: str | None) -> list[str]:
        """Familiers dont ce monstre fait partie (identifiant de PNJ d'abord, sinon nom exact)."""
        ids = list(self._by_code.get(int(code), [])) if code is not None else []
        if not ids and name:
            ids = list(self._by_name.get(norm(name), []))
        return list(dict.fromkeys(ids))

    def thresholds(self) -> dict:
        return self.meta.get("thresholds") or {"global": {"label": "5 / 30 / 105", "levels": DEFAULT_LEVELS}}


def level_of(souls: int, levels: list[int]) -> tuple[int, int | None]:
    """(niveau atteint, prochain objectif ou None si le dernier est atteint)."""
    lvl = sum(1 for t in levels if souls >= t)
    nxt = levels[lvl] if lvl < len(levels) else None
    return lvl, nxt


def parse_levels(text: str) -> list[int] | None:
    try:
        vals = [int(x) for x in text.replace(";", ",").replace("/", ",").split(",") if x.strip()]
    except ValueError:
        return None
    if not vals or any(v <= 0 for v in vals) or vals != sorted(vals) or len(set(vals)) != len(vals):
        return None
    return vals


class PetStore:
    """Progression persistante (pets.json) : morts, âmes, suivi, paliers, ajouts manuels."""

    def __init__(self, path: Path, clock=time.time):
        self.path, self.clock = Path(path), clock
        self.data = {"version": 1, "progress": {}, "custom": [], "edition": "global",
                     "custom_levels": None, "auto": False}
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.data.update(loaded)
        except (OSError, ValueError):
            pass

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)                     # écriture atomique : jamais de fichier coupé

    def get(self, pet_id: str) -> dict:
        p = self.data["progress"].setdefault(pet_id, {})
        p.setdefault("kills", 0)
        p.setdefault("souls", 0)
        p.setdefault("tracked", False)
        p.setdefault("auto", True)
        return p

    def set_value(self, pet_id: str, key: str, value: int):
        self.get(pet_id)[key] = max(0, int(value))
        self.get(pet_id)["updated"] = self.clock()
        self.save()

    def add(self, pet_id: str, key: str, delta: int):
        self.set_value(pet_id, key, self.get(pet_id)[key] + delta)

    def reset(self, pet_id: str):
        p = self.get(pet_id)
        p["kills"] = p["souls"] = 0
        p["updated"] = self.clock()
        self.save()

    def set_tracked(self, pet_id: str, on: bool):
        self.get(pet_id)["tracked"] = bool(on)
        self.save()

    def tracked(self) -> list[str]:
        return [k for k, v in self.data["progress"].items() if v.get("tracked")]

    def levels(self, catalog: Catalog) -> list[int]:
        if self.data.get("edition") == "custom" and self.data.get("custom_levels"):
            return list(self.data["custom_levels"])
        th = catalog.thresholds().get(self.data.get("edition") or "global")
        return list((th or {}).get("levels") or DEFAULT_LEVELS)

    def add_custom(self, name: str, mobs: list[str]) -> str:
        import re
        pid = "perso-" + (re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-") or "familier")
        self.data["custom"].append({"id": pid, "genus": "Perso", "name_en": name, "name_fr": name,
                                    "mobs": [{"fr": m, "en": m, "match": "manuel", "ids": []} for m in mobs]})
        self.save()
        return pid


class KillCounter:
    """Applique les morts signalées par le moteur aux familiers suivis (une seule fois par mort)."""

    def __init__(self, catalog: Catalog, store: PetStore, journal_path: Path | None = None, clock=time.time):
        self.catalog, self.store, self.clock = catalog, store, clock
        self.journal_path = journal_path
        self.journal: deque = deque(maxlen=JOURNAL_MAX)
        self._seen: deque = deque(maxlen=4096)
        self._seen_set: set = set()
        self.counts = {"counted": 0, "ignored": 0, "uncertain": 0, "duplicate": 0}
        if journal_path:
            self._load_journal()

    def _load_journal(self):
        try:
            for line in self.journal_path.read_text(encoding="utf-8").splitlines()[-JOURNAL_MAX:]:
                self.journal.append(json.loads(line))
        except (OSError, ValueError):
            pass

    def _log(self, entry: dict):
        self.journal.append(entry)
        if not self.journal_path:
            return
        try:
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
            with self.journal_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            if self.journal_path.stat().st_size > 1_000_000:      # rotation simple
                lines = self.journal_path.read_text(encoding="utf-8").splitlines()[-JOURNAL_MAX:]
                self.journal_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            pass

    def on_kill(self, ev: dict) -> list[str]:
        """Événement « kill » du moteur → familiers incrémentés (ids). Décision journalisée."""
        key = (ev.get("entity"), ev.get("spawn") or ev.get("first_hit") or ev.get("code"))
        name = ev.get("name") or f"PNJ {ev.get('code')}"
        verdict = ev.get("verdict")
        entry = {"t": round(ev.get("t", 0) / 1000 if ev.get("t") else self.clock(), 1), "monstre": name,
                 "code": ev.get("code"), "verdict": verdict, "raison": ev.get("reason") or "", "familiers": []}
        if key in self._seen_set:
            self.counts["duplicate"] += 1
            return []                                   # même mort signalée deux fois
        self._remember(key)
        pets = self.catalog.pets_for_mob(ev.get("code"), ev.get("name"))
        if verdict != "mine":
            entry["decision"] = "incertaine" if verdict == "uncertain" else "ignorée"
            self.counts["uncertain" if verdict == "uncertain" else "ignored"] += 1
            self._log(entry)
            return []
        counted = []
        for pid in pets:
            p = self.store.get(pid)
            if p.get("tracked") and p.get("auto", True) and self.store.data.get("auto"):
                p["kills"] += 1
                p["updated"] = self.clock()
                counted.append(pid)
        if counted:
            self.store.save()
            self.counts["counted"] += 1
            entry["decision"] = "comptée"
        else:
            self.counts["ignored"] += 1
            entry["decision"] = "ignorée"
            entry["raison"] = entry["raison"] or ("aucun familier suivi pour ce monstre" if pets else
                                                  "monstre sans familier connu")
        entry["familiers"] = counted
        self._log(entry)
        return counted

    def _remember(self, key):
        if len(self._seen) == self._seen.maxlen:
            self._seen_set.discard(self._seen[0])
        self._seen.append(key)
        self._seen_set.add(key)
