"""Import du classement shugo.gg (toute la région) — testé avec la vraie réponse API."""
import json
from pathlib import Path

import httpx
import pytest

from aion_target.cache import PlayerCache
from aion_target.importer import LeaderboardImporter, parse_page
from aion_target.shugo import CharacterInfo

PAGE1 = json.loads((Path(__file__).parent / "fixtures" / "shugo_leaderboard_eu_p1.json").read_text())


def fake_api(total_pages=4, fail_at=None, status=429):
    """Faux shugo.gg : pages 1..total_pages construites à partir de la vraie page 1."""
    calls = []

    def handler(req: httpx.Request):
        page = int(req.url.params["page"])
        calls.append(page)
        assert req.url.params["subRegion"] == "eu" and req.url.params["region"] == "GLOBAL"
        if fail_at == page:
            return httpx.Response(status)
        entries = [dict(e, name=f"{e['name']}_{page}" if page > 1 else e["name"]) for e in PAGE1["entries"]]
        body = dict(PAGE1, page=page, entries=entries, hasMore=page < total_pages,
                    total=total_pages * 50, totalPages=total_pages)
        return httpx.Response(200, json=body)
    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def make(tmp_path, http, **kw):
    cache = PlayerCache(tmp_path / "p.db")
    state = {}
    learned = []
    imp = LeaderboardImporter(http, cache, state, region="eu", delay=0, on_new_server=learned.append, **kw)
    return imp, cache, state, learned


def test_parse_real_page():
    entries, has_more, total = parse_page(PAGE1, "eu")
    assert total == 21141 and has_more and len(entries) == 50
    info, ts = entries[0]
    assert (info.name, info.server, info.gear_score, info.combat_power, info.level, info.char_class) == \
        ("gigawhaleYT", "eu-israphel", 3369, 229674, 45, "Chanter")
    assert info.url == "https://shugo.gg/character/global/eu-israphel/gigawhaleYT"
    assert ts > 1.7e9                                    # lastSeen → horodatage
    assert any(i.server == "eu-azphel" for i, _ in entries)   # serveur inconnu de ma liste


def test_full_import_fills_cache_and_learns_servers(tmp_path):
    http, calls = fake_api(total_pages=4)
    imp, cache, state, learned = make(tmp_path, http)
    imp.run()
    assert calls == [1, 2, 3, 4]
    assert len(cache) == 200
    assert cache.get("Tonny")[0].server == "eu-azphel"
    assert "eu-azphel" in learned
    assert state["next_page"] == 1 and state["last_full"] > 0


def test_max_players_limits_pages(tmp_path):
    http, calls = fake_api(total_pages=10)
    imp, cache, _, _ = make(tmp_path, http, max_players=100)
    imp.run()
    assert calls == [1, 2] and len(cache) == 100


def test_resume_after_interruption(tmp_path):
    http, calls = fake_api(total_pages=4, fail_at=3)
    imp, cache, state, _ = make(tmp_path, http)
    imp.run()                                            # 429 à la page 3 : on s'arrête poliment
    assert calls == [1, 2, 3] and state["next_page"] == 3 and not state.get("last_full")
    http2, calls2 = fake_api(total_pages=4)
    imp2 = LeaderboardImporter(http2, cache, state, region="eu", delay=0)
    imp2.run()
    assert calls2 == [3, 4]                              # reprise là où ça s'était arrêté


def test_not_due_before_24h(tmp_path):
    http, calls = fake_api(total_pages=2)
    imp, _, state, _ = make(tmp_path, http)
    imp.run()
    assert not imp.due()
    state["last_full"] -= 25 * 3600
    assert imp.due()


def test_never_overwrites_a_fresher_local_score(tmp_path):
    http, _ = fake_api(total_pages=1)
    imp, cache, _, _ = make(tmp_path, http)
    cache.put(CharacterInfo("gigawhaleYT", "eu-israphel", 3400, 240000))   # vu en jeu à l'instant
    imp.run()
    assert cache.get("gigawhaleYT")[0].combat_power == 240000
    assert cache.get("shiguanggg")[0].combat_power == 207414                # les autres sont importés


def test_disabled(tmp_path):
    http, calls = fake_api(total_pages=2)
    imp, cache, _, _ = make(tmp_path, http, max_players=0)
    imp.run()
    assert calls == [] and len(cache) == 0


# --- rang (TOP) ---------------------------------------------------------------------

def test_rank_is_imported(tmp_path):
    http, _ = fake_api(total_pages=1)
    imp, cache, _, _ = make(tmp_path, http)
    imp.run()
    assert cache.get("gigawhaleYT")[0].rank == 1
    assert cache.get("Talldos")[0].rank == 50


def test_profile_refresh_keeps_rank(tmp_path):
    """La fiche shugo.gg ne donne pas le rang : un rafraîchissement ne doit pas l'effacer."""
    http, _ = fake_api(total_pages=1)
    imp, cache, _, _ = make(tmp_path, http)
    imp.run()
    cache.put(CharacterInfo("Krix", "eu-israphel", 2900, 170000))   # rafraîchi en jeu, sans rang
    info = cache.get("Krix")[0]
    assert info.combat_power == 170000 and info.rank == 23


def test_reimport_updates_rank_even_if_local_score_is_fresher(tmp_path):
    http, _ = fake_api(total_pages=1)
    imp, cache, state, _ = make(tmp_path, http)
    cache.put(CharacterInfo("gigawhaleYT", "eu-israphel", 3400, 240000))   # plus récent que l'import
    imp.run()
    info = cache.get("gigawhaleYT")[0]
    assert info.combat_power == 240000 and info.rank == 1


# --- erreurs visibles (l'import ne doit plus échouer en silence) ---------------------

@pytest.mark.parametrize("status,expected", [(403, "HTTP 403"), (429, "HTTP 429"), (503, "HTTP 503")])
def test_http_error_is_recorded(tmp_path, status, expected):
    http, _ = fake_api(total_pages=3, fail_at=1, status=status)
    imp, cache, state, _ = make(tmp_path, http)
    imp.run()
    assert state["last_error"] == expected and len(cache) == 0


def test_network_error_is_recorded(tmp_path):
    def boom(req):
        raise httpx.ConnectError("hors ligne")
    imp, _, state, _ = make(tmp_path, httpx.Client(transport=httpx.MockTransport(boom)))
    imp.run()
    assert state["last_error"].startswith("réseau")


def test_success_clears_previous_error(tmp_path):
    http, _ = fake_api(total_pages=1)
    imp, _, state, _ = make(tmp_path, http)
    state["last_error"] = "HTTP 403"
    imp.run()
    assert "last_error" not in state and state["last_full"] > 0


def test_api_requests_send_json_headers(tmp_path):
    seen = {}

    def handler(req):
        seen.update(accept=req.headers.get("accept"), referer=req.headers.get("referer"))
        return httpx.Response(200, json=dict(PAGE1, hasMore=False))
    imp, _, _, _ = make(tmp_path, httpx.Client(transport=httpx.MockTransport(handler)))
    imp.run()
    assert "application/json" in seen["accept"] and seen["referer"].startswith("https://shugo.gg/leaderboard")


@pytest.mark.parametrize("err,needle", [("HTTP 403", "refuse"), ("HTTP 429", "trop de requêtes"),
                                         ("réseau : x", "connexion"), ("HTTP 500", "indisponible")])
def test_error_explanations(err, needle):
    from aion_target.importer import explain_error
    assert needle in explain_error(err)
