from aion_target.config import Config
from aion_target.shugo import CharacterInfo
from aion_target.style import ASSETS, frame_contents, threat

INFO = CharacterInfo("Corgoth", "eu-zikel", 3369, 229116, 45, "Templar")


def test_each_frame_has_its_own_text_and_color():
    cfg = Config(gs_color="#FF0000", cp_color="#00FF00", detail_color="#0000FF")
    c = frame_contents(INFO, cfg)
    assert c["gs"] == [("GS 3 369", "#FF0000")]
    assert c["cp"] == [("CP 229 116", "#00FF00")]
    assert c["details"] == [("Templar · Nv 45 · Zikel", "#0000FF")]


def test_details_optional():
    assert frame_contents(INFO, Config(show_details=False))["details"] == []


def test_nothing_shown_for_npc_or_while_searching():
    for state in (None, "loading", "notfound"):
        assert all(not v for v in frame_contents(state, Config()).values())


def test_threat_dot():
    assert threat(229116, 200000) == "stronger"
    assert threat(150000, 200000) == "weaker"
    assert threat(150000, None) is None           # personnage non renseigné : pas de pastille


def test_bundled_font_present_with_license():
    assert (ASSETS / "Cinzel.ttf").stat().st_size > 50_000
    assert "Open Font License" in (ASSETS / "Cinzel-OFL.txt").read_text()


def test_cache_size_labels():
    from aion_target.control import size_label
    assert size_label(25) == "25 Mo" and size_label(1024) == "1 Go" and size_label(5120) == "5 Go"
    assert size_label(0.4) == "0,4 Mo"


def test_top_frame_only_within_top_1000():
    from dataclasses import replace
    cfg = Config(rank_color="#ABCDEF")
    assert frame_contents(replace(INFO, rank=23), cfg)["rank"] == [("TOP - 23", "#ABCDEF")]
    assert frame_contents(replace(INFO, rank=1000), cfg)["rank"] == [("TOP - 1000", "#ABCDEF")]
    assert frame_contents(replace(INFO, rank=1001), cfg)["rank"] == []
    assert frame_contents(replace(INFO, rank=None), cfg)["rank"] == []
    assert frame_contents(replace(INFO, rank=23), Config(show_top=False))["rank"] == []


def test_read_test_messages():
    from aion_target.control import describe_read_test
    assert describe_read_test("leyndell", "Isr", 80, "x.png").startswith("✓ Lu : « leyndell » · serveur [Isr]")
    assert "NOIRE" in describe_read_test("", None, 0.5, "x.png")
    assert "cadre BLEU" in describe_read_test("", None, 80, "x.png")


def test_read_test_shows_raw_text_and_mode():
    from aion_target.control import describe_read_test
    msg = describe_read_test("Corgoth", None, 80, "x.png", "45 Corgoth2m", "lecture seule")
    assert "Texte brut : « 45 Corgoth2m » (lecture seule)" in msg
