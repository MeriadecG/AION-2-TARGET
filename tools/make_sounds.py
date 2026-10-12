"""Génère les sons de notification intégrés (WAV 16 bits mono, synthèse : aucun droit d'auteur).

  python tools/make_sounds.py   → aion_target/assets/sounds/*.wav
"""
from pathlib import Path
import wave

import numpy as np

RATE = 22050
OUT = Path(__file__).resolve().parent.parent / "aion_target" / "assets" / "sounds"


def tone(freq, dur, decay, partials=((1, 1.0),), attack=0.005):
    t = np.arange(int(RATE * dur)) / RATE
    s = sum(a * np.sin(2 * np.pi * freq * r * t) * np.exp(-t * decay * (1 + 0.6 * (r - 1)))
            for r, a in partials)
    env = np.minimum(1, t / attack)
    return s * env


def at(total, *parts):
    """Mélange des (début_s, signal)."""
    out = np.zeros(int(RATE * total))
    for start, sig in parts:
        i = int(RATE * start)
        n = min(len(sig), len(out) - i)
        out[i:i + n] += sig[:n]
    return out


BELL = ((1, 1.0), (2.0, 0.5), (2.76, 0.35), (5.4, 0.15))
SOUNDS = {
    "carillon": at(1.4, (0, tone(1318.5, 1.0, 4, BELL)), (0.32, tone(1046.5, 1.08, 3.5, BELL))),
    "cloche": tone(880, 1.8, 2.2, BELL),
    "cristal": at(0.9, (0, tone(2093, 0.8, 7, ((1, 1), (2, 0.4)))), (0.09, tone(3136, 0.7, 8))),
    "gong": tone(196, 2.4, 1.3, ((1, 1.0), (1.48, 0.6), (2.31, 0.4), (3.2, 0.2)), attack=0.03),
    "alerte": at(0.75, *[(i * 0.16, tone(1000, 0.09, 8, ((1, 1), (3, 0.2)))) for i in range(3)]),
    "appel": at(1.2, *[(i * 0.13, tone(f, 0.9, 5, ((1, 1), (2, 0.3), (3, 0.1))))
                       for i, f in enumerate((523.25, 659.25, 783.99, 1046.5))]),
}


def write(name, sig):
    sig = sig / np.max(np.abs(sig)) * 0.7            # ≈ −3 dB, sans saturation
    fade = int(RATE * 0.03)
    sig[-fade:] *= np.linspace(1, 0, fade)
    with wave.open(str(OUT / f"{name}.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((sig * 32767).astype("<i2").tobytes())


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, sig in SOUNDS.items():
        write(name, sig)
        print(OUT / f"{name}.wav")
