"""Refonte « Tactical Analytics » : fenêtre de contrôle seulement, overlays intacts."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PyQt6.QtWidgets")

from aion_target import theme  # noqa: E402
from aion_target.config import Config  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_palette_matches_spec():
    assert (theme.BG, theme.SURFACE, theme.TITLE, theme.ACCENT, theme.MUTED, theme.GOLD) == \
        ("#ECF2F7", "#FFFFFF", "#163B5D", "#3C9FC9", "#637D95", "#DBA954")
    qss = theme.stylesheet()
    for color in ("#ECF2F7", "#163B5D", "#3C9FC9", "#637D95", "#DBA954"):
        assert color in qss
    assert "#15171c" not in qss                                   # plus de fond noir


def _contrast(a: str, b: str) -> float:
    def lum(h):
        r, g, b_ = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
        f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b_)
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_text_colors_are_readable_on_white():
    for c in (theme.TITLE, theme.TEXT, theme.OK, theme.WARN, theme.ERR, theme.GOLD_TEXT):
        assert _contrast(c, "#FFFFFF") >= 4.5, c                  # états critiques : AA
    assert _contrast(theme.MUTED, "#FFFFFF") >= 4.0               # secondaire imposé (#637D95)


def test_components(app):
    from aion_target.ui_kit import StatTile, StatusChip
    t = StatTile("CP", important=True)
    t.set(None)
    assert t.value.text() == "—"                                  # jamais de donnée inventée
    t.set("229 116", "TOP 23")
    assert t.value.text() == "229 116" and t.property("important") == "true"
    c = StatusChip("Moteur")
    c.set_state("err", "permission manquante")
    assert c.level == "err" and theme.ERR in c.text.text()


def test_control_window_tiles_and_chips(app):
    from aion_target.control import ControlWindow
    from aion_target.overlay import Overlay, Worker
    from aion_target.shugo import CharacterInfo
    from aion_target.style import Ambiguous
    cfg = Config()
    cfg.save = lambda *a: None
    ctl = ControlWindow(cfg, Overlay(cfg), Worker(cfg), None, lambda: None)
    assert "#ECF2F7" in ctl.styleSheet()
    ctl._on_status("Corgoth", CharacterInfo("Corgoth", "eu-israphel", 3369, 229116, 45, "Templar", rank=23))
    assert ctl.t_cp.value.text().replace(" ", " ") == "229 116" and ctl.t_cp.sub.text() == "TOP 23"
    ctl._on_status("Krix", Ambiguous(["eu-siel", "eu-zikel"]))
    assert ctl.t_cp.value.text() == "—" and "homonymes" in ctl.t_target.sub.text()
    ctl.register_status("boom", "Test", lambda: 1 / 0)              # une erreur ne casse rien
    ctl.register_status("ok", "Module", lambda: ("ok", "actif"))
    ctl._refresh_chips()
    assert ctl._chips["ok"][0].level == "ok" and ctl._chips["boom"][0].level == "idle"


def test_overlays_are_untouched_by_the_theme(app):
    """Overlays : fenêtres de premier niveau SANS feuille de style, couleurs d'origine."""
    from aion_target import dps_ui
    from aion_target.currency import CurrencyWorker
    from aion_target.currency_ui import CurrencyOverlay
    from aion_target.dps_ui import DpsOverlay
    from aion_target.overlay import ROLE_COLORS, Overlay
    cfg = Config()
    cfg.save = lambda *a: None
    ov = Overlay(cfg)
    wins = list(ov.windows.values()) + [DpsOverlay(cfg)] + list(CurrencyOverlay(cfg, CurrencyWorker(cfg)).windows.values())
    for w in wins:
        assert w.isWindow() and w.parent() is None and w.styleSheet() == ""
    assert dps_ui.GOLD == "#C9A227"                                # or des overlays inchangé
    assert ROLE_COLORS["gs"].name() == "#f2c14e"
