import pytest

from aion_target.name_filter import clean, is_plausible_player
from aion_target.shugo import parse_character_page
from aion_target.tracker import TargetTracker

# Extrait représentatif d'une vraie page profil shugo.gg (structure HTML supposée).
PAGE = """
<html><head>
<title>gigawhaleYT - Level 45 Chanter (3,274 GS) | Shugo.GG</title>
<meta name="description" content="gigawhaleYT is a Level 45 Asmodians Chanter (3,274 GS)">
</head><body>
<p>gigawhaleYT is an AION 2 character on Israphel (Global, Europe).
Level 45 &middot; Chanter &middot; Asmodians &middot; Legion: Failure &middot;
Gear score <span>3,274</span> &middot; Combat power <b>209,621</b>.</p>
</body></html>
"""


# --- filtre de noms ------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("  gigawhaleYT ", "gigawhaleYT"),
    ("[Bless]", "Bless"),
    ("Lv.45 Krix", "Krix"),
    ("45 Krix", "Krix"),
    ("白菜菜", "白菜菜"),
])
def test_clean(raw, expected):
    assert clean(raw) == expected


@pytest.mark.parametrize("name,ok", [
    ("gigawhaleYT", True),
    ("白菜菜", True),
    ("Мокрая", True),
    ("Sn1per", True),
    ("Ebony Dragon Lord", False),   # boss / PNJ : espaces
    ("", False),
    ("a", False),
    ("12345", False),
    ("x" * 30, False),
    ("Garde-du-pont", False),
])
def test_plausible(name, ok):
    assert is_plausible_player(name) is ok


def test_blacklist():
    assert not is_plausible_player("Shugo", {"shugo"})


# --- parsing shugo -------------------------------------------------------------

def test_parse_page():
    info = parse_character_page(PAGE, "gigawhaleYT", "eu-israphel")
    assert info.gear_score == 3274
    assert info.combat_power == 209621
    assert info.level == 45
    assert info.char_class == "Chanter"


@pytest.mark.parametrize("faction", ["Asmodians", "Asmodian", "Elyos"])
def test_parse_skips_faction_before_class(faction):
    page = f"<p>Krix is a Level 45 {faction} Assassin. Gear score 3,100 · Combat power 150,000</p>"
    info = parse_character_page(page, "Krix", "eu-israphel")
    assert info.char_class == "Assassin" and info.level == 45


def test_parse_title_only_fallback():
    page = "<title>Foo - Level 40 Ranger (2,900 GS) | Shugo.GG</title>"
    info = parse_character_page(page, "Foo", "eu-siel")
    assert info.gear_score == 2900 and info.combat_power is None


def test_parse_not_a_profile():
    assert parse_character_page("<html>Page introuvable</html>", "x", "eu-siel") is None


# --- tracker ------------------------------------------------------------------

def test_tracker_requires_stable_reads():
    t = TargetTracker(stable_reads=2)
    assert t.feed("Krix") is None
    assert t.feed("Krix") == "Krix"
    assert t.feed("Krix") is None           # pas de nouvelle recherche


def test_tracker_ocr_flicker_does_not_trigger():
    t = TargetTracker(stable_reads=2)
    assert t.feed("Krix") is None
    assert t.feed("Kr1x") is None
    assert t.feed("Krix") is None


def test_tracker_clears_when_target_lost():
    t = TargetTracker(stable_reads=2)
    t.feed("Krix"); t.feed("Krix")
    assert t.feed("") is None
    assert t.feed("") == ""
    assert t.current is None


def test_tracker_ignores_npc():
    t = TargetTracker(stable_reads=1)
    assert t.feed("Ebony Dragon Lord") is None


# --- config -------------------------------------------------------------------

def test_config_migration_speeds_up_old_default(tmp_path):
    import json
    from aion_target.config import Config
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"capture": [2722, 1104, 291, 35], "poll_interval_ms": 400}))
    cfg = Config.load(path)
    assert cfg.poll_interval_ms == 100
    assert cfg.capture == [2722, 1104, 291, 35]          # réglage utilisateur conservé
    from aion_target.config import CONFIG_VERSION
    assert json.loads(path.read_text())["version"] == CONFIG_VERSION


def test_config_migration_keeps_custom_interval(tmp_path):
    import json
    from aion_target.config import Config
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"poll_interval_ms": 250}))
    assert Config.load(path).poll_interval_ms == 250


def test_default_frames_sit_below_zone_without_overlap():
    from aion_target.config import Config, rects_overlap
    cfg = Config(capture=[2722, 1104, 291, 35])
    for rect in cfg.frames().values():
        assert not rects_overlap(rect, cfg.capture)
        assert rect[1] >= 1104 + 35


def test_display_frame_cannot_cover_reading_zone():
    from aion_target.config import Config, rects_overlap
    cfg = Config(capture=[100, 100, 300, 40])
    cfg.set_frame("gs", [120, 110, 100, 24])          # posé SUR la zone de lecture
    assert not rects_overlap(cfg.frame_gs, cfg.capture)


def test_v2_config_migrates_to_frames(tmp_path):
    import json
    from aion_target.config import Config
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"version": 2, "capture": [10, 10, 200, 30], "font_size": 14}))
    cfg = Config.load(path)
    assert cfg.frame_gs and cfg.frame_cp and cfg.frame_details
    assert "font_size" not in json.loads(path.read_text())


def test_rank_frame_exists_and_avoids_zone():
    from aion_target.config import Config, rects_overlap
    cfg = Config(capture=[100, 100, 300, 40])
    assert set(cfg.frames()) == {"gs", "cp", "details", "rank"}
    assert not rects_overlap(cfg.frame_rank, cfg.capture)


def test_known_player_validated_on_first_read():
    t = TargetTracker(stable_reads=2)
    assert t.feed("Krix", trusted=True) == "Krix"
    assert t.feed("Krix", trusted=True) is None
    assert t.feed("Garde du pont", trusted=True) is None     # jamais un nom non plausible


def test_perf_report_every_10s():
    import types
    from aion_target.overlay import PerfStats
    p = PerfStats(every=10)
    p.reset(0.0)
    g = types.SimpleNamespace(frames_received=0)
    for i in range(50):
        p.add(20 + i % 5, True, 30.0)
    assert p.report(g, 5.0) is None                     # pas avant 10 s
    g.frames_received = 300
    msg = p.report(g, 10.0)
    assert "5.0 lectures/s" in msg and "OCR médiane" in msg and "lecture seule 100 %" in msg
    assert "âge image lue moy 30 ms" in msg
    assert "capture KDE 30 img/s" in msg


def test_speed_report_keeps_useful_lines():
    from aion_target.overlay import LOG_RING, log, speed_report
    LOG_RING.clear()
    log("OCR : 'Corgoth' (19 ms, lecture seule)")
    log("Ligne sans intérêt")
    log("Affiché Corgoth : 210 ms après le 1er texte lu")
    rep = speed_report()
    assert rep.startswith("Aion Target — rapport de vitesse")
    assert "OCR : 'Corgoth'" in rep and "Affiché Corgoth" in rep and "sans intérêt" not in rep
