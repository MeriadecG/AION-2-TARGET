import threading
import time

import numpy as np

from aion_target.latency import CaptureLatencyTest, summarize


def run_with_fake_capture(delay_s: float, flips=3, frozen=False, background=128):
    """Fausse capture qui montre le carré avec `delay_s` de retard (ou jamais si figée)."""
    history = []                       # (instant, None | False | True)

    def set_color(white):
        history.append((time.monotonic(), white))

    def grab(x, y, w, h):
        now = time.monotonic() - delay_s
        seen = [c for t, c in history if t <= now]
        state = None if (frozen or not seen) else seen[-1]
        value = background if state is None else (255 if state else 0)
        return np.full((h, w, 3), value, np.uint8)

    done = threading.Event()
    out = {}

    def on_done(text, lat):
        out.update(text=text, lat=lat)
        done.set()
    t = CaptureLatencyTest(grab, set_color, (0, 0, 24, 24), on_done, flips=flips,
                           min_interval=0.4, timeout=8)
    t.start()
    assert done.wait(60)
    out["history"] = history
    return out


def test_measures_small_delay():
    out = run_with_fake_capture(0.15)
    assert len(out["lat"]) == 4                               # 1re apparition + 3 changements
    assert all(130 <= v <= 300 for v in out["lat"]), out["lat"]
    assert "rapide" in out["text"] or "un peu lente" in out["text"]


def test_measures_large_delay_without_confusion():
    out = run_with_fake_capture(2.0)
    assert out["lat"] and all(1900 <= v <= 2300 for v in out["lat"]), out["lat"]
    assert "TRÈS en retard" in out["text"]


def test_frozen_capture_is_reported():
    out = run_with_fake_capture(0, frozen=True)
    assert out["lat"] == [] and "jamais apparu" in out["text"]


def test_square_hidden_at_the_end():
    out = run_with_fake_capture(0.05)
    assert out["history"][-1][1] is None


def test_summary_verdicts():
    assert "rapide" in summarize([40, 50, 60], 0)
    assert "TRÈS en retard" in summarize([4800, 5100, 4900], 0)
    assert "2 changement(s) jamais vu(s)" in summarize([100], 2)
