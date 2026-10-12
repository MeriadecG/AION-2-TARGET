"""Correction des lectures OCR grâce au dictionnaire des vrais noms (mémoire des joueurs)."""
import pytest

from aion_target.names import NameIndex, levenshtein_le1

REAL = ["Corgoth", "4llin", "gigawhaleYT", "Sn1per", "z079", "leyndell", "Krix", "白菜菜",
        "Мокрая", "Soulea7er", "Tadas"]


@pytest.fixture
def idx():
    i = NameIndex()
    for n in REAL:
        i.add(n)
    return i


@pytest.mark.parametrize("ocr,expected", [
    ("Corgoth", "Corgoth"),
    ("corgoth", "Corgoth"),               # casse
    ("45Corgoth", "Corgoth"),             # niveau collé
    ("45 Corgoth", "Corgoth"),
    ("4SCorgoth", "Corgoth"),             # 5 lu comme S
    ("450Corgoth", "Corgoth"),            # cercle du niveau lu comme 0
    ("(45)Corgoth", "Corgoth"),
    ("45.Corgoth", "Corgoth"),
    ("454llin", "4llin"),                 # vrai nom qui commence par un chiffre
    ("4llin", "4llin"),
    ("Sn1per", "Sn1per"),                 # S en tête : pas un niveau
    ("45Sn1per", "Sn1per"),
    ("z079", "z079"),
    ("Corgotn", "Corgoth"),               # une lettre mal lue
    ("45Corgotn", "Corgoth"),
    ("Corgoh", "Corgoth"),                # une lettre manquante
    ("Corggoth", "Corgoth"),              # une lettre en trop
    ("gigawhaIeYT", "gigawhaleYT"),       # l lu comme I
    ("白菜菜", "白菜菜"),
    ("45Мокрая", "Мокрая"),
])
def test_resolves_real_names(idx, ocr, expected):
    assert idx.resolve(ocr) == expected


@pytest.mark.parametrize("ocr", [
    "", "45", "12m", "Marchand d'alchimie", "45Marchand d'alchimie12m",
    "Krax",                               # nom court : pas de correction approximative
    "Inconnu",
])
def test_no_false_match(idx, ocr):
    assert idx.resolve(ocr) is None


def test_ambiguous_fuzzy_match_is_refused():
    i = NameIndex()
    for n in ("Corgoth", "Corgath"):
        i.add(n)
    assert i.resolve("Corgxth") is None   # à 1 lettre des deux : on ne devine pas
    assert i.resolve("Corgoth") == "Corgoth"


def test_resolve_from_raw_line(idx):
    assert idx.resolve_line("45 Corgoth2m") == "Corgoth"
    assert idx.resolve_line("leyndell [Isr]18 m") == "leyndell"
    assert idx.resolve_line("45Marchandd'alchimie12m") is None


def test_add_updates_index(idx):
    assert idx.resolve("Nouveau1") is None
    idx.add("Nouveau1")
    assert idx.resolve("45Nouveau1") == "Nouveau1"
    assert len(idx) == len(REAL) + 1


@pytest.mark.parametrize("a,b,ok", [("abc", "abc", True), ("abc", "abd", True), ("abc", "ab", True),
                                     ("abc", "abcd", True), ("abc", "xyz", False), ("abc", "a", False)])
def test_levenshtein_le1(a, b, ok):
    assert levenshtein_le1(a, b) is ok


def test_build_21000_names_quickly():
    import random
    import string
    import time
    rnd = random.Random(1)
    names = {"".join(rnd.choices(string.ascii_letters + string.digits, k=rnd.randint(4, 12)))
             for _ in range(21000)}
    t0 = time.perf_counter()
    i = NameIndex(names)
    assert time.perf_counter() - t0 < 3.0
    some = next(iter(names))
    t0 = time.perf_counter()
    for _ in range(200):
        i.resolve("45" + some[:-1] + "x")
    assert (time.perf_counter() - t0) / 200 < 0.005     # < 5 ms par lecture
