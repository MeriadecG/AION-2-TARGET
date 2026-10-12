"""Mesures de latence : percentiles p50 / p95 / p99 par étape (diagnostic, sans dépendance).

Étapes (toutes en millisecondes, horloge monotone, vrais échantillons uniquement) :
  frame_age      : âge de l'image au moment où elle est lue (horodatage de capture si connu)
  new_frame_gap  : écart entre deux images NOUVELLES lues (Wayland ; borné par la cadence)
  capture        : prise de l'image de la zone
  ocr            : lecture du texte (images réellement relues, pas les images sautées)
  resolve        : correction / identification du nom (dictionnaire, PNJ)
  full_reread    : seconde lecture complète (nom inconnu après la lecture rapide)
  confirm        : 1re image du nom → cible validée
  ram            : consultation du score en mémoire (index RAM)
  remote_quick   : requête → réponse de la base shugo.gg
  remote_full    : requête → fiche shugo.gg (relue chez NCSoft)
  first_cache    : 1re image du nom → 1er score PRÊT, venu de la mémoire locale
  first_net      : 1re image du nom → 1er score PRÊT, venu du réseau
  fresh          : 1re image du nom → score actualisé prêt
  deliver        : score émis par la lecture → cadres PEINTS (passage entre threads compris)
  paint_first_cache / paint_first_net / paint_fresh : 1re image du nom → cadres PEINTS
"""
from __future__ import annotations

import threading
from collections import defaultdict, deque

KEEP = 500          # échantillons gardés par étape


def percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return float("nan")
    k = (len(sorted_vals) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


class LatencyStats:
    def __init__(self):
        self._lock = threading.Lock()
        self._data: dict[str, deque] = defaultdict(lambda: deque(maxlen=KEEP))

    def add(self, stage: str, ms: float):
        with self._lock:
            self._data[stage].append(float(ms))

    def summary(self, stage: str) -> dict | None:
        with self._lock:
            vals = sorted(self._data.get(stage, ()))
        if not vals:
            return None
        return {"n": len(vals), "p50": percentile(vals, 50), "p95": percentile(vals, 95),
                "p99": percentile(vals, 99), "max": vals[-1]}

    def report(self) -> list[str]:
        order = ["frame_age", "new_frame_gap", "capture", "ocr", "resolve", "full_reread", "confirm", "ram",
                 "remote_quick", "remote_full", "first_cache", "first_net", "fresh", "deliver",
                 "paint_first_cache", "paint_first_net", "paint_fresh"]
        with self._lock:
            stages = order + sorted(k for k in self._data if k not in order)
        lines = []
        for st in stages:
            s = self.summary(st)
            if s:
                lines.append(f"Latence {st:<17} n={s['n']:<4} p50 {s['p50']:7.0f} ms · "
                             f"p95 {s['p95']:7.0f} ms · p99 {s['p99']:7.0f} ms · max {s['max']:7.0f} ms")
        return lines


    def clear(self):
        with self._lock:
            self._data.clear()


STATS = LatencyStats()

# Fournisseurs de lignes supplémentaires pour le rapport (préchargement, mémoire…).
REPORTERS: list = []


def extra_lines() -> list[str]:
    out = []
    for fn in list(REPORTERS):
        try:
            line = fn()
        except Exception as e:                      # le rapport ne doit jamais planter
            line = f"(rapport indisponible : {type(e).__name__})"
        if line:
            out.append(line)
    return out
