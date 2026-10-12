"""Logique pure (testable) : décide quand une lecture OCR devient une cible à rechercher."""
from __future__ import annotations

from .name_filter import clean, is_plausible_player


class TargetTracker:
    """Exige `stable_reads` lectures identiques avant de valider un nom.

    feed() renvoie :
      - le nom à rechercher quand une NOUVELLE cible stable apparaît ;
      - "" quand la cible disparaît (plus rien de lisible) → effacer l'affichage ;
      - None sinon (rien de nouveau).
    """

    def __init__(self, stable_reads: int = 2, blacklist: list[str] | None = None):
        self.stable_reads = max(1, stable_reads)
        self.blacklist = {b.lower() for b in (blacklist or [])}
        self.current: str | None = None
        self._candidate: str | None = None
        self._count = 0
        self._empty_count = 0

    def feed(self, raw: str, trusted: bool = False) -> str | None:
        """trusted=True : le nom est celui d'un joueur réel connu (mémoire) → une erreur d'OCR
        est quasi impossible, on le valide dès la PREMIÈRE lecture (≈ 0,2 s gagnées)."""
        if trusted:
            name = clean(raw)
            if is_plausible_player(name, self.blacklist):
                self._candidate, self._count, self._empty_count = name, self.stable_reads, 0
                if name != self.current:
                    self.current = name
                    return name
                return None
        return self._feed(raw)

    def _feed(self, raw: str) -> str | None:
        name = clean(raw)
        if not is_plausible_player(name, self.blacklist):
            self._candidate, self._count = None, 0
            self._empty_count += 1
            if self.current is not None and self._empty_count >= self.stable_reads:
                self.current = None
                return ""
            return None

        self._empty_count = 0
        if name == self._candidate:
            self._count += 1
        else:
            self._candidate, self._count = name, 1

        if self._count >= self.stable_reads and name != self.current:
            self.current = name
            return name
        return None
