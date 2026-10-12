"""Vitesse : la base shugo.gg (recherche rapide) s'affiche avant la fiche lente (NCSoft)."""
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx

from aion_target.cache import PlayerCache
from aion_target.lookup import PlayerLookup
from aion_target.shugo import CharacterInfo, ShugoClient

PROFILE = ("<title>{n} - Level 45 Templar (3,100 GS) | Shugo.GG</title>"
           "<p>Level 45 · Templar · Gear score 3,100 · Combat power 150,000</p>")


def iso(minutes_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


def make(tmp_path, base: dict, profile_delay=1.0, quick_delay=0.0):
    """base = {nom: (serveur, minutes depuis lastSeen, CP)} : ce que contient la base shugo.gg."""
    calls = []

    def handler(req: httpx.Request):
        url = str(req.url)
        if "/api/leaderboard/" in url:
            calls.append(("rapide", req.url.params["q"]))
            time.sleep(quick_delay)
            q = req.url.params["q"].casefold()
            entries = [{"name": n, "serverName": srv.capitalize(), "gearScore": 3000, "combatPower": cp,
                        "level": 45, "className": "Templar", "rank": 42, "lastSeen": iso(age)}
                       for n, (srv, age, cp) in base.items() if q in n.casefold()]
            return httpx.Response(200, json={"entries": entries, "hasMore": False, "total": len(entries)})
        if req.url.host == "api-search.plaync.com":
            calls.append(("officielle", req.url.params["keyword"]))
            return httpx.Response(200, json={"list": []})
        calls.append(("fiche", url))
        time.sleep(profile_delay)                       # la fiche passe par NCSoft : lente
        slug, name = req.url.path.split("/")[-2:]
        if name in base and f"eu-{base[name][0]}" == slug:
            return httpx.Response(200, text=PROFILE.format(n=name))
        return httpx.Response(404)

    client = ShugoClient(["eu-siel", "eu-zikel"], region="eu", fanout=2,
                         http=httpx.Client(transport=httpx.MockTransport(handler)))
    results = []
    got = threading.Event()

    def on_result(n, info):
        results.append((time.monotonic(), info))
        got.set()
    svc = PlayerLookup(client, PlayerCache(tmp_path / "p.db"), on_result=on_result)
    return svc, calls, results


def test_quick_base_score_shown_long_before_slow_profile(tmp_path):
    svc, calls, results = make(tmp_path, {"Krix": ("zikel", 120, 140000)}, profile_delay=1.0)
    t0 = time.monotonic()
    svc.prefetch("Krix").result(5)
    first_at, first = results[0]
    assert first.combat_power == 140000 and first.rank == 42
    assert first_at - t0 < 0.4                          # affiché sans attendre la fiche
    last_at, last = results[-1]
    assert last.combat_power == 150000                  # puis score frais de la fiche
    assert last.rank == 42                              # rang conservé
    assert last_at - t0 >= 1.0


def test_recent_base_score_skips_slow_profile(tmp_path):
    svc, calls, results = make(tmp_path, {"Krix": ("zikel", 3, 140000)})   # vu il y a 3 min
    svc.prefetch("Krix").result(5)
    assert [k for k, _ in calls] == ["rapide"]          # aucune fiche, aucune recherche officielle
    assert len(results) == 1 and results[0][1].combat_power == 140000


def test_recent_memory_score_needs_no_network(tmp_path):
    svc, calls, _ = make(tmp_path, {})
    svc.cache.put(CharacterInfo("Krix", "eu-zikel", 3000, 140000))       # mis à jour à l'instant
    assert svc.prefetch("Krix") is None and calls == []
    assert svc.get("Krix").combat_power == 140000


def test_unknown_to_base_falls_back_to_profile_then_missing(tmp_path):
    svc, calls, results = make(tmp_path, {}, profile_delay=0.05)
    assert svc.prefetch("Nobody").result(5) is None
    kinds = [k for k, _ in calls]
    assert kinds[0] == "rapide" and "fiche" in kinds and "officielle" in kinds
    assert svc.known_missing("Nobody") and results[-1][1] is None


def test_quick_server_used_as_hint_for_profile(tmp_path):
    svc, calls, _ = make(tmp_path, {"Lyna": ("azphel", 90, 120000)}, profile_delay=0.05)
    svc.prefetch("Lyna").result(5)
    assert any(k == "fiche" and "/eu-azphel/Lyna" in u for k, u in calls)
