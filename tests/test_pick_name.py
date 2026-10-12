from pathlib import Path

import numpy as np
import pytest

from aion_target.name_filter import pick_name

FIXTURE = Path(__file__).parent / "fixtures" / "target_corgoth.png"


def box(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


# Lignes telles que RapidOCR les renvoie sur le cadre de cible (capture réelle).
FRAME_ONE_BLOCK = [
    ("LogHorizon", 1.0, box(157, 0, 244, 16)),
    ("Bénédiction de Verteron", 0.99, box(114, 13, 286, 29)),
    ("45 Corgoth", 1.0, box(145, 29, 232, 52)),
    ("2 m", 0.83, box(322, 33, 348, 47)),
]
FRAME_SPLIT = [
    ("LogHorizon", 1.0, box(156, 0, 244, 16)),
    ("Bénédiction de Verteron", 1.0, box(114, 12, 286, 30)),
    ("45", 1.0, box(148, 32, 168, 47)),
    ("Corgoth", 1.0, box(168, 30, 232, 52)),
    ("2 m", 0.84, box(322, 34, 348, 47)),
]


def test_level_and_name_in_one_block():
    assert pick_name(FRAME_ONE_BLOCK) == "Corgoth"


def test_level_and_name_split():
    assert pick_name(FRAME_SPLIT) == "Corgoth"


def test_legion_is_not_taken_as_name():
    # La légion a un score égal au nom : l'ancienne règle « meilleur score » aurait pu la prendre.
    assert pick_name(FRAME_SPLIT) != "LogHorizon"


def test_name_only_zone_falls_back_to_best_line():
    assert pick_name([("Corgoth", 0.98, box(0, 0, 60, 18))]) == "Corgoth"


def test_empty():
    assert pick_name([]) == ""


def test_real_capture_with_rapidocr():
    pytest.importorskip("rapidocr")
    from PIL import Image

    from aion_target.ocr import NameReader
    img = np.asarray(Image.open(FIXTURE).convert("RGB"))
    assert NameReader().read(img) == "Corgoth"


def test_unchanged_frame_skips_ocr():
    from aion_target.ocr import frames_similar
    a = np.zeros((30, 200, 3), np.uint8)
    b = a.copy(); b[0, 0] = 255               # un pixel : bruit
    c = a.copy(); c[5:25, 20:120] = 255       # nouveau nom
    assert frames_similar(a, b)
    assert not frames_similar(a, c)
    assert not frames_similar(a, None)


def test_ocr_is_fast_enough():
    pytest.importorskip("rapidocr")
    import time
    from PIL import Image
    from aion_target.ocr import NameReader
    img = np.asarray(Image.open(FIXTURE).convert("RGB"))
    r = NameReader()
    r._ocr(img)
    t0 = time.perf_counter()
    assert r._ocr(img) == "Corgoth"
    assert time.perf_counter() - t0 < 1.0     # ~0,15 s mesuré ; ~1,8 s avant réglage


@pytest.mark.parametrize("lines,expected", [
    ([("45Corgoth", 0.91, box(0, 0, 90, 20)), ("2m€", 0.97, box(250, 0, 280, 20))], "Corgoth"),
    ([("45Corgoth2m", 0.94, box(0, 0, 280, 20))], "Corgoth"),
    ([("45 Corgoth 2 m", 0.94, box(0, 0, 280, 20))], "Corgoth"),
    ([("2m€", 0.97, box(250, 0, 280, 20)), ("Corgoth", 0.90, box(0, 0, 90, 20))], "Corgoth"),
    ([("45 Sn1per", 0.95, box(0, 0, 90, 20))], "Sn1per"),
])
def test_glued_level_name_and_distance(lines, expected):
    assert pick_name(lines) == expected


def test_single_line_35px_zone_like_user_config():
    pytest.importorskip("rapidocr")
    from PIL import Image
    from aion_target.ocr import NameReader
    img = np.asarray(Image.open(FIXTURE).convert("RGB"))
    assert NameReader()._ocr(np.ascontiguousarray(img[24:59, 60:360])) == "Corgoth"


# --- PNJ : ne jamais renvoyer de nom ------------------------------------------------

NPC_FIXTURE = Path(__file__).parent / "fixtures" / "target_npc_marchand.png"


@pytest.mark.parametrize("lines", [
    # tel que lu par l'OCR sur la capture « Marchand d'alchimie »
    [("45Marchand d'alchimie", 1.0, box(40, 0, 230, 20)), ("12m", 1.0, box(320, 2, 350, 18))],
    [("45Marchand d'alchimie12 m", 0.96, box(0, 0, 380, 22))],
    [("45", 1.0, box(40, 0, 60, 20)), ("Marchand d'alchimie", 1.0, box(62, 0, 230, 20)),
     ("12 m", 0.9, box(320, 2, 350, 18))],
])
def test_npc_with_level_returns_nothing(lines):
    assert pick_name(lines) == ""


@pytest.mark.parametrize("text", ["12m", "12 m", "2m€", "150m"])
def test_distance_is_never_a_name(text):
    assert pick_name([(text, 1.0, box(0, 0, 40, 20))]) == ""


def test_real_npc_capture_reads_nothing():
    pytest.importorskip("rapidocr")
    from PIL import Image
    from aion_target.ocr import NameReader
    img = np.asarray(Image.open(NPC_FIXTURE).convert("RGB"))
    assert NameReader()._ocr(img) == ""


# --- Abysse : « Nom [Srv] », pas de niveau, légion et rang au-dessus -----------------

ABYSS_FIXTURE = Path(__file__).parent / "fixtures" / "target_abyss_leyndell.png"
ABYSS_LINES = [   # tel que lu par l'OCR sur la capture réelle
    ("Soldat de 6e classe", 1.0, box(115, 13, 285, 29)),
    ("SteelRose", 1.0, box(145, 4, 250, 18)),          # LÉGION : ne doit jamais être pris
    ("leyndell [Isr]", 1.0, box(121, 33, 245, 52)),
    ("18m", 1.0, box(286, 35, 312, 50)),
]


def test_abyss_name_with_server_tag_not_legion():
    from aion_target.name_filter import parse_target
    assert parse_target(ABYSS_LINES) == ("leyndell", "Isr")
    assert pick_name(ABYSS_LINES) == "leyndell"


@pytest.mark.parametrize("text,expected", [
    (" leyndell [Isr]", ("leyndell", "Isr")),
    ("leyndell[Isr]18m", ("leyndell", "Isr")),
    ("leyndell (lsr]", ("leyndell", "lsr")),        # OCR : ( au lieu de [, l au lieu de I
    ("Krix [Zik] 4 m", ("Krix", "Zik")),
])
def test_server_tag_variants(text, expected):
    from aion_target.name_filter import parse_target
    assert parse_target([(text, 0.95, box(0, 0, 200, 20))]) == expected


def test_without_tag_name_is_on_distance_row_not_legion():
    lines = [("SteelRose", 1.0, box(145, 4, 250, 18)),
             ("leyndell", 1.0, box(121, 33, 200, 52)), ("18m", 1.0, box(286, 35, 312, 50))]
    assert pick_name(lines) == "leyndell"


def test_without_anchor_lowest_line_wins_over_legion():
    lines = [("SteelRose", 1.0, box(145, 4, 250, 18)), ("leyndell", 0.9, box(121, 33, 200, 52))]
    assert pick_name(lines) == "leyndell"


def test_real_abyss_capture():
    pytest.importorskip("rapidocr")
    from PIL import Image
    from aion_target.ocr import NameReader
    img = np.asarray(Image.open(ABYSS_FIXTURE).convert("RGB"))
    r = NameReader()
    assert r._ocr(img) == "leyndell" and r.last_tag == "Isr"
    line = np.ascontiguousarray(img[30:54, 60:380])           # zone réduite à la ligne du nom
    assert r._ocr(line) == "leyndell" and r.last_tag == "Isr"


@pytest.mark.parametrize("tag,expected", [("Isr", "eu-israphel"), ("lsr", "eu-israphel"),
                                          ("Lum", "eu-lumiel"), ("Zik", "eu-zikel"), ("Xyz", None)])
def test_server_tag_to_slug(tag, expected):
    from aion_target.name_filter import server_from_tag
    servers = ["eu-israphel", "eu-lumiel", "eu-zikel", "eu-siel"]
    assert server_from_tag(tag, servers) == expected


# --- vitesse : zone d'une seule ligne lue sans détection -----------------------------

@pytest.mark.parametrize("fixture,crop,expected", [
    ("target_corgoth.png", (24, 59, 60, 360), ("Corgoth", None)),
    ("target_abyss_leyndell.png", (30, 54, 60, 380), ("leyndell", "Isr")),
    ("target_npc_marchand.png", (0, 26, 0, 382), ("", None)),
])
def test_single_line_zone_uses_fast_recognition_only(fixture, crop, expected):
    pytest.importorskip("rapidocr")
    from PIL import Image
    from aion_target.ocr import NameReader
    img = np.asarray(Image.open(Path(__file__).parent / "fixtures" / fixture).convert("RGB"))
    y0, y1, x0, x1 = crop
    r = NameReader()
    assert (r._ocr(np.ascontiguousarray(img[y0:y1, x0:x1])), r.last_tag) == expected
    # joueur : lecture seule directe ; PNJ : la détection confirme (une seule fois, mémorisé)
    assert r.last_mode == ("lecture seule" if expected[0] else "lecture seule → détection")


def test_tall_zone_still_uses_detection():
    pytest.importorskip("rapidocr")
    from PIL import Image
    from aion_target.ocr import NameReader
    r = NameReader()
    assert r._ocr(np.asarray(Image.open(FIXTURE).convert("RGB"))) == "Corgoth"
    assert r.last_mode == "détection"


def test_empty_single_line_zone_is_fast_and_empty():
    pytest.importorskip("rapidocr")
    import time
    from aion_target.ocr import NameReader
    r = NameReader()
    empty = np.full((35, 300, 3), 30, np.uint8)
    r._ocr(empty)
    t0 = time.perf_counter()
    assert r._ocr(empty) == "" and r.last_mode == "lecture seule"
    assert time.perf_counter() - t0 < 0.15          # pas de repli sur la détection (~250 ms)


def test_single_line_fallback_to_detection_when_no_name_found(monkeypatch):
    """Lecture seule qui renvoie du texte sans nom reconnaissable → la détection tranche."""
    pytest.importorskip("rapidocr")
    from PIL import Image
    from aion_target.ocr import NameReader
    r = NameReader()
    line = np.ascontiguousarray(np.asarray(Image.open(FIXTURE).convert("RGB"))[24:59, 60:360])
    real_engine = r._engine
    calls = []

    def engine(img, **kw):
        calls.append(kw.get("use_det", True))
        if kw.get("use_det") is False:                     # lecture seule « polluée » par une icône
            return type("R", (), {"txts": ("@@45@@Corgoth##",), "scores": (0.9,), "boxes": None})()
        return real_engine(img, **kw)
    r._engine = engine
    assert r._ocr(line) == "Corgoth" and r.last_mode == "lecture seule → détection"
    n = len(calls)
    assert r._ocr(line) == "Corgoth" and r.last_mode == "lecture seule (mémo)"
    assert len(calls) == n + 1                              # pas de nouvelle détection : mémo
