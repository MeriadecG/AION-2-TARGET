"""Dictionnaire des vrais noms de joueurs, pour corriger les lectures OCR.

La mémoire contient ~21 000 joueurs européens : un nom lu de travers (« 45Corgoth »,
« 4SCorgoth », « Corgotn ») est ramené au vrai nom (« Corgoth »), ce qui permet de
l'afficher immédiatement au lieu de chercher un joueur qui n'existe pas.

- Préfixe de niveau collé : 1 à 4 caractères en tête (chiffres ou sosies : S→5, O→0, l→1…)
  retirés si le reste est un vrai nom. Uniquement s'il y a au moins un vrai chiffre dans le
  préfixe (« Sn1per » n'est pas « 5 » + « n1per »).
- Une lettre mal lue / en trop / en moins : index « par suppression » (méthode SymSpell),
  seulement pour les noms d'au moins 5 caractères et si UN SEUL nom correspond, et JAMAIS sur
  la première lettre (« Vakron » n'est pas « akron » : deux joueurs différents).
- Nom très court (1-2 caractères) : accepté seulement s'il suit un niveau lu (« 45 dN ») ;
  une lettre isolée de l'interface (« E », « DN ») n'est pas un joueur.
"""
from __future__ import annotations

import re
import unicodedata

# Caractères que l'OCR produit à la place du niveau « (45) » : chiffres et sosies.
LEVEL_CHARS = set("0123456789OoSsIl|BZzg()[]{}.,:;'`°@©®-_ ")
MIN_FUZZY_LEN = 5
_DISTANCE_RE = re.compile(r"\s*\d{1,3}(?:[.,]\d)?\s*m\W*$", re.IGNORECASE)
_TAG_RE = re.compile(r"\s*[\[({]\s*[A-Za-z]{2,5}\s*[\])}].*$")
_WORD_RE = re.compile(r"^[^\W_]+$")


def _norm(s: str) -> str:
    return unicodedata.normalize("NFC", s).strip().casefold()


def _deletes(s: str) -> set[str]:
    return {s[:i] + s[i + 1:] for i in range(len(s))}


def levenshtein_le1(a: str, b: str) -> bool:
    """Vrai si a et b diffèrent d'au plus une substitution, insertion ou suppression."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la > lb:
        a, b, la, lb = b, a, lb, la
    i = 0
    while i < la and a[i] == b[i]:
        i += 1
    if la == lb:
        return a[i + 1:] == b[i + 1:]
    return a[i:] == b[i + 1:]


class NameIndex:
    def __init__(self, names=()):
        self._exact: dict[str, str] = {}
        self._dels: dict[str, str | tuple[str, ...]] = {}
        for n in names:
            self.add(n)

    def __len__(self):
        return len(self._exact)

    def __contains__(self, name: str):
        return _norm(name) in self._exact

    def add(self, name: str):
        key = _norm(name)
        if not key or key in self._exact:
            if key:
                self._exact[key] = name
            return
        self._exact[key] = name
        if len(key) >= MIN_FUZZY_LEN:
            for v in _deletes(key):
                cur = self._dels.get(v)
                if cur is None:
                    self._dels[v] = key
                elif isinstance(cur, str):
                    if cur != key:
                        self._dels[v] = (cur, key)
                elif key not in cur:
                    self._dels[v] = cur + (key,)

    # -- recherche --
    def _bases(self, text: str) -> list[str]:
        """Formes à essayer, de la plus probable à la moins probable."""
        t = _norm(text)
        if not t:
            return []
        out = []
        compact = t.replace(" ", "")
        for c in (compact, t.split()[-1] if " " in t else None):
            if c and c not in out:
                out.append(c)
        return out

    def _variants(self, base: str) -> list[str]:
        """La base, puis la base sans préfixe de niveau (2, 1, 3, 4 caractères)."""
        out = [base]
        for k in (2, 1, 3, 4):
            if len(base) - k < 2:
                continue
            prefix = base[:k]
            if all(ch in LEVEL_CHARS for ch in prefix) and any(ch.isdigit() for ch in prefix):
                out.append(base[k:].lstrip(" ._-"))
        if base[:1].isdigit():
            # Commence par un chiffre : probablement un niveau collé → retrait d'abord,
            # le nom brut (« 4llin ») en dernier recours.
            out = out[1:] + out[:1]
        return out

    def _fuzzy(self, q: str) -> str | None:
        if len(q) < MIN_FUZZY_LEN - 1:
            return None
        found: set[str] = set()

        def collect(v):
            cur = self._dels.get(v)
            if cur is None:
                return
            found.update((cur,) if isinstance(cur, str) else cur)
        collect(q)                                    # lettre manquante dans la lecture
        for v in _deletes(q):
            if v in self._exact and len(v) >= MIN_FUZZY_LEN:
                found.add(v)                          # lettre en trop dans la lecture
            collect(v)                                # lettre différente
        # Jamais de correction sur la 1re lettre : « Vakron » ≠ « akron », « Wakron » ≠ « Vakron ».
        found = {k for k in found if levenshtein_le1(q, k) and k[:1] == q[:1]}
        return self._exact[found.pop()] if len(found) == 1 else None

    def resolve(self, text: str | None) -> str | None:
        """Vrai nom correspondant à une lecture OCR, ou None si rien de sûr."""
        if not text:
            return None
        bases = self._bases(text)
        variants = [v for b in bases for v in self._variants(b)]
        # Un niveau a-t-il été lu avant le nom ? (« 45 dN », « 45dN »)
        has_level = any(ch.isdigit() for ch in _norm(text)[:4])
        for v in variants:
            hit = self._exact.get(v)
            if hit:
                if len(v) < 2 or (len(v) == 2 and not has_level):
                    continue                          # « E », « DN » sans niveau : pas un joueur
                return hit
        for v in variants:
            if _WORD_RE.match(v):
                hit = self._fuzzy(v)
                if hit:
                    return hit
        return None

    def resolve_line(self, raw: str | None) -> str | None:
        """Même chose à partir d'une ligne brute (« 45 Corgoth2m », « leyndell [Isr]18 m »).

        Uniquement si, une fois distance et balise retirées, il reste UN seul mot
        (un PNJ « Marchand d'alchimie » n'est jamais rapproché d'un joueur)."""
        if not raw:
            return None
        s = _TAG_RE.sub("", _DISTANCE_RE.sub("", raw.strip()))
        s = _DISTANCE_RE.sub("", s).strip()
        compact = s.replace(" ", "")
        if not compact or not _WORD_RE.match(compact.lstrip("".join(LEVEL_CHARS))
                                             if compact[:1] in LEVEL_CHARS else compact):
            return None
        return self.resolve(s)
