"""Module DPS : client du moteur, historique (combats / rencontres), préchargement, rejeu réel."""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from aion_target.history import History  # noqa: E402
from aion_target.prefetch import Prefetcher, server_slug_from_name  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine" / "target" / "release" / ("aion-engine.exe" if sys.platform == "win32" else "aion-engine")

FIGHT = {
    "id": "f1", "bossName": "Nuakum", "targetId": 77, "startTimeMs": 1_791_800_000_000, "durationMs": 120_000,
    "totalDamage": 300000, "mobCode": 123, "dungeonId": 600091, "serverId": 1304,
    "actors": [{"actorId": 1, "nickname": "Krix", "job": "살성"}, {"actorId": 2, "nickname": "Bob", "job": "치유성"}],
    "details": {"skills": [
        {"actorId": 1, "name": "Frappe", "dmg": 200000, "time": 40, "crit": 10},
        {"actorId": 1, "name": "Entaille", "dmg": 60000, "time": 12, "crit": 2},
        {"actorId": 2, "name": "Châtiment", "dmg": 40000, "time": 20, "crit": 1}],
        "pingHistory": [{"tsMs": 1, "pingMs": 40}, {"tsMs": 2, "pingMs": 60}]},
}


def test_fight_history_roundtrip_and_dedup(tmp_path):
    h = History(tmp_path / "h.db")
    assert h.version == 1
    assert h.add_fight(FIGHT) and not h.add_fight(FIGHT)       # un combat = une fois
    f = h.fights()[0]
    assert (f["boss"], f["ping_ms"], f["total"]) == ("Nuakum", 50, 300000)
    d = h.fight_detail("f1")
    assert [a["name"] for a in d["actors"]] == ["Krix", "Bob"]
    assert d["actors"][0]["damage"] == 260000 and round(d["actors"][0]["dps"]) == 2167
    assert d["skills"][1][0]["name"] == "Frappe"
    assert h.fights("Bob") and not h.fights("Inconnu")
    h.delete_fight("f1")
    assert h.fights() == []


def test_fight_limit(tmp_path):
    h = History(tmp_path / "h.db", max_fights=3)
    for i in range(6):
        h.add_fight({**FIGHT, "id": f"f{i}", "startTimeMs": 1000 + i})
    assert [f["id"] for f in h.fights()] == ["f5", "f4", "f3"]


def test_encounters_identity_confidence_and_favorites(tmp_path):
    now = [1000.0]
    h = History(tmp_path / "h.db", clock=lambda: now[0])
    h.record_encounter("Krix", "eu-siel", source="ocr", confidence="exact", gs=3000, cp=150000, data_at=990)
    h.record_encounter("Krix", "eu-israphel", source="groupe", confidence="exact", gs=2000, cp=90000, data_at=995)
    assert len(h.encounters()) == 2                            # homonymes : deux personnes
    now[0] += 10
    h.record_encounter("Krix", "eu-siel", source="ocr", confidence="exact", gs=3100, cp=155000, data_at=1005)
    e = [x for x in h.encounters() if x.server == "eu-siel"][0]
    assert e.seen_count == 1 and e.cp == 155000               # même rencontre (moins de 60 s)
    now[0] += 120
    h.record_encounter("Krix", "eu-siel", source="ocr", confidence="exact", gs=1, cp=1, data_at=100)
    e = [x for x in h.encounters() if x.server == "eu-siel"][0]
    assert e.seen_count == 2 and e.cp == 155000               # donnée plus ancienne : ignorée
    h.record_encounter("Zed", None, source="ocr", confidence="ambigu", gs=5, cp=5)
    z = h.encounters("Zed")[0]
    assert z.cp is None and z.server == ""                    # ambigu : jamais de score
    h.set_favorite("Zed", "", True)
    assert [x.name for x in h.encounters(favorites_only=True)] == ["Zed"]
    h.delete_encounter("Zed", "")
    assert not h.encounters("Zed")


def test_history_migration_keeps_data(tmp_path):
    db = tmp_path / "h.db"
    h = History(db)
    h.add_fight(FIGHT)
    h.close()
    h2 = History(db)                                           # réouverture : aucune perte
    assert h2.fights()[0]["id"] == "f1" and h2.version == 1


class FakeLookup:
    def __init__(self):
        self.calls = []

        class C:
            limited = False

            def rate_limited(self):
                return self.limited
        self.client = C()

    def prefetch(self, name, hint=None):
        self.calls.append((name, hint))
        return object()


def test_prefetcher_rate_priority_and_server_rule():
    t = [0.0]
    lk = FakeLookup()
    p = Prefetcher(lambda: lk, "eu", ["eu-siel", "eu-israphel"], clock=lambda: t[0], rate_per_min=20)
    p.observe([{"name": "A", "server": "Siel"}, {"name": "B", "server": "Israphel"},
               {"name": "C", "server": None}, {"name": "D", "server": "Inconnu"}])
    assert p.skipped_no_server == 2                            # serveur inconnu : jamais préchargé
    assert p.tick() == ("B", "eu-israphel")                    # le plus récent d'abord
    assert p.tick() is None                                    # débit : 1 requête / 3 s
    t[0] += 3.1
    lk.client.limited = True
    assert p.tick() is None                                    # 403/429 : pause
    lk.client.limited = False
    assert p.tick() == ("A", "eu-siel")
    assert server_slug_from_name("eu", "Siel", ["eu-siel"]) == "eu-siel"


def test_engine_client_parses_protocol(qtbot=None):
    from PyQt6 import QtWidgets
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from aion_target.engine_client import EngineClient, job_fr
    logs, got = [], {}
    c = EngineClient(logs.append)
    c.dps.connect(lambda d: got.setdefault("dps", d))
    c.players.connect(lambda p: got.setdefault("players", p))
    c.status.connect(lambda s: got.setdefault("status", s))
    c.handle_line('{"v":1,"type":"status","capture":"running","game_port":13328}')
    c.handle_line('{"v":1,"type":"dps","rows":[{"name":"Krix","job":"살성","dps":10}],"ping_ms":42}')
    c.handle_line('{"v":1,"type":"players","observed":[{"name":"Bob","server":"Siel"}]}')
    c.handle_line('{"v":2,"type":"dps"}')                     # autre protocole : ignoré
    c.handle_line("pas du json")
    assert got["status"]["game_port"] == 13328 and c.ping_ms == 42
    assert got["players"][0]["name"] == "Bob"
    assert any("protocole 2" in m for m in logs)
    assert job_fr("살성") == "Assassin"


@pytest.mark.skipif(not ENGINE.exists(), reason="moteur non compilé (cargo build --release dans engine/)")
def test_engine_replay_on_real_frames():
    out = subprocess.run([str(ENGINE), "--replay", str(ROOT / "engine" / "tests" / "data" / "replay_dummy.txt"),
                          "--mode", "lastHitByMe"], capture_output=True, text=True, timeout=60)
    msgs = [json.loads(l) for l in out.stdout.splitlines()]
    assert msgs[0]["type"] == "hello" and msgs[0]["v"] == 1
    dps = next(m for m in msgs if m["type"] == "dps")
    row = dps["rows"][0]
    assert (row["id"], row["damage"], row["dps"]) == (2737, 50000.0, 6250.0)
    assert next(m for m in msgs if m["type"] == "replay_done")["segments"] == 5


@pytest.mark.skipif(not ENGINE.exists(), reason="moteur non compilé")
def test_engine_check_and_clean_quit():
    out = subprocess.run([str(ENGINE), "--check"], capture_output=True, text=True, timeout=30)
    assert '"type":"check"' in out.stdout
