"""Compteur de session « Kinah / minute » : variation NETTE du solde observé.

Ce n'est PAS un calcul de butin : achats, ventes, marché, échanges comptent aussi (le résultat
peut être négatif). Le solde vient des lectures déjà faites par currency.py (aucun OCR ni
capture en plus).

- Une valeur n'est retenue qu'après DEUX lectures identiques consécutives et un format valide
  (entier positif plausible) : une lecture OCR erronée isolée est ignorée, une vraie variation
  (même importante) est acceptée dès qu'elle est lue deux fois.
- Le temps en pause ne compte pas. Après un redémarrage, une session en cours revient
  « interrompue » : rien n'est compté tant que l'utilisateur ne clique pas « Reprendre »
  (le temps hors ligne n'est jamais ajouté en silence).
"""
from __future__ import annotations

import time

MAX_AMOUNT = 10 ** 12          # au-delà : lecture aberrante
MIN_RATE_SECONDS = 30          # Kinah/minute affiché seulement après 30 s actives
KEEP_SAMPLES = 720             # mesures horodatées gardées (≈ 24 min à 2 s, bien plus si stable)


def valid_amount(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and 0 <= v < MAX_AMOUNT


class ValueGate:
    """Deux lectures identiques consécutives avant d'accepter une valeur."""

    def __init__(self, need: int = 2):
        self.need, self._cand, self._n = need, None, 0

    def push(self, v) -> int | None:
        if not valid_amount(v):
            self._cand, self._n = None, 0
            return None
        if v == self._cand:
            self._n += 1
        else:
            self._cand, self._n = v, 1
        return v if self._n >= self.need else None


class KinahSession:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.state = "idle"            # idle · running · paused · interrupted
        self.initial: int | None = None
        self.current: int | None = None
        self.active_s = 0.0            # temps actif cumulé AVANT la reprise en cours
        self.resumed_at: float | None = None
        self.started_at: float | None = None
        self.last_valid_at: float | None = None
        self.samples: list[tuple[float, int]] = []
        self.rejected = 0
        self.gate = ValueGate()

    # --- commandes ---
    def start(self, known: int | None = None):
        now = self.clock()
        self.state, self.started_at, self.resumed_at = "running", now, now
        self.active_s, self.samples, self.rejected = 0.0, [], 0
        self.initial = self.current = known if valid_amount(known) else None
        if self.initial is not None:
            self.last_valid_at = now
            self.samples.append((now, self.initial))
        self.gate = ValueGate()

    def pause(self):
        if self.state == "running":
            self.active_s += self.clock() - (self.resumed_at or self.clock())
            self.resumed_at = None
            self.state = "paused"

    def resume(self):
        if self.state in ("paused", "interrupted"):
            self.state, self.resumed_at = "running", self.clock()
            self.gate = ValueGate()       # deux nouvelles lectures avant de compter à nouveau

    def reset(self):
        self.__init__(self.clock)

    # --- lectures ---
    def feed(self, value) -> bool:
        """Lecture du solde suivi. Vrai si elle est retenue."""
        if self.state != "running":
            return False
        v = self.gate.push(value)
        if v is None:
            if value is not None and not valid_amount(value):
                self.rejected += 1
            return False
        now = self.clock()
        self.last_valid_at = now
        if self.initial is None:
            self.initial = v
        if v != self.current or not self.samples:
            self.samples.append((now, v))
            del self.samples[:-KEEP_SAMPLES]
        self.current = v
        return True

    # --- résultats ---
    def elapsed(self) -> float:
        run = self.clock() - self.resumed_at if self.state == "running" and self.resumed_at else 0.0
        return self.active_s + max(0.0, run)

    def net(self) -> int | None:
        if self.initial is None or self.current is None:
            return None
        return self.current - self.initial

    def per_minute(self) -> float | None:
        el, net = self.elapsed(), self.net()
        if net is None or el < MIN_RATE_SECONDS:
            return None
        return net / (el / 60)

    def per_hour(self) -> float | None:
        m = self.per_minute()
        return None if m is None else m * 60

    def age(self) -> float | None:
        """Secondes depuis la dernière lecture valide."""
        return None if self.last_valid_at is None else self.clock() - self.last_valid_at

    # --- sauvegarde ---
    def to_dict(self) -> dict:
        return {"state": self.state, "initial": self.initial, "current": self.current,
                "active_s": round(self.elapsed(), 1), "started_at": self.started_at,
                "last_valid_at": self.last_valid_at, "saved_at": self.clock(),
                "samples": self.samples[-KEEP_SAMPLES:], "rejected": self.rejected}

    @classmethod
    def from_dict(cls, d: dict | None, clock=time.time) -> "KinahSession":
        s = cls(clock)
        if not d:
            return s
        s.initial = d.get("initial") if valid_amount(d.get("initial")) else None
        s.current = d.get("current") if valid_amount(d.get("current")) else None
        s.active_s = float(d.get("active_s") or 0.0)
        s.started_at, s.last_valid_at = d.get("started_at"), d.get("last_valid_at")
        s.samples = [(float(t), int(v)) for t, v in (d.get("samples") or []) if valid_amount(v)]
        s.rejected = int(d.get("rejected") or 0)
        state = d.get("state", "idle")
        # Session en cours à la fermeture : jamais reprise en silence.
        s.state = "interrupted" if state == "running" else (state if state in ("paused", "idle") else "idle")
        return s


def fmt_rate(v: float | None) -> str:
    if v is None:
        return "—"
    from .currency import fmt_amount
    return fmt_amount(int(round(v)))


def fmt_elapsed(seconds: float) -> str:
    s = int(seconds)
    h, m = divmod(s // 60, 60)
    return f"{h} h {m:02d} min" if h else f"{m} min {s % 60:02d} s"
