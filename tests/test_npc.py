"""Cibles PNJ : cas réels du journal du 12/10/2026 (boss Vakron)."""
from aion_target.names import NameIndex
from aion_target.npc import NpcIndex

PLAYERS = NameIndex(["Lis", "Tiuna", "Corgoth", "akron", "Ursusa"])
NPCS = NpcIndex.load()


def is_player(c):
    return c in PLAYERS


def test_asset_loaded():
    assert len(NPCS) > 3000
    assert NPCS.match("Vakron") == "Vakron"


def test_glued_and_noisy_reads_are_the_npc():
    for read in ("Vakron", "vakron", "54Vakron", "X54Vakron", "Vakron1", "Vakrons", "54 Vakron lis"):
        assert NPCS.match(read, is_player=is_player) == "Vakron", read


def test_one_letter_misread_only_in_fuzzy_mode():
    assert NPCS.match("Vakrom", is_player=is_player) is None
    assert NPCS.match("Vakrom", is_player=is_player, fuzzy=True) == "Vakron"


def test_players_are_not_npcs():
    for read in ("Corgoth", "45 Corgoth", "Tiuna", "Lis", "TINA"):
        assert NPCS.match(read, is_player=is_player, fuzzy=True) is None, read


def test_exact_player_with_npc_name_stays_a_player():
    idx = NameIndex(["Vakron"])
    assert NPCS.match("Vakron", is_player=lambda c: c in idx) is None
    assert NPCS.match("Ursusa", is_player=is_player) is None


def test_names_no_last_word_after_npc_name():
    assert PLAYERS.resolve("54 Vakron lis") is None
    assert PLAYERS.resolve("45 Lis") == "Lis"


def test_names_no_fuzzy_on_short_reads():
    assert PLAYERS.resolve("TINA") is None
    assert PLAYERS.resolve("Tiuma") == "Tiuna"
