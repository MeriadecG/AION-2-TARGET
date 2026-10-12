"""Reconnaissance des cibles PNJ (« Vakron ») à partir de leur nom lu par l'OCR.

Un PNJ n'est jamais corrigé vers un joueur ni cherché sur shugo.gg : l'overlay n'affiche
rien. Liste : aion_target/assets/npc_names.txt (données A2Tools, GPL-3.0, voir NOTICE.md),
générée par tools/make_npc_names.py.

Lectures gérées : « Vakron », « 54Vakron », « X54Vakron » (niveau collé), « Vakron1 »,
« Vakrons » (un caractère en trop), « 54 Vakron lis » (mot parasite), « Vakrom » (une lettre
mal lue, uniquement après échec du dictionnaire des joueurs). Un joueur qui porte EXACTEMENT
ce nom reste un joueur.
"""
from __future__ import annotations

import re
from pathlib import Path

from .names import _DISTANCE_RE, _TAG_RE, NameIndex, _norm

ASSET = Path(__file__).with_name("assets") / "npc_names.txt"
MIN_TOKEN = 4          # mot isolé dans une ligne : « Ours » oui, « Gaz » non
MIN_FUZZY = 6
_TRAILING = set("0123456789sl|i.,'")
_LEVEL_RE = re.compile(r"^[^\W\d_]?[0-9oOsSlI|]*\d[0-9oOsSlI|]*[\s._-]*")


class NpcIndex:
    def __init__(self, names=()):
        self._exact: dict[str, str] = {}
        for n in names:
            k = _norm(n).replace(" ", "")
            if len(k) >= 3:
                self._exact.setdefault(k, n)
        self._fuzzy = NameIndex(k for k in self._exact if len(k) >= MIN_FUZZY)

    def __len__(self):
        return len(self._exact)

    @classmethod
    def load(cls, path: Path = ASSET) -> "NpcIndex":
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return cls()
        return cls(l.strip() for l in lines if l.strip() and not l.startswith("#"))

    @staticmethod
    def _candidates(text: str) -> list[str]:
        t = _norm(_TAG_RE.sub("", _DISTANCE_RE.sub("", text or "")))
        if not t:
            return []
        out: list[str] = []

        def push(c: str, min_len: int):
            for v in (c, _LEVEL_RE.sub("", c)):
                if len(v) >= min_len and v not in out:
                    out.append(v)
        push(t.replace(" ", ""), 3)
        rest = _LEVEL_RE.sub("", t)
        push(rest.replace(" ", ""), 3)
        for w in t.split():
            push(w, MIN_TOKEN)
        return out

    def match(self, *texts: str, is_player=lambda s: False, fuzzy: bool = False) -> str | None:
        """Nom du PNJ lu dans l'un des textes, sinon None.

        is_player(c) : vrai si c est EXACTEMENT un joueur connu (prioritaire sur le PNJ).
        fuzzy : autorise une lettre mal lue (à n'utiliser qu'après le dictionnaire des joueurs).
        """
        for text in texts:
            for c in self._candidates(text):
                if is_player(c):
                    continue
                # Un caractère parasite en fin de lecture : « Vakron1 », « Vakrons ».
                forms = [c] + ([c[:-1]] if len(c) >= 5 and c[-1] in _TRAILING else [])
                for f in forms:
                    hit = self._exact.get(f)
                    if hit and not is_player(f):
                        return hit
                if fuzzy and len(c) >= MIN_FUZZY and c.isalpha():
                    k = self._fuzzy._fuzzy(c)
                    if k and not is_player(c):
                        return self._exact[k]
        return None
