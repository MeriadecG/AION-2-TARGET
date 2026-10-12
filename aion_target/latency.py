"""Mesure du retard de la capture d'écran (indépendante du jeu, de l'OCR et du réseau).

Un petit carré change de couleur (noir ↔ blanc) à un endroit connu de l'écran ; on capture
cet endroit en boucle et on chronomètre le temps avant que le changement apparaisse dans
l'image capturée. Sous KDE Wayland, c'est le délai du partage d'écran (PipeWire / KWin).

1. Le carré devient noir : on attend de le VOIR noir dans la capture (1re mesure, qui sert
   aussi à régler l'intervalle).
2. Puis il alterne blanc / noir, à un intervalle plus long que ce retard : chaque changement
   est vu avant le suivant, aucune confusion possible (même avec 5 s de retard).
"""
from __future__ import annotations

import statistics
import threading
import time

FLIPS = 6
MIN_INTERVAL = 1.0        # secondes minimum entre deux changements
TIMEOUT = 10.0            # sans changement visible au bout de ce délai : capture figée
PROBE = 24                # côté du carré (pixels)
DARK, LIGHT = 60, 200     # seuils de luminosité moyenne (évite les valeurs ambiguës)


def summarize(latencies_ms: list[float], missed: int) -> str:
    """Phrase compréhensible à partir des mesures (logique pure, testée)."""
    if not latencies_ms:
        return ("✗ Le carré de test n'est jamais apparu dans la capture : l'image capturée ne se "
                "met pas à jour (capture figée, ou ce n'est pas l'écran partagé).")
    med = statistics.median(latencies_ms)
    lo, hi = min(latencies_ms), max(latencies_ms)
    verdict = ("✓ capture rapide" if med < 150 else
               "≈ capture un peu lente" if med < 500 else
               "✗ capture TRÈS en retard : c'est elle qui cause l'attente")
    miss = f", {missed} changement(s) jamais vu(s)" if missed else ""
    return (f"Retard de capture : médiane {med:.0f} ms (min {lo:.0f}, max {hi:.0f}, "
            f"{len(latencies_ms)} mesures{miss}) — {verdict}")


class CaptureLatencyTest:
    """`set_color(bool blanc)` change la couleur du carré ; `grab(x, y, w, h)` capture.
    Tout le pilotage tourne dans un thread ; `set_color` est appelé depuis ce thread (la
    fenêtre Qt doit le relayer par un signal). Résultat : `on_done(texte, mesures)`."""

    def __init__(self, grab, set_color, rect, on_done, flips=FLIPS, min_interval=MIN_INTERVAL,
                 timeout=TIMEOUT):
        self.grab, self.set_color, self.rect = grab, set_color, rect
        self.on_done = on_done
        self.flips, self.min_interval, self.timeout = flips, min_interval, timeout
        self.latencies: list[float] = []
        self.missed = 0

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _level(self) -> str | None:
        try:
            m = float(self.grab(*self.rect).mean())
        except Exception:
            return None
        return "white" if m > LIGHT else "black" if m < DARK else None

    def _wait_for(self, want: str, since: float) -> float | None:
        while time.monotonic() - since < self.timeout:
            if self._level() == want:
                return (time.monotonic() - since) * 1000
            time.sleep(0.005)
        return None

    def _run(self):
        try:
            t0 = time.monotonic()
            self.set_color(False)
            first = self._wait_for("black", t0)
            if first is None:
                self.on_done(summarize([], 1), [])
                return
            self.latencies.append(first)
            interval = max(self.min_interval, first / 1000 + 0.5)
            flips = self.flips if first < 1000 else 3          # gros retard : test raccourci
            white = False
            for _ in range(flips):
                time.sleep(max(0.0, interval - 0.3))
                white = not white
                t = time.monotonic()
                self.set_color(white)
                seen = self._wait_for("white" if white else "black", t)
                if seen is None:
                    self.missed += 1
                else:
                    self.latencies.append(seen)
            self.on_done(summarize(self.latencies, self.missed), list(self.latencies))
        finally:
            self.set_color(None)                               # fin : masquer le carré
