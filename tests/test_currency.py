"""Monnaie calculée : lecture de la barre (vraie capture du jeu), calcul, stabilité, cadres."""
import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from aion_target.currency import (CurrencyReader, Stabilizer, compute, fmt_amount,  # noqa: E402
                                  parse_values)
from aion_target.currency_ui import keep_below, rects_overlap  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def box(x0, x1, y0=0, y1=20):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def test_parse_values_simple():
    lines = [("22212", 1, box(250, 300)), ("1300", 1, box(20, 60)),
             ("Q807572", .98, box(70, 140)), ("707572", 1, box(160, 230))]
    assert parse_values(lines) == [1300, 807572, 707572, 22212]


def test_parse_values_merges_split_thousands():
    lines = [("807", 1, box(70, 100)), ("572", 1, box(105, 135)), ("707 572", 1, box(160, 230))]
    assert parse_values(lines) == [807572, 707572]


def test_parse_values_keeps_separate_amounts_apart():
    # « 1300 » puis icône (écart large) puis « 807 » : pas de fusion
    lines = [("1300", 1, box(20, 60)), ("807", 1, box(90, 120))]
    assert parse_values(lines) == [1300, 807]


def test_compute_and_format():
    v = [1300, 807572, 707572, 22212]
    assert compute(v, 2, 3) == 100000
    assert compute(v, 3, 2) == -100000
    assert compute(v, 2, 5) is None and compute([], 1, 2) is None
    assert fmt_amount(100000) == "100 000" and fmt_amount(-5) == "−5" and fmt_amount(None) == "—"


def test_stabilizer_needs_two_identical_reads():
    s = Stabilizer()
    assert not s.push(100000) and s.value is None
    assert s.push(100000) and s.value == 100000
    assert not s.push(None) and s.value == 100000        # barre cachée : on garde
    assert not s.push(99999) and s.value == 100000       # une lecture isolée ne suffit pas
    assert s.push(99999) and s.value == 99999


@pytest.mark.parametrize("name", ["currency_bar.png"])
def test_real_capture(name):
    img = np.asarray(Image.open(FIX / name).convert("RGB"))
    vals = CurrencyReader().read(img)
    assert vals == [1300, 807572, 707572, 22212]
    assert compute(vals, 2, 3) == 100000


def test_real_capture_upper_left_area_of_screen():
    """Zone plus large que nécessaire (coin de l'écran) : les 4 montants restent en tête."""
    img = np.asarray(Image.open(FIX / "currency_bar_wide.png").convert("RGB"))[0:26]
    vals = CurrencyReader().read(img)
    assert vals[:4] == [1300, 807572, 707572, 22212]


def test_display_frame_never_covers_read_zone():
    zone = [40, 0, 310, 26]
    assert rects_overlap([44, 10, 230, 24], zone)
    assert keep_below([44, 10, 230, 24], zone) == [44, 30, 230, 24]
    assert keep_below([44, 40, 230, 24], zone) == [44, 40, 230, 24]


def test_worker_reads_zone_and_emits(tmp_path, monkeypatch):
    from PyQt6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from aion_target import currency
    from aion_target.config import Config
    monkeypatch.setattr(currency, "INTERVAL", 0.05)
    cfg = Config()
    cfg.currency_enabled = True
    img = np.asarray(Image.open(FIX / "currency_bar.png").convert("RGB"))

    class Grab:
        n = 0

        def grab(self, *zone):
            Grab.n += 1
            return img
    w = currency.CurrencyWorker(cfg, Grab(), log=lambda m: None)
    got = []
    w.amount.connect(got.append)
    w.start()
    import time
    t0 = time.monotonic()
    while not got and time.monotonic() - t0 < 20:
        app.processEvents()
        time.sleep(0.02)
    w.stop()
    assert got == [100000] and Grab.n >= 2
