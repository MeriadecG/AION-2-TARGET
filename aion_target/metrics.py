"""Mesures de latence : percentiles p50 / p95 / p99 par étape (diagnostic, sans dépendance).

Étapes mesurées par la boucle de lecture :
  capture      : prise de l'image de la zone
  ocr          : lecture du texte
  resolve      : correction / identification du nom (dictionnaire, relecture)
  first_cache  : changement de cible → premier score affiché, quand il venait de la MÉMOIRE
  first_net    : changement de cible → premier score affiché, quand il a fallu le RÉSEAU
  fresh        : changement de cible → score actualisé (fiche relue)
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
        order = ["capture", "ocr", "resolve", "first_cache", "first_net", "fresh"]
        with self._lock:
            stages = order + sorted(k for k in self._data if k not in order)
        lines = []
        for st in stages:
            s = self.summary(st)
            if s:
                lines.append(f"Latence {st:<11} n={s['n']:<4} p50 {s['p50']:7.0f} ms · "
                             f"p95 {s['p95']:7.0f} ms · p99 {s['p99']:7.0f} ms · max {s['max']:7.0f} ms")
        return lines


STATS = LatencyStats()
