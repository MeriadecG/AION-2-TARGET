"""Recherche réseau testée sans internet (faux transport httpx)."""
import json
import threading
import time

import httpx
import pytest

from aion_target.cache import PlayerCache
from aion_target.lookup import PlayerLookup
from aion_target.shugo import CharacterInfo, NetworkError, ShugoClient, parse_official_search

PAGE = ("<title>{n} - Level 45 Templar (3,100 GS) | Shugo.GG</title>"
        "<p>Level 45 · Templar · Gear score 3,100 · Combat power 150,000</p>")

# Réponse réelle de l'API officielle (copiée depuis le navigateur)
OFFICIAL = {"list": [{"characterId": "AuRz…", "name": "<strong>corgoth</strong>", "race": 2,
                      "pcId": 11, "level": 15, "serverId": 2202, "serverName": "Zikel",
                      "region": "naw"}],
            "pagination": {"page": 1, "size": 30, "total": 1, "endPage": 1}}


def make_client(players: dict[str, str], official: dict | None = None, *, delay=0.0,
                search_fails=False, servers=("eu-israphel", "eu-siel", "eu-lumiel")):
    """players = {nom: slug serveur}. Enregistre les URL demandées."""
    calls = []

    def handler(req: httpx.Request):
        calls.append(str(req.url))
        if delay:
            time.sleep(delay)
        if req.url.host == "api-search.plaync.com":
            if search_fails:
                return httpx.Response(503)
            kw = req.url.params["keyword"]
            body = official if official is not None else {"list": [
                {"name": f"<strong>{kw}</strong>", "serverName": players[kw].split("-", 1)[1].capitalize()}
            ] if kw in players else []}
            return httpx.Response(200, json=body)
        slug, name = req.url.path.split("/")[-2:]
        if players.get(name) == slug:
            return httpx.Response(200, text=PAGE.format(n=name))
        return httpx.Response(404)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    learned = []
    client = ShugoClient(list(servers), region="eu", fanout=2, http=http, on_new_server=learned.append)
    return client, calls, learned


def test_parse_official_search_real_payload():
    assert parse_official_search(json.dumps(OFFICIAL), "Corgoth", "eu") == "eu-zikel"
    assert parse_official_search(OFFICIAL, "Corgot", "eu") is None      # partiel ≠ exact


def test_player_on_frequent_server_found_by_fanout():
    client, calls, _ = make_client({"Krix": "eu-israphel"})
    assert client.lookup("Krix").server == "eu-israphel"


def test_unknown_server_is_learned_via_official_search():
    client, calls, learned = make_client({"Lyna": "eu-ariel"})
    info = client.lookup("Lyna")
    assert info.server == "eu-ariel"
    assert learned == ["eu-ariel"] and "eu-ariel" in client.servers
    # pas de balayage des serveurs : fanout (2) + recherche officielle + 1 requête ciblée
    assert len(calls) == 4


def test_hint_from_cache_goes_straight_to_server():
    client, calls, _ = make_client({"Lyna": "eu-ariel"})
    assert client.lookup("Lyna", hint="eu-ariel").server == "eu-ariel"
    assert any("/eu-ariel/Lyna" in c for c in calls)


def test_official_api_down_falls_back_to_all_known_servers():
    client, calls, _ = make_client({"Krix": "eu-lumiel"}, search_fails=True)
    assert client.lookup("Krix").server == "eu-lumiel"   # 3e serveur, hors fanout


def test_not_found_anywhere_returns_none():
    client, _, _ = make_client({})
    assert client.lookup("Marchand") is None


def test_total_network_failure_raises():
    def boom(req):
        raise httpx.ConnectError("hors ligne")
    client = ShugoClient(["eu-siel"], http=httpx.Client(transport=httpx.MockTransport(boom)))
    with pytest.raises(NetworkError):
        client.lookup("Krix")


def test_sources_run_in_parallel():
    client, _, _ = make_client({"Lyna": "eu-ariel"}, delay=0.5)
    t0 = time.monotonic()
    client.lookup("Lyna")
    # parallèle : recherche officielle (0,5 s) puis requête ciblée (0,5 s) ≈ 1 s ;
    # en série (2 serveurs + recherche + ciblée) il faudrait ≈ 2 s. Marge pour machine chargée.
    assert time.monotonic() - t0 < 1.6


# --- cache disque ----------------------------------------------------------------

def test_cache_never_expires_with_time(tmp_path):
    now = [1_000_000.0]
    cache = PlayerCache(tmp_path / "p.db", clock=lambda: now[0])
    cache.put(CharacterInfo("Corgoth", "eu-zikel", 3369, 229116, 45, "Templar", "https://x"))
    now[0] += 400 * 24 * 3600                    # plus d'un an plus tard
    cache.purge()
    info, age = cache.get("CORGOTH")
    assert info.combat_power == 229116 and info.url == "https://x"
    assert age == 400 * 24 * 3600               # l'âge sert à savoir s'il faut rafraîchir
    assert cache.server_of("corgoth") == "eu-zikel"


def test_cache_size_limit_evicts_least_recently_updated(tmp_path):
    t = [0.0]
    cache = PlayerCache(tmp_path / "p.db", clock=lambda: t[0], max_bytes=10**9)
    for i in range(3000):
        t[0] += 1
        cache.put(CharacterInfo(f"P{i}", "eu-siel", 1, 1))
    big = cache.size_bytes()
    cache.max_bytes = big // 2
    cache.purge()
    assert cache.size_bytes() <= big // 2
    assert cache.get("P2999")[0] is not None     # le plus récent est gardé
    assert cache.get("P0") == (None, None)       # le plus ancien est parti
    assert 0 < len(cache) < 3000


def test_cache_stats_and_clear(tmp_path):
    cache = PlayerCache(tmp_path / "p.db")
    cache.put(CharacterInfo("Krix", "eu-siel", 1, 2))
    assert len(cache) == 1 and cache.size_bytes() > 0
    cache.clear()
    assert len(cache) == 0


# --- service : cache d'abord, toujours rafraîchi -----------------------------------

def test_shows_cached_score_immediately_then_refreshes(tmp_path):
    client, calls, _ = make_client({"Krix": "eu-israphel"})
    cache = PlayerCache(tmp_path / "p.db")
    cache.put_many([(CharacterInfo("Krix", "eu-israphel", 2000, 100000), time.time() - 86400)])  # score d'hier
    got = threading.Event()
    results = []
    svc = PlayerLookup(client, cache, on_result=lambda n, i: (results.append(i), got.set()))
    assert svc.get("Krix").combat_power == 100000        # instantané, ancien score
    assert got.wait(2)
    assert results[0].combat_power == 150000             # puis score à jour
    assert cache.get("Krix")[0].combat_power == 150000


def test_prefetch_deduplicates_and_respects_freshness(tmp_path):
    client, calls, _ = make_client({"Krix": "eu-israphel"}, delay=0.1)
    svc = PlayerLookup(client, PlayerCache(tmp_path / "p.db"))
    f1 = svc.prefetch("Krix")
    f2 = svc.prefetch("krix")
    assert f1 is f2                                      # une seule recherche en cours
    f1.result(2)
    n = len(calls)
    assert svc.prefetch("Krix") is None                  # frais : pas de nouvelle requête
    assert len(calls) == n


def test_missing_names_not_requested_again(tmp_path):
    client, calls, _ = make_client({})
    svc = PlayerLookup(client, PlayerCache(tmp_path / "p.db"))
    svc.prefetch("Garde").result(2)
    assert svc.prefetch("Garde") is None and svc.known_missing("garde")


def test_cache_creates_missing_folder(tmp_path):
    cache = PlayerCache(tmp_path / "pas" / "encore" / "là" / "players.db")
    cache.put(CharacterInfo("Krix", "eu-siel", 1, 2))
    assert cache.get("krix")[0].combat_power == 2


def test_abyss_server_hint_goes_straight_to_server(tmp_path):
    client, calls, _ = make_client({"leyndell": "eu-israphel"}, servers=("eu-siel", "eu-lumiel", "eu-israphel"))
    client.fanout = 0                                 # rien d'office : seul l'indice compte
    svc = PlayerLookup(client, PlayerCache(tmp_path / "p.db"))
    info = svc.prefetch("leyndell", hint="eu-israphel").result(2)
    assert info.server == "eu-israphel"
    assert calls[0].endswith("/eu-israphel/leyndell") or any("/eu-israphel/leyndell" in c for c in calls)


def test_old_database_without_rank_is_upgraded(tmp_path):
    import sqlite3
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE players (key TEXT PRIMARY KEY, name TEXT NOT NULL, server TEXT NOT NULL, "
                "gs INTEGER, cp INTEGER, level INTEGER, class TEXT, url TEXT, updated REAL NOT NULL)")
    con.execute("INSERT INTO players VALUES ('krix','Krix','eu-siel',1,2,45,'Assassin','',1.0)")
    con.commit(); con.close()
    cache = PlayerCache(db)
    info = cache.get("krix")[0]
    assert info.combat_power == 2 and info.rank is None
    cache.put_many([(CharacterInfo("Krix", "eu-siel", 1, 2, rank=7), 2.0)])
    assert cache.get("krix")[0].rank == 7
