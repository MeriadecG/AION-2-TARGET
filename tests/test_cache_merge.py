"""Cache GS/CP : une seule règle de fusion, identique en RAM et dans SQLite."""
import random
import sqlite3
import threading
import time

import pytest

from aion_target.cache import PlayerCache, merge_profile
from aion_target.shugo import CharacterInfo


def ci(name="Ana", server="eu-siel", gs=None, cp=None, level=None, cls=None, url="", rank=None):
    return CharacterInfo(name, server, gs, cp, level, cls, url, rank)


def fields(info):
    return (info.name, info.server, info.gear_score, info.combat_power, info.level, info.char_class,
            info.url, info.rank)


def ram_equals_sqlite(c: PlayerCache, path):
    """Compare l'index RAM au contenu réel de la base (et à une réouverture)."""
    con = sqlite3.connect(path)
    rows = {(r[0].casefold(), r[1]): r for r in con.execute(
        "SELECT name, server, gs, cp, level, class, url, rank, updated FROM players")}
    con.close()
    ram = {}
    with c._ram_lock:
        for k, per in c._ram.items():
            for server, (info, ts) in per.items():
                ram[(k, server)] = (*fields(info), ts)
    db = {k: (r[0], r[1], r[2], r[3], r[4], r[5], r[6] or "", r[7], r[8]) for k, r in rows.items()}
    assert ram == db
    reopened = PlayerCache(path)
    for (k, server), row in db.items():
        info, _ = reopened.get(row[0], server)
        assert fields(info) == row[:8]
    reopened.close()


@pytest.fixture
def cache(tmp_path):
    t = [1000.0]
    c = PlayerCache(tmp_path / "p.db", clock=lambda: t[0])
    c.t = t
    c.path = tmp_path / "p.db"
    yield c
    c.close()


# --- règle pure ---------------------------------------------------------------------------------

def test_merge_absent_never_replaces_known():
    old = ci(gs=3000, cp=90000, level=45, cls="Cleric", url="u", rank=5)
    (m, ts), newer = merge_profile(old, 10.0, ci(cp=95000), 20.0)
    assert newer and ts == 20.0
    assert (m.gear_score, m.combat_power, m.level, m.char_class, m.url, m.rank) == (3000, 95000, 45, "Cleric", "u", 5)


def test_merge_older_data_only_fills_gaps():
    old = ci(cp=90000)
    (m, ts), newer = merge_profile(old, 20.0, ci(gs=2800, cp=1, level=40, rank=9), 10.0)
    assert not newer and ts == 20.0
    assert (m.gear_score, m.combat_power, m.level, m.rank) == (2800, 90000, 40, 9)


def test_merge_zero_is_unknown_not_a_value():
    (m, _), _ = merge_profile(ci(gs=3000, cp=9), 1.0, ci(gs=0, cp=-1), 2.0)
    assert (m.gear_score, m.combat_power) == (3000, 9)
    (m, _), _ = merge_profile(None, None, ci(gs=0), 1.0)
    assert m.gear_score is None                       # inconnu reste identifiable (pas « 0 »)


def test_merge_never_mixes_servers():
    with pytest.raises(ValueError):
        merge_profile(ci(server="eu-siel"), 1.0, ci(server="eu-zikel"), 2.0)


# --- scénarios du cache -----------------------------------------------------------------------------

def test_gs_and_cp(cache):
    cache.put(ci(gs=3000, cp=90000))
    info, age = cache.get("ana", "eu-siel")
    assert (info.gear_score, info.combat_power, age) == (3000, 90000, 0)
    ram_equals_sqlite(cache, cache.path)


def test_cp_only_then_gs_only_keeps_both(cache):
    cache.put(ci(cp=90000))
    info = cache.get("Ana", "eu-siel")[0]
    assert info.gear_score is None and info.combat_power == 90000
    cache.t[0] += 5
    cache.put(ci(gs=3100))
    info = cache.get("Ana", "eu-siel")[0]
    assert (info.gear_score, info.combat_power) == (3100, 90000)
    ram_equals_sqlite(cache, cache.path)


def test_successive_updates_take_latest_values(cache):
    for i in range(5):
        cache.t[0] += 10
        cache.put(ci(gs=3000 + i, cp=90000 + i, level=40 + i))
    info, age = cache.get("Ana", "eu-siel")
    assert (info.gear_score, info.combat_power, info.level, age) == (3004, 90004, 44, 0)
    ram_equals_sqlite(cache, cache.path)


def test_same_clock_tick_live_update_wins(cache):
    cache.put(ci(cp=1))
    cache.put(ci(cp=2))                                # même horodatage : l'observation la plus tardive
    assert cache.get("Ana", "eu-siel")[0].combat_power == 2


def test_homonyms_on_two_servers_never_mix(cache):
    cache.put(ci(server="eu-siel", gs=3000, cp=90000))
    cache.put(ci(server="eu-zikel", gs=1000, cp=10000))
    cache.put_many([(ci(server="eu-zikel", cp=12000, rank=50), 2000.0)])
    assert cache.get("Ana") == (None, None)            # ambigu sans serveur
    assert cache.is_ambiguous("ANA")
    a, b = cache.get("Ana", "eu-siel")[0], cache.get("Ana", "eu-zikel")[0]
    assert (a.gear_score, a.combat_power, a.rank) == (3000, 90000, None)
    assert (b.gear_score, b.combat_power, b.rank) == (1000, 12000, 50)
    ram_equals_sqlite(cache, cache.path)


def test_unknown_server_is_never_stored(cache):
    cache.put(ci(server="", cp=5))
    assert cache.put_many([(ci(server="", cp=5), 1.0)]) == 0
    assert cache.candidates("Ana") == [] and len(cache) == 0


def test_older_ranking_import_does_not_overwrite_recent_observation(cache):
    cache.put(ci(gs=3000, cp=90000, level=45))         # observé en jeu à t=1000
    written = cache.put_many([(ci(gs=2500, cp=70000, level=44, cls="Cleric", rank=12), 500.0)])
    assert written == 0
    info, age = cache.get("Ana", "eu-siel")
    assert (info.gear_score, info.combat_power, info.level) == (3000, 90000, 45)
    assert info.char_class == "Cleric" and info.rank == 12          # trous complétés, rang à jour
    assert age == 0
    ram_equals_sqlite(cache, cache.path)


def test_recent_import_with_empty_fields_keeps_known_values(cache):
    cache.put(ci(gs=3000, cp=90000, level=45, cls="Cleric", url="https://x"))
    cache.t[0] = 5000.0
    written = cache.put_many([(ci(cp=95000, rank=3), 4000.0)])    # GS, niveau, classe absents
    assert written == 1
    info, age = cache.get("Ana", "eu-siel")
    assert (info.gear_score, info.combat_power, info.level, info.char_class, info.url, info.rank) == \
        (3000, 95000, 45, "Cleric", "https://x", 3)
    assert age == 1000.0
    ram_equals_sqlite(cache, cache.path)


def test_restart_and_reload_give_same_profiles(cache):
    cache.put(ci(gs=3000, cp=90000))
    cache.put_many([(ci(name="Bob", cp=5, rank=1), 900.0), (ci(name="Ana", rank=7), 10.0)])
    ram_equals_sqlite(cache, cache.path)
    before = {n: fields(cache.get(n, "eu-siel")[0]) for n in ("Ana", "Bob")}
    cache.reload_ram()
    assert {n: fields(cache.get(n, "eu-siel")[0]) for n in ("Ana", "Bob")} == before


def test_sqlite_failure_never_breaks_display(cache, monkeypatch):
    cache.put(ci(gs=1, cp=1))

    class Broken:
        def __getattr__(self, name):
            return getattr(real, name)

        def executemany(self, *a, **k):
            raise sqlite3.OperationalError("database or disk is full")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    real = cache._db
    cache._db = Broken()
    try:
        cache.put(ci(gs=2, cp=2))
    finally:
        cache._db = real
    assert cache.get("Ana", "eu-siel")[0].combat_power == 2       # affichage servi
    assert "disk is full" in cache.last_error


# --- concurrence --------------------------------------------------------------------------------------

def test_concurrent_import_lookups_party_and_network(tmp_path):
    """Import du classement, résultats de recherche, groupe, observations réseau et lectures de la
    cible en parallèle : aucune valeur connue perdue, RAM == SQLite à la fin, lectures sans attente."""
    path = tmp_path / "p.db"
    c = PlayerCache(path)
    names = [f"Joueur{i}" for i in range(60)]
    servers = ["eu-siel", "eu-zikel"]
    for n in names:                                     # base : GS et CP connus
        for s in servers:
            c.put(ci(n, s, gs=2000, cp=50000, level=40))
    stop = threading.Event()
    errors, slow_reads = [], []

    def importer():                                     # ancien classement, champs partiels
        rnd = random.Random(1)
        while not stop.is_set():
            batch = [(ci(rnd.choice(names), rnd.choice(servers), cp=1, rank=rnd.randint(1, 999)), 1.0)
                     for _ in range(50)]
            c.put_many(batch)

    def network():                                      # observations partielles récentes (sans GS)
        rnd = random.Random(2)
        while not stop.is_set():
            c.put(ci(rnd.choice(names), rnd.choice(servers), cp=rnd.randint(60000, 70000)))

    def party():                                        # groupe : GS et CP envoyés par le jeu
        rnd = random.Random(3)
        while not stop.is_set():
            c.put(ci(rnd.choice(names), rnd.choice(servers), gs=rnd.randint(3000, 3500), cp=rnd.randint(60000, 70000)))

    def target():                                       # la cible : lectures RAM uniquement
        rnd = random.Random(4)
        while not stop.is_set():
            t0 = time.perf_counter()
            info, _ = c.get(rnd.choice(names), rnd.choice(servers))
            slow_reads.append(time.perf_counter() - t0)
            if info is None or info.gear_score is None or info.combat_power is None:
                errors.append(info)

    threads = [threading.Thread(target=f) for f in (importer, network, party, target, target)]
    for t in threads:
        t.start()
    time.sleep(1.5)
    stop.set()
    for t in threads:
        t.join(5)
    assert errors == []                                  # jamais de GS/CP perdu
    assert max(slow_reads) < 0.25                        # la cible n'attend jamais SQLite (marge GIL)
    for n in names:                                      # l'import ancien n'a rien écrasé
        for s in servers:
            info = c.get(n, s)[0]
            assert info.combat_power >= 50000 and info.gear_score >= 2000
    ram_equals_sqlite(c, path)
    c.close()
