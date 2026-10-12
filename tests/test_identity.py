"""Identité des personnages : homonymes entre serveurs, migration, résultats tardifs, mesures."""
import sqlite3
import time

import httpx
import pytest

from aion_target.cache import PlayerCache
from aion_target.lookup import PlayerLookup
from aion_target.shugo import AmbiguousName, CharacterInfo, ShugoClient
from aion_target.style import Ambiguous, frame_contents
from aion_target.metrics import LatencyStats, percentile


def test_homonyms_on_two_servers_coexist_and_are_never_guessed(tmp_path):
    c = PlayerCache(tmp_path / "p.db")
    c.put(CharacterInfo("Krix", "eu-siel", 3000, 150000))
    c.put(CharacterInfo("Krix", "eu-israphel", 2000, 90000))
    assert len(c) == 2                                       # plus d'écrasement
    assert c.get("krix") == (None, None)                     # ambigu : aucun score
    assert c.get("Krix", "eu-israphel")[0].combat_power == 90000
    assert c.is_ambiguous("KRIX") and c.server_of("Krix") is None
    assert c.all_names() == ["Krix"]


def test_unique_name_still_found_without_server(tmp_path):
    c = PlayerCache(tmp_path / "p.db")
    c.put(CharacterInfo("Corgoth", "eu-zikel", 3369, 229116))
    assert c.get("corgoth")[0].server == "eu-zikel"


def test_v1_database_is_migrated_with_backup(tmp_path):
    db = tmp_path / "players.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE players (key TEXT PRIMARY KEY, name TEXT NOT NULL, server TEXT NOT NULL, "
                "gs INTEGER, cp INTEGER, level INTEGER, class TEXT, url TEXT, updated REAL NOT NULL, rank INTEGER)")
    con.execute("INSERT INTO players VALUES ('krix','Krix','eu-siel',1,2,45,'Assassin','',1.0,7)")
    con.commit()
    con.close()
    c = PlayerCache(db)
    assert (tmp_path / "players.db.bak-v1").exists()        # copie de sécurité
    info = c.get("Krix", "eu-siel")[0]
    assert info.combat_power == 2 and info.rank == 7        # données conservées
    c.put(CharacterInfo("Krix", "eu-israphel", 5, 6))        # l'homonyme ne l'écrase plus
    assert c.get("Krix", "eu-siel")[0].combat_power == 2
    c.close()
    assert PlayerCache(db).get("Krix", "eu-siel")[0].combat_power == 2   # migration idempotente


def _client(official_servers, pages):
    def handler(req: httpx.Request):
        if req.url.host == "api-search.plaync.com":
            return httpx.Response(200, json={"list": [
                {"name": "<strong>Krix</strong>", "serverName": s.split("-", 1)[1].capitalize()}
                for s in official_servers]})
        if "leaderboard" in req.url.path:
            return httpx.Response(200, json={"entries": []})
        slug, name = req.url.path.split("/")[-2:]
        if slug in pages:
            cp = pages[slug]
            return httpx.Response(200, text=f"<title>{name} - Level 45 Templar (3,000 GS) | Shugo.GG</title>"
                                            f"<p>Level 45 · Templar · Gear score 3,000 · Combat power {cp:,}</p>")
        return httpx.Response(404)
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return ShugoClient(["eu-siel", "eu-israphel", "eu-lumiel"], region="eu", fanout=3, http=http)


def test_lookup_without_server_refuses_homonyms():
    client = _client(["eu-siel", "eu-israphel"], {"eu-siel": 150000, "eu-israphel": 90000})
    with pytest.raises(AmbiguousName) as e:
        client.lookup("Krix")
    assert sorted(e.value.servers) == ["eu-israphel", "eu-siel"]


def test_lookup_with_abyss_server_gets_the_right_homonym():
    client = _client(["eu-siel", "eu-israphel"], {"eu-siel": 150000, "eu-israphel": 90000})
    assert client.lookup("Krix", hint="eu-israphel").combat_power == 90000


def test_lookup_unique_confirmed_by_official_search():
    client = _client(["eu-israphel"], {"eu-israphel": 90000})
    assert client.lookup("Krix").server == "eu-israphel"


def test_lookup_service_marks_ambiguity_and_shows_nothing(tmp_path):
    client = _client(["eu-siel", "eu-israphel"], {"eu-siel": 150000, "eu-israphel": 90000})
    results = []
    svc = PlayerLookup(client, PlayerCache(tmp_path / "p.db"), on_result=lambda n, i: results.append(i))
    svc.prefetch("Krix").result(5)
    assert svc.ambiguous("Krix") == ["eu-israphel", "eu-siel"]
    assert svc.cached("Krix") is None and results == [None]
    assert svc.ambiguous("Krix", hint="eu-siel") == []        # en Abysse, la balise tranche
    info = svc.prefetch("Krix", hint="eu-siel").result(5)
    assert info.combat_power == 150000 and svc.cached("Krix", "eu-siel").combat_power == 150000


def test_ambiguous_target_displays_no_score():
    class Cfg:
        show_details, detail_color, gs_color, cp_color, show_top, rank_color = True, "#ccc", "#f00", "#0ff", True, "#f0f"
    out = frame_contents(Ambiguous(["eu-siel", "eu-israphel"]), Cfg())
    assert out["gs"] == [] and out["cp"] == [] and "Homonymes" in out["details"][0][0]


def test_late_result_of_previous_target_or_other_server_is_ignored(tmp_path, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6 import QtWidgets
    from PyQt6.QtCore import Qt
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from aion_target import overlay
    from aion_target.config import Config
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    w = overlay.Worker(Config())

    class LK:
        def ambiguous(self, n, h=None):
            return []

        def cached(self, n, h=None):
            return None
    w.lookup = LK()
    shown = []
    w.status.connect(lambda n, i: shown.append((n, i)), type=Qt.ConnectionType.DirectConnection)
    w.tracker.current = "Bob"
    w._hint = "eu-siel"
    w._on_result("Alice", CharacterInfo("Alice", "eu-siel", 1, 1))           # ancienne cible
    w._on_result("Bob", CharacterInfo("Bob", "eu-israphel", 2, 2))           # homonyme ailleurs
    assert shown == []
    w._on_result("Bob", CharacterInfo("Bob", "eu-siel", 3, 3))
    assert shown and shown[-1][1].combat_power == 3


def test_percentiles():
    vals = sorted(float(i) for i in range(1, 101))
    assert percentile(vals, 50) == pytest.approx(50.5)
    assert percentile(vals, 99) == pytest.approx(99.01)
    s = LatencyStats()
    for v in range(100):
        s.add("first_cache", v)
    assert s.summary("first_cache")["n"] == 100
    assert any("first_cache" in line and "p95" in line for line in s.report())
