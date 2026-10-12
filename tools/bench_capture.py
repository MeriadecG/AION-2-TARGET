"""Coût CPU de la découpe Wayland (portail) : ancien chemin (image entière copiée et décodée à
chaque lecture) contre le nouveau (seules les lignes de la zone). Hors GStreamer : la conversion
plein écran que faisait videoconvert (BGRx → RGB) s'ajoutait à l'ancien chemin et n'est pas
comptée ici. Usage : python tools/bench_capture.py [largeur hauteur]"""
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from aion_target.capture import crop_global, decode_rgb, pixel_rect, rows_to_rgb  # noqa: E402


def main(w=2560, h=1440, reps=60):
    zone = (1100, 60, 300, 35)
    rgb = bytes(np.random.randint(0, 255, w * h * 3, np.uint8))     # ancien format (RGB)
    bgrx = bytes(np.random.randint(0, 255, w * h * 4, np.uint8))    # format natif PipeWire
    stride = w * 4

    def old():
        frame = decode_rgb(bytes(memoryview(rgb)), w, h)            # bytes(info.data) + copie
        return crop_global(frame, (0, 0), None, *zone)

    def new():
        x0, y0, x1, y1 = pixel_rect(w, h, (0, 0), None, *zone)
        rows = bgrx[y0 * stride:y1 * stride]                         # extract_dup des lignes
        return rows_to_rgb(rows, "BGRx", stride, x0, x1, y1 - y0)

    for label, fn in (("ancien (image entière)", old), ("nouveau (zone seule)", new)):
        fn()
        t = []
        for _ in range(reps):
            t0 = time.perf_counter()
            fn()
            t.append((time.perf_counter() - t0) * 1000)
        t.sort()
        print(f"{label:<24} {w}x{h} : médiane {t[len(t) // 2]:6.2f} ms · max {t[-1]:6.2f} ms par lecture")


if __name__ == "__main__":
    a = [int(x) for x in sys.argv[1:3]]
    main(*a) if a else main()
