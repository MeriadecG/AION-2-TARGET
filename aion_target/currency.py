"""Monnaie calculée : lit la barre des monnaies du jeu et affiche « valeur A − valeur B ».

Exemple (barre « 1 300 | 807 572 | 707 572 | 22 212 ») : valeur 2 − valeur 3 = 100 000, une
monnaie que le jeu n'affiche que dans ses menus.

- Lecture légère : un thread à part, une capture de la petite zone toutes les 2 s, OCR
  seulement si l'image a changé. Moteur OCR séparé de celui du GS/CP (aucune interférence).
- Fiabilité : une valeur n'est affichée qu'après DEUX lectures identiques ; si la barre est
  cachée (menu plein écran, chargement), la dernière valeur reste affichée.
- Deux emplacements possibles : la barre (haut gauche) puis, si elle est illisible, le menu
  (haut droite, 2e cadre facultatif). A, B et A − B viennent TOUJOURS de la même capture : on
  ne soustrait jamais deux valeurs lues à des moments différents.
- Chaque lecture est horodatée (signal `reading`) : l'interface montre l'âge de la dernière
  mesure valide et le compteur Kinah/minute s'en sert (aucun OCR en plus).
- Réseau : le cœur d'analyse utilisé (A2Tools, commit 7186846) ne décode AUCUN solde de
  monnaie (aucun champ kinah / monnaie / inventaire dans src-tauri/src) : lecture OCR seulement.
"""
from __future__ import annotations

import re
import threading
import time

import numpy as np

from PyQt6.QtCore import QObject, pyqtSignal

INTERVAL = 2.0          # secondes entre deux lectures
UPSCALE = 2             # mesuré : chiffres 100 % lus à 2x (1x : icône collée « Q807572 »)
MAX_VALUES = 8


def parse_values(lines) -> list[int]:
    """Boîtes OCR [(texte, score, boîte)] → montants de gauche à droite.

    Un montant peut être coupé en deux boîtes à l'espace des milliers (« 807 » « 572 ») :
    une boîte de exactement 3 chiffres collée à la précédente (écart < 0,6 hauteur) est
    rattachée. Les icônes entre les montants laissent un écart bien plus grand.
    """
    items = []
    for text, score, box in lines:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        digits = re.sub(r"\D", "", text)
        if digits:
            items.append((min(xs), max(xs), max(ys) - min(ys), digits))
    items.sort()
    merged: list[list] = []
    for x0, x1, h, digits in items:
        if merged and len(digits) == 3 and x0 - merged[-1][1] < 0.6 * max(h, 1):
            merged[-1][1] = x1
            merged[-1][2] += digits
        else:
            merged.append([x0, x1, digits])
    return [int(d) for _, _, d in merged[:MAX_VALUES] if len(d) <= 15]


def compute(values: list[int], a: int, b: int) -> int | None:
    """valeur a − valeur b (positions à partir de 1) ; None si la barre est incomplète."""
    if not values or min(a, b) < 1 or max(a, b) > len(values):
        return None
    return values[a - 1] - values[b - 1]


def fmt_amount(n: int | None) -> str:
    if n is None:
        return "—"
    s = f"{abs(n):,}".replace(",", " ")       # espace fine insécable, à la française
    return ("−" if n < 0 else "") + s


class CurrencyReader:
    """OCR des montants (moteur RapidOCR à part, chargé à la première lecture)."""

    def __init__(self):
        self._engine = None
        self._last_img: np.ndarray | None = None
        self._last: list[int] = []

    def read(self, img: np.ndarray) -> list[int]:
        from .ocr import ENGINE_PARAMS, _no_spinning, frames_similar
        if frames_similar(img, self._last_img, tol=1.5):
            return self._last
        if self._engine is None:
            from rapidocr import RapidOCR
            _no_spinning()
            params = dict(ENGINE_PARAMS)
            params["EngineConfig.onnxruntime.intra_op_num_threads"] = 1   # tâche de fond : 1 thread
            params["Det.limit_side_len"] = 1600     # bande large et basse : ne pas la réduire
            self._engine = RapidOCR(params=params)
        self._last_img = img
        big = img.repeat(UPSCALE, axis=0).repeat(UPSCALE, axis=1)
        r = self._engine(big, use_cls=False)
        txts = getattr(r, "txts", None) or ()
        scores = getattr(r, "scores", None) or ()
        boxes = getattr(r, "boxes", None)
        if boxes is None:
            return []
        lines = [(t, float(s), (np.asarray(b) / UPSCALE).tolist())
                 for t, s, b in zip(txts, scores, boxes) if s >= 0.5]
        self._last = parse_values(lines)
        return self._last


class Stabilizer:
    """N'accepte une valeur qu'après `need` lectures identiques consécutives."""

    def __init__(self, need: int = 2):
        self.need, self._cand, self._count = need, None, 0
        self.value: int | None = None

    def push(self, v: int | None) -> bool:
        """True si la valeur affichée change."""
        if v is None:
            self._cand, self._count = None, 0
            return False
        if v == self._cand:
            self._count += 1
        else:
            self._cand, self._count = v, 1
        if self._count >= self.need and v != self.value:
            self.value = v
            return True
        return False


def make_reading(values: list[int], a: int, b: int, zone: str, t: float) -> dict:
    """Lecture horodatée : A, B et A − B issus de la MÊME capture (None si absents)."""
    va = values[a - 1] if 1 <= a <= len(values) else None
    vb = values[b - 1] if 1 <= b <= len(values) else None
    return {"t": t, "zone": zone, "values": list(values), "a": va, "b": vb,
            "diff": va - vb if va is not None and vb is not None else None}


class CurrencyWorker(QObject):
    """Thread de fond : lit la zone toutes les INTERVAL secondes."""
    values = pyqtSignal(list)          # montants lus (diagnostic dans l'onglet Farm)
    reading = pyqtSignal(dict)         # lecture horodatée (a, b, diff, zone)
    amount = pyqtSignal(object)        # valeur calculée validée (int)
    error = pyqtSignal(str)

    def __init__(self, cfg, grabber=None, log=print):
        super().__init__()
        self.cfg, self.grabber, self.log = cfg, grabber, log
        self.stab = Stabilizer()
        self.stab.value = cfg.currency_last
        self.paused = False
        self.extra_wanted = lambda: False   # compteur de session actif : lire même sans affichage
        self.last_reading: dict | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="aion-currency", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    def read_once(self, grabber, reader, alt_reader) -> dict:
        """Barre principale ; si elle ne donne pas A et B, le 2e emplacement (menu)."""
        cfg = self.cfg
        vals = reader.read(grabber.grab(*cfg.currency_zone))
        rd = make_reading(vals, cfg.currency_a, cfg.currency_b, "barre", time.time())
        if rd["diff"] is None and cfg.currency_alt_enabled:
            alt = alt_reader.read(grabber.grab(*cfg.currency_zone_alt))
            rd_alt = make_reading(alt, cfg.currency_alt_a, cfg.currency_alt_b, "menu", time.time())
            if rd_alt["diff"] is not None or (rd["a"] is None and rd_alt["a"] is not None):
                return rd_alt
        return rd

    def _run(self):
        grabber = self.grabber
        reader = CurrencyReader()
        last_err = ""
        alt_reader = CurrencyReader()
        while not self._stop.wait(INTERVAL):
            if self.paused or not (self.cfg.currency_enabled or self.extra_wanted()):
                continue
            try:
                if grabber is None:
                    from .capture import MssGrabber
                    grabber = MssGrabber()         # mss : à créer dans CE thread
                rd = self.read_once(grabber, reader, alt_reader)
                self.last_reading = rd
                self.reading.emit(rd)
                self.values.emit(list(rd["values"]))
                if self.stab.push(rd["diff"]):
                    self.amount.emit(self.stab.value)
                last_err = ""
            except Exception as e:                 # jamais d'arrêt du thread
                msg = f"{type(e).__name__}: {e}"
                if msg != last_err:
                    self.log(f"Monnaie : lecture impossible ({msg})")
                    self.error.emit(msg)
                    last_err = msg
                time.sleep(INTERVAL)
