"""Banc d'essai OCR sur de VRAIES captures du cadre de cible (tests/fixtures).

Compare, à résultat égal, des variantes de la lecture « une ligne » (RapidOCR sans détection) :
  actuel      : image RGB telle quelle (réglage de l'application)
  rogné       : marges de fond uniformes retirées (largeur réduite → reconnaissance plus courte)
  1 thread    : même lecture avec 1 seul thread ONNX
  détection   : chemin complet (référence, utilisé pour les zones hautes / relecture)
Usage : python tools/bench_ocr.py [répétitions]
"""
import statistics
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIX = ROOT / "tests" / "fixtures"
CASES = [("target_corgoth.png", (24, 59, 60, 360), "Corgoth"),
         ("target_abyss_leyndell.png", (30, 54, 60, 380), "leyndell")]


def trim_margins(img: np.ndarray, tol: float = 6.0, pad: int = 4) -> np.ndarray:
    """Retire à gauche / à droite les colonnes de fond uniformes (écart-type faible)."""
    g = img.mean(axis=2)
    busy = np.where(g.std(axis=0) > tol)[0]
    if busy.size == 0:
        return img
    x0, x1 = max(0, busy[0] - pad), min(img.shape[1], busy[-1] + 1 + pad)
    return np.ascontiguousarray(img[:, x0:x1])


def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(round((len(v) - 1) * p / 100)))]


def main(reps: int = 40):
    from rapidocr import RapidOCR

    from aion_target.name_filter import parse_target
    from aion_target.ocr import ENGINE_PARAMS, _no_spinning
    _no_spinning()
    eng2 = RapidOCR(params=ENGINE_PARAMS)
    eng1 = RapidOCR(params={**ENGINE_PARAMS, "EngineConfig.onnxruntime.intra_op_num_threads": 1})

    def rec(engine, img):
        r = engine(img, use_det=False, use_cls=False)
        txt = (getattr(r, "txts", None) or [""])[0]
        h, w = img.shape[:2]
        name, _ = parse_target([(txt, 1.0, [[0, 0], [w, 0], [w, h], [0, h]])])
        return name

    def det(engine, img):
        r = engine(img, use_cls=False)
        txts, scores = getattr(r, "txts", None) or (), getattr(r, "scores", None) or ()
        boxes = getattr(r, "boxes", None)
        boxes = boxes if boxes is not None else [[[0, 0]] * 4] * len(txts)
        lines = [(t, float(s), np.asarray(b).tolist()) for t, s, b in zip(txts, scores, boxes)]
        return parse_target(lines)[0]

    variants = {
        "actuel": lambda img: rec(eng2, img),
        "rogné": lambda img: rec(eng2, trim_margins(img)),
        "1 thread": lambda img: rec(eng1, img),
        "détection": lambda img: det(eng2, img),
    }
    print(f"{len(CASES)} captures réelles × {reps} répétitions, {__import__('os').cpu_count()} cœurs")
    for label, fn in variants.items():
        times, ok = [], 0
        for f, (y0, y1, x0, x1), expected in CASES:
            img = np.ascontiguousarray(np.asarray(Image.open(FIX / f).convert("RGB"))[y0:y1, x0:x1])
            fn(img)                                            # préchauffage
            for _ in range(reps):
                t = time.perf_counter()
                name = fn(img)
                times.append((time.perf_counter() - t) * 1000)
                ok += name == expected
        n = len(CASES) * reps
        print(f"{label:<10} exact {ok}/{n} · p50 {statistics.median(times):5.1f} ms · "
              f"p95 {pct(times, 95):5.1f} ms · max {max(times):5.1f} ms")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 40)
