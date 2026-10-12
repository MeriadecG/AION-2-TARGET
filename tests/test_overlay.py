import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PyQt6.QtWidgets")

from aion_target.config import Config, rects_overlap  # noqa: E402
from aion_target.overlay import Overlay  # noqa: E402
from aion_target.shugo import CharacterInfo  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _cfg():
    cfg = Config(capture=[860, 40, 240, 32])
    cfg.save = lambda *a: None
    return cfg


def _geo(w):
    g = w.geometry()
    return [g.x(), g.y(), g.width(), g.height()]


def test_in_game_nothing_covers_the_reading_zone(app):
    cfg = _cfg()
    o = Overlay(cfg)
    assert not o.windows["zone"].isVisible()
    for role in ("gs", "cp", "details"):
        assert not rects_overlap(_geo(o.windows[role]), cfg.capture)


def test_edit_mode_shows_zone_then_saves_frames(app):
    cfg = _cfg()
    o = Overlay(cfg)
    o.set_edit(True)
    assert o.windows["zone"].isVisible()
    o.windows["cp"].move(1000, 300)
    o.set_edit(False)
    assert cfg.frame_cp[:2] == [1000, 300]
    assert not o.windows["zone"].isVisible()


def test_frames_show_scores_and_nothing_for_npc(app):
    o = Overlay(_cfg())
    o.show_status("Krix", CharacterInfo("Krix", "eu-siel", 3100, 150000))
    assert o.contents()["cp"][0][0] == "CP 150 000"
    o.show_status("Garde", "notfound")
    assert all(not v for v in o.contents().values())


def test_threat_dot_needs_my_cp(app):
    o = Overlay(_cfg())
    o.show_status("Krix", CharacterInfo("Krix", "eu-siel", 3100, 150000))
    assert o.dot() is None
    o.set_my_cp(200000)
    assert o.dot() == "weaker"


def test_memory_stats_work_with_empty_cache(app, tmp_path):
    """Un cache vide a une longueur 0 : il ne doit pas être pris pour « pas de cache »."""
    import types
    from aion_target.cache import PlayerCache
    from aion_target.control import ControlWindow
    from aion_target.overlay import Worker
    cfg = _cfg()
    w = Worker(cfg)
    w.lookup = types.SimpleNamespace(cache=PlayerCache(tmp_path / "p.db"))
    ctl = ControlWindow(cfg, Overlay(cfg), w, None, lambda: None)
    ctl._refresh_mem()
    assert ctl.mem_stats.text().startswith("0 joueurs")


def test_overlay_flags_per_platform():
    from PyQt6.QtCore import Qt
    from aion_target.overlay import overlay_flags
    W = Qt.WindowType
    TYPE_MASK = W.WindowType_Mask

    linux_game = overlay_flags("linux", edit=False)
    assert linux_game & TYPE_MASK == W.ToolTip                  # infobulle : au-dessus du plein écran
    assert linux_game & W.X11BypassWindowManagerHint and linux_game & W.WindowTransparentForInput

    win_game = overlay_flags("win32", edit=False)
    assert win_game & TYPE_MASK == W.Tool                       # Windows : outil « toujours devant »
    assert win_game & W.WindowStaysOnTopHint and win_game & W.WindowTransparentForInput

    for plat in ("linux", "win32"):
        edit = overlay_flags(plat, edit=True)
        assert edit & TYPE_MASK == W.Tool and not edit & W.WindowTransparentForInput


def test_loop_survives_an_error_and_keeps_displaying(app, tmp_path, monkeypatch):
    """Une erreur pendant un tour ne doit plus arrêter la lecture (symptôme : plus rien ne s'affiche)."""
    import threading
    import time as _t
    import numpy as np
    from PyQt6.QtCore import Qt
    from aion_target import ocr, overlay
    from aion_target.shugo import CharacterInfo

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    class FakeReader:
        last_tag, last_mode, last_raw = None, "lecture seule", "45 Krix"

        def __init__(self, *a):
            pass

        def read(self, img):
            return "Krix"

        def read_full(self, img):
            return "Krix"
    monkeypatch.setattr(ocr, "NameReader", FakeReader)

    class Grab:
        name = "fake"

        def grab(self, *a):
            return np.zeros((35, 300, 3), np.uint8)

    cfg = _cfg()
    w = overlay.Worker(cfg, Grab())
    w.maybe_import = lambda *a, **k: None
    build = w._build_lookup

    def build_lookup():
        lk = build()
        lk.cache.put(CharacterInfo("Krix", "eu-siel", 3000, 150000))
        return lk
    w._build_lookup = build_lookup
    real_feed, boom = w.tracker.feed, {"n": 0}

    def feed(*a, **k):
        boom["n"] += 1
        if boom["n"] == 1:
            raise KeyError("erreur simulée")
        return real_feed(*a, **k)
    w.tracker.feed = feed
    shown, errors = [], []
    w.status.connect(lambda n, i: (shown if isinstance(i, CharacterInfo) else errors).append((n, i)),
                     type=Qt.ConnectionType.DirectConnection)
    threading.Thread(target=w.run, daemon=True).start()
    deadline = _t.monotonic() + 10
    while not shown and _t.monotonic() < deadline:
        _t.sleep(0.05)
    w.running = False
    assert any(i == "error" for _, i in errors)            # l'erreur est signalée…
    assert shown and shown[0][0] == "Krix"                  # …et la lecture a continué


@pytest.mark.parametrize("fast_read,fast_raw,full_read,expected_how", [
    ("4SCorgoth", "4SCorgoth2m", None, "corrigé"),            # dictionnaire seul
    ("45Corgotn", "45Corgotn2m", None, "corrigé"),            # niveau collé + lettre fausse
    ("", "@@@Corg0th##", "Corgoth", "relecture complète"),    # illisible → relecture complète
])
def test_static_misread_is_fixed_immediately(app, tmp_path, monkeypatch, fast_read, fast_raw,
                                             full_read, expected_how):
    """Image FIGÉE dont la lecture rapide est fausse : avant, le mauvais nom restait collé tant
    que l'image ne changeait pas (≈ 5 s). Maintenant : affichage en moins d'une demi-seconde."""
    import threading
    import time as _t
    import numpy as np
    from PyQt6.QtCore import Qt
    from aion_target import ocr, overlay
    from aion_target.shugo import CharacterInfo

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    calls = {"full": 0}

    class Reader:
        last_tag, last_mode = None, "lecture seule"

        def __init__(self, *a):
            self.last_raw = ""

        def read(self, img):
            self.last_raw, self.last_mode = fast_raw, "lecture seule"
            return fast_read

        def read_full(self, img):
            calls["full"] += 1
            self.last_raw, self.last_mode = full_read or "", "relecture complète"
            return full_read or ""
    monkeypatch.setattr(ocr, "NameReader", Reader)

    class Grab:
        name = "fake"

        def grab(self, *a):
            return np.zeros((35, 300, 3), np.uint8)               # image identique à chaque fois

    cfg = _cfg()
    w = overlay.Worker(cfg, Grab())
    w.maybe_import = lambda *a, **k: None
    build = w._build_lookup

    def build_lookup():
        from aion_target.cache import PlayerCache
        PlayerCache(tmp_path / "aion-target" / "players.db").put(
            CharacterInfo("Corgoth", "eu-zikel", 3369, 229116, 45, "Templar"))
        lk = build()                                              # dictionnaire construit ici
        lk.client.quick_search = lambda n: None
        lk.client.lookup = lambda n, hint=None: None
        return lk
    w._build_lookup = build_lookup
    shown = []
    w.status.connect(lambda n, i: shown.append((_t.monotonic(), n)) if isinstance(i, CharacterInfo) else None,
                     type=Qt.ConnectionType.DirectConnection)
    t_start = []
    orig_loop_build = w._build_lookup

    def mark():
        lk = orig_loop_build()
        t_start.append(_t.monotonic())
        return lk
    w._build_lookup = mark
    threading.Thread(target=w.run, daemon=True).start()
    deadline = _t.monotonic() + 10
    while not shown and _t.monotonic() < deadline:
        _t.sleep(0.02)
    w.running = False
    assert shown and shown[0][1] == "Corgoth"
    assert shown[0][0] - t_start[0] < 0.5                         # immédiat, sans attendre
    assert calls["full"] <= 1                                     # relecture une seule fois (mémo)
