"""Kinah / minute : session, pauses, lectures OCR erronées, sauvegarde, monnaie périmée."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from aion_target.currency import make_reading  # noqa: E402
from aion_target.farm import KinahSession, ValueGate, fmt_elapsed  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def test_two_consistent_readings_and_format():
    g = ValueGate()
    assert g.push(100) is None and g.push(100) == 100
    assert g.push(999999) is None                    # lecture isolée erronée : ignorée
    assert g.push(100) is None and g.push(100) == 100
    assert g.push(-5) is None and g.push("12") is None and g.push(10 ** 13) is None


def test_session_net_rate_and_pauses():
    c = Clock()
    s = KinahSession(c)
    s.start()
    assert s.feed(807572) is False and s.feed(807572) is True        # 2 lectures → solde initial
    c.t += 60
    s.feed(907572)
    s.feed(907572)
    assert s.net() == 100000 and s.per_minute() == pytest.approx(100000)
    s.pause()
    c.t += 3600                                                      # pause : ne compte pas
    assert s.feed(5) is False and s.elapsed() == pytest.approx(60)
    s.resume()
    c.t += 60
    s.feed(857572)
    s.feed(857572)                                                   # achat : variation négative
    assert s.net() == 50000 and s.per_minute() == pytest.approx(25000)
    s.feed(9999999)                                                  # pic isolé (OCR)
    assert s.current == 857572
    s.feed(2_000_000)
    s.feed(2_000_000)                                                # vraie grosse variation
    assert s.current == 2_000_000


def test_rate_waits_for_enough_time_and_negative_ok():
    c = Clock()
    s = KinahSession(c)
    s.start(known=1000)
    c.t += 10
    s.feed(900)
    s.feed(900)
    assert s.net() == -100 and s.per_minute() is None               # < 30 s : pas de cadence
    c.t += 50
    assert s.per_minute() == pytest.approx(-100)


def test_restart_needs_explicit_resume():
    c = Clock()
    s = KinahSession(c)
    s.start(known=500)
    c.t += 120
    s.feed(800)
    s.feed(800)
    saved = s.to_dict()
    c.t += 7200                                                      # appli fermée 2 h
    r = KinahSession.from_dict(saved, c)
    assert r.state == "interrupted" and r.elapsed() == pytest.approx(120)
    assert r.feed(900) is False and r.feed(900) is False             # rien sans « Reprendre »
    r.resume()
    c.t += 60
    r.feed(900)
    r.feed(900)
    assert r.elapsed() == pytest.approx(180) and r.net() == 400
    assert fmt_elapsed(3725) == "1 h 02 min" and fmt_elapsed(65) == "1 min 05 s"


def test_reading_never_mixes_captures():
    rd = make_reading([1300, 807572, 707572, 22212], 2, 3, "barre", 5.0)
    assert (rd["a"], rd["b"], rd["diff"]) == (807572, 707572, 100000)
    partial = make_reading([1300, 807572], 2, 3, "barre", 6.0)       # B absent de CETTE capture
    assert partial["b"] is None and partial["diff"] is None          # jamais l'ancienne valeur de B


def test_worker_falls_back_to_menu_zone():
    from aion_target.config import Config
    from aion_target.currency import CurrencyWorker
    cfg = Config()
    cfg.currency_alt_enabled, cfg.currency_alt_a, cfg.currency_alt_b = True, 1, 2

    class Grab:
        def grab(self, *rect):
            return rect

    class Reader:
        def __init__(self, table):
            self.table = table

        def read(self, img):
            return self.table[tuple(img)]
    w = CurrencyWorker(cfg)
    bar, alt = tuple(cfg.currency_zone), tuple(cfg.currency_zone_alt)
    rd = w.read_once(Grab(), Reader({bar: []}), Reader({alt: [807572, 707572]}))
    assert rd["zone"] == "menu" and rd["diff"] == 100000
    rd = w.read_once(Grab(), Reader({bar: [1, 807572, 707572]}), Reader({alt: [5, 1]}))
    assert rd["zone"] == "barre" and rd["diff"] == 100000


@pytest.fixture(scope="module")
def app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_farm_tab_session_and_overlay(app, tmp_path):
    from PyQt6.QtWidgets import QGridLayout
    from aion_target.config import Config
    from aion_target.currency import CurrencyWorker
    from aion_target.currency_ui import CurrencyOverlay, FarmTab, overlay_text
    cfg = Config()
    cfg.save = lambda *a: None
    w = CurrencyWorker(cfg)
    w.start = lambda: None
    ov = CurrencyOverlay(cfg, w)

    def card(root, label):
        g = QGridLayout()
        root.addLayout(g)
        return g
    tab = FarmTab(cfg, ov, card)
    tab.session.start()
    for v in (100, 100):
        w.reading.emit(make_reading([1, v, 50], 2, 3, "barre", 0.0))
    assert tab.session.current == 100 and "gain net observé" in tab.session_label.text()
    tab._save_session()
    assert cfg.farm_session["state"] == "running" and cfg.farm_session["current"] == 100
    assert overlay_text(["a", "diff", "rate"], {"a": "Total", "diff": "Non lié", "rate": "Kinah"},
                        {"a": 807572, "b": 1, "diff": 100000, "rate": 1500.4}).replace("\u202f", " ") == \
        "Total 807 572  ·  Non lié 100 000  ·  Kinah 1 500/min"
