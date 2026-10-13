"""Affichage GS/CP rapide : index RAM, priorité de la cible, préchargement, transitions."""
import os
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from aion_target.cache import PlayerCache  # noqa: E402
from aion_target.prefetch import Prefetcher  # noqa: E402
from aion_target.shugo import CharacterInfo, ShugoClient  # noqa: E402
from aion_target.style import fmt_age, frame_contents  # noqa: E402


def ci(name, server="eu-siel", cp=100, **kw):
    return CharacterInfo(name, server, 1, cp, **kw)


# --- index RAM ---------------------------------------------------------------------------

def test_ram_index_serves_reads_without_sqlite(tmp_path):
    c = PlayerCache(tmp_path / "p.db")
    c.put(ci("Krix", cp=10, level=45, char_class="Templar"))
    c.put(ci("Krix", "eu-zikel", cp=20))
    with c._lock:                                  # SQLite verrouillé (import, purge…)
        t0 = time.monotonic()
        assert c.get("krix", "eu-siel")[0].combat_power == 10
        assert c.get("Krix") == (None, None)       # homonymes : jamais de score
        assert len(c.candidates("KRIX")) == 2
        assert time.monotonic() - t0 < 0.05        # aucune attente du verrou SQLite
    reopened = PlayerCache(tmp_path / "p.db")      # SQLite reste la source durable
    assert reopened.get("Krix", "eu-zikel")[0].combat_power == 20
    assert sorted(reopened.all_names()) == ["Krix"]


def test_ram_index_follows_sqlite_rules(tmp_path):
    t = [1000.0]
    c = PlayerCache(tmp_path / "p.db", clock=lambda: t[0])
    c.put(ci("Ana", cp=50, level=45, char_class="Cleric", rank=12))
    c.put_many([(ci("Ana", cp=1), 10.0)])          # import plus ancien : ignoré…
    info, age = c.get("Ana", "eu-siel")
    assert info.combat_power == 50 and age == 0 and info.updated == 1000.0
    c.put(CharacterInfo("Ana", "eu-siel", 2, 60))  # écriture partielle (groupe) : rien de perdu
    info = c.get("Ana", "eu-siel")[0]
    assert (info.combat_power, info.level, info.char_class, info.rank) == (60, 45, "Cleric", 12)
    t[0] = 2000.0
    c.put_many([(ci("Ana", cp=70, rank=3), 1500.0)])
    info, age = c.get("Ana", "eu-siel")
    assert (info.combat_power, info.rank, age) == (70, 3, 500.0)
    db = PlayerCache(tmp_path / "p.db", clock=lambda: t[0]).get("Ana", "eu-siel")[0]
    assert (db.combat_power, db.level, db.char_class, db.rank) == (70, info.level, info.char_class, 3)
    c.clear()
    assert c.get("Ana", "eu-siel") == (None, None) and c.all_names() == []


# --- fraîcheur ---------------------------------------------------------------------------

def test_freshness_indicator():
    from aion_target.config import Config
    assert fmt_age(30) is None and fmt_age(25 * 60) == "il y a 25 min"
    assert fmt_age(5 * 3600) == "il y a 5 h" and fmt_age(4 * 86400) == "il y a 4 j"
    cfg = Config()
    old = ci("Bob", level=45, char_class="Gladiator", updated=time.time() - 3 * 86400)
    assert "il y a 3 j" in frame_contents(old, cfg)["details"][0][0]
    fresh = ci("Bob", level=45, updated=time.time() - 5)
    assert "il y a" not in frame_contents(fresh, cfg)["details"][0][0]
    cfg.show_freshness = False
    assert "il y a" not in frame_contents(old, cfg)["details"][0][0]
    assert old == ci("Bob", level=45, char_class="Gladiator")       # n'entre pas dans l'égalité


# --- refus du site ------------------------------------------------------------------------

def test_retry_after_and_growing_backoff():
    assert ShugoClient.retry_after("120", 0) == 120
    assert ShugoClient.retry_after("1", 0) == ShugoClient.BACKOFF_MIN
    assert ShugoClient.retry_after("Wed, 21 Oct 2015 07:28:00 GMT", 1445412480 - 600) == 600
    assert ShugoClient.retry_after("n'importe quoi", 0) is None

    class Resp:
        def __init__(self, code, ra=None):
            self.status_code, self.headers = code, ({"retry-after": ra} if ra else {})
    c = ShugoClient(["eu-siel"])
    c._watch_limits(Resp(429, "90"))
    assert c.rate_limited() and 80 < c.rate_limited_until - time.time() <= 90
    c.rate_limited_until = 0
    c._watch_limits(Resp(403))
    first = c.rate_limited_until - time.time()
    c._watch_limits(Resp(403))
    assert c.rate_limited_until - time.time() > first * 1.5       # refus répétés : pause plus longue
    c._watch_limits(Resp(200))
    assert c.rate_limited()


# --- lookup : cible prioritaire, index des observés ------------------------------------------

class SlowClient:
    def __init__(self, delay=0.3):
        from concurrent.futures import ThreadPoolExecutor
        self._pool = ThreadPoolExecutor(4)
        self.delay, self.calls = delay, []

    def rate_limited(self):
        return False

    def quick_search_all(self, name):
        self.calls.append(("quick", name))
        time.sleep(self.delay)
        return []

    def lookup(self, name, hint=None):
        self.calls.append(("full", name))
        time.sleep(self.delay)
        return None


def lookup_with(tmp_path, delay=0.3, rows=()):
    from aion_target.lookup import PlayerLookup
    cache = PlayerCache(tmp_path / "p.db")
    for r in rows:
        cache.put(r)
    return PlayerLookup(SlowClient(delay), cache)


def test_background_prefetch_waits_for_the_target(tmp_path):
    lk = lookup_with(tmp_path)
    fut = lk.prefetch("Cible", "eu-siel")                       # recherche de la cible
    assert lk.target_busy()
    assert lk.prefetch("Passant", "eu-siel", background=True) is None
    fut.result(5)
    assert not lk.target_busy()
    assert lk.prefetch("Passant", "eu-siel", background=True) is not None


def test_target_reuses_inflight_prefetch_for_same_name(tmp_path):
    lk = lookup_with(tmp_path, delay=0.3)
    bg = lk.prefetch("Passant", "eu-siel", background=True)
    assert lk.prefetch("Passant") is bg                          # pas de seconde requête
    assert lk.target_busy()                                      # devenue prioritaire
    bg.result(5)
    assert [c for c in lk.client.calls if c[0] == "quick"] == [("quick", "Passant")]


def test_observed_names_first_but_never_an_uncertain_match(tmp_path):
    lk = lookup_with(tmp_path, rows=[ci("Corgoth"), ci("Mirabel")])
    assert lk.resolve("Lunaria") is None
    lk.observe_name("Lunaria")                                   # vu par le réseau, hors classement
    assert lk.resolve("45Lunaria") == "Lunaria"
    assert lk.resolve("Lunarja") == "Lunaria"                    # une lettre, nom observé
    lk.observe_name("Mirabol")
    assert lk.resolve("Mirabel") == "Mirabel"                    # exact d'abord
    assert lk.resolve("Mirabal") is None                         # deux candidats : refusé


# --- préchargement -----------------------------------------------------------------------

def test_prefetch_local_first_then_priorities(tmp_path):
    t = [0.0]
    lk = lookup_with(tmp_path, delay=0.01)
    lk.cache.put(ci("Recent"))
    lk.cache.put_many([(ci("Vieux"), time.time() - 86400)])
    lk.cache.put(ci("Homo", "eu-siel"))
    lk.cache.put(ci("Homo", "eu-zikel"))
    p = Prefetcher(lambda: lk, "eu", ["eu-siel", "eu-zikel"], clock=lambda: t[0], rate_per_min=20)
    p.observe([{"name": "Vieux", "server": "Siel"}, {"name": "Neuf", "server": "Siel"},
               {"name": "Recent", "server": "Siel"}, {"name": "Homo", "server": None},
               {"name": "Partiel", "server": None}])
    s = p.stats
    assert (s["local_recent"], s["local_old"], s["local_missing"]) == (1, 1, 1)   # sans quota
    assert s["no_server"] == 2 and s["ambiguous"] == 1
    assert p.seen["partiel"].server is None and p.seen["partiel"].local == "absent"
    assert ("Recent", "eu-siel") not in p.queue                  # récent en mémoire : rien à faire
    assert "Partiel" in lk.observed and "Recent" in lk.observed  # index prioritaire de l'OCR
    assert p.tick() == ("Neuf", "eu-siel")                       # jamais vu d'abord
    assert p.tick() is None                                      # débit
    t[0] += 3.1
    time.sleep(0.1)
    assert p.tick() == ("Vieux", "eu-siel")
    assert p.stats["sent"] == 2 and not p.queue                  # sans serveur : jamais de requête
    assert "PAS la liste de l'instance" in p.report()


def test_prefetch_keeps_observations_until_lookup_ready(tmp_path):
    holder = {"lk": None}
    p = Prefetcher(lambda: holder["lk"], "eu", ["eu-siel"], rate_per_min=60)
    p.observe([{"name": "Tot", "server": "Siel"}])               # moteur plus rapide que l'appli
    assert not p.queue and p._pending
    holder["lk"] = lookup_with(tmp_path, delay=0.01)
    p.tick()
    assert p.seen["tot"].server == "eu-siel"


def test_prefetch_defers_while_target_searched(tmp_path):
    lk = lookup_with(tmp_path, delay=0.3)
    p = Prefetcher(lambda: lk, "eu", ["eu-siel"], rate_per_min=60)
    p.observe([{"name": "Passant", "server": "Siel"}])
    fut = lk.prefetch("Cible", "eu-siel")
    assert p.tick() is None and p.stats["deferred_target"] == 1
    fut.result(5)
    assert p.tick() == ("Passant", "eu-siel")


# --- transitions de bout en bout (vrais Worker / lookup / overlay, OCR et réseau simulés) ----

@pytest.fixture(scope="module")
def app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_transitions_cached_instant_no_wrong_score(app):
    from aion_target.simulate import Scene, World, run_scenario
    known = [CharacterInfo(n, "eu-siel", 3000, cp, 45) for n, cp in (("Alpha", 1), ("Bravo", 2))]
    rows = [(k, 5 * 86400) for k in known]                       # connus mais anciens
    rows += [(CharacterInfo("Krix", "eu-siel", 1, 10), 60), (CharacterInfo("Krix", "eu-zikel", 1, 20), 60)]
    world = World(quick_ms=400, full_ms=1500)                    # réseau lent
    scenes = [("A", Scene("Alpha", "45 Alpha"), "Alpha", 0.3),
              ("B", Scene("Bravo", "45 Bravo"), "Bravo", 0.3),
              ("A retour", Scene("Alpha", "45 Alpha"), "Alpha", 0.3),
              ("homonymes", Scene("Krix", "45 Krix"), None, 0.5),
              ("PNJ", Scene("Vakron", "54 Vakron"), None, 0.4),
              ("vide", Scene(""), None, 0.3)]
    res = run_scenario(scenes, rows, world)
    for r in res[:3]:
        assert r.paint_ms is not None and r.paint_ms < 300, r     # avant toute réponse réseau
        assert r.first_kind in ("mémoire", "déjà affiché")
    assert all(not r.wrong for r in res), [(r.label, r.wrong) for r in res]
    assert not any(c[1] == "Vakron" for c in world.calls)          # PNJ : aucune requête


def test_network_error_keeps_the_known_score(app):
    from aion_target.simulate import Scene, World, run_scenario
    world = World(quick_ms=50, full_ms=100, fail=True)
    rows = [(CharacterInfo("Alpha", "eu-siel", 3000, 7, 45), 3 * 86400)]
    res = run_scenario([("A", Scene("Alpha", "45 Alpha"), "Alpha", 0.6)], rows, world)
    assert res[0].paint_ms is not None and not res[0].wrong


def test_unknown_observed_player_is_trusted_on_first_read(app):
    from aion_target.metrics import STATS
    from aion_target.simulate import Scene, World, run_scenario
    world = World(quick_ms=30, full_ms=60)
    world.remote["neo"] = [(CharacterInfo("Neo", "eu-siel", 1, 5, 45), time.time())]
    STATS.clear()
    run_scenario([("Neo", Scene("Neo", "45 Neo"), "Neo", 0.8)], [], world, observed=["Neo"])
    assert STATS.summary("confirm")["max"] < 60                  # validé dès la 1re lecture


def test_partial_data_never_erases_gear_score(tmp_path):
    """Bug « GS ? alors que le CP s'affiche » : une donnée partielle (groupe, fiche sans GS)
    écrasait le GS connu par « inconnu »."""
    c = PlayerCache(tmp_path / "p.db")
    c.put(CharacterInfo("Ana", "eu-siel", 3369, 229000, 45, "Cleric"))
    c.put(CharacterInfo("Ana", "eu-siel", None, 231000))                 # CP seul (groupe)
    info = c.get("Ana", "eu-siel")[0]
    assert (info.gear_score, info.combat_power, info.level) == (3369, 231000, 45)
    db = PlayerCache(tmp_path / "p.db").get("Ana", "eu-siel")[0]
    assert (db.gear_score, db.combat_power) == (3369, 231000)


def test_profile_without_gear_score_is_completed(tmp_path):
    lk = lookup_with(tmp_path, delay=0.01)
    lk.cache.put(CharacterInfo("Bob", "eu-siel", None, 150000))          # récent mais incomplet
    assert lk.needs_network("Bob", "eu-siel")
    assert lk.prefetch("Bob", "eu-siel") is not None                    # la fiche complétera le GS
    lk.cache.put(CharacterInfo("Cid", "eu-siel", 3000, 150000))
    assert not lk.needs_network("Cid", "eu-siel") and lk.prefetch("Cid", "eu-siel") is None
