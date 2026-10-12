"""Familiers : catalogue sourcé, progression persistante, comptage des morts sans faux positif."""
import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from aion_target.pets import Catalog, KillCounter, PetStore, level_of, parse_levels  # noqa: E402


def test_catalog_is_sourced_and_searchable():
    c = Catalog.load()
    assert len(c.pets) >= 200 and c.meta["sources"] and c.meta["generated"]
    assert all(p.id and p.genus and p.name_en for p in c.pets)
    assert len({p.id for p in c.pets}) == len(c.pets)                    # identifiants uniques
    papis = c.by_id["papis"]
    assert papis.genus == "Fera" and papis.name == "Papis"
    assert any(m["match"] == "exact" for m in papis.mobs)
    assert c.search("assaillant manduri")[0].name_en == "Manduri Fighter"  # nom français de monstre
    assert all(p.genus == "Special" for p in c.search("", "Special"))
    assert c.thresholds()["global"]["levels"] == [5, 30, 105]


def test_levels_can_exceed_target():
    assert level_of(0, [5, 30, 105]) == (0, 5)
    assert level_of(12, [5, 30, 105]) == (1, 30)
    assert level_of(140, [5, 30, 105]) == (3, None)
    assert parse_levels("5, 30 / 105") == [5, 30, 105]
    assert parse_levels("30, 5") is None and parse_levels("a") is None and parse_levels("") is None


def test_store_persists_progress_and_custom(tmp_path):
    s = PetStore(tmp_path / "pets.json")
    s.set_value("papis", "souls", 12)
    s.add("papis", "kills", 140)
    s.add("papis", "souls", -50)                                          # jamais négatif
    s.set_tracked("papis", True)
    pid = s.add_custom("Nouveau", ["Monstre X"])
    r = PetStore(tmp_path / "pets.json")
    assert r.get("papis")["kills"] == 140 and r.get("papis")["souls"] == 0
    assert r.tracked() == ["papis"] and r.data["custom"][0]["id"] == pid
    c = Catalog.load(r.data["custom"])
    assert c.pets_for_mob(None, "monstre x") == [pid]
    r.data["edition"], r.data["custom_levels"] = "custom", [10, 20]
    assert r.levels(c) == [10, 20]


def kill(entity, code, verdict="mine", name=None, **kw):
    return {"type": "kill", "entity": entity, "code": code, "verdict": verdict, "name": name,
            "t": 1_700_000_000_000, "first_hit": 1, **kw}


def test_kill_counter_rules(tmp_path):
    c = Catalog.load()
    papis_code = next(m["ids"][0] for m in c.by_id["papis"].mobs if m["match"] == "exact")
    s = PetStore(tmp_path / "pets.json")
    s.data["auto"] = True
    s.set_tracked("papis", True)
    k = KillCounter(c, s, tmp_path / "kills.jsonl")
    assert k.on_kill(kill(10, papis_code)) == ["papis"]
    assert k.on_kill(kill(10, papis_code)) == []                          # même mort : une seule fois
    assert k.on_kill(kill(11, papis_code, verdict="other",
                          reason="frappé seulement par d'autres")) == []  # pas ma mort
    assert k.on_kill(kill(12, papis_code, verdict="uncertain")) == []
    assert k.on_kill(kill(13, 999999999, name="Inconnu")) == []           # pas un familier
    assert s.get("papis")["kills"] == 1 and s.get("papis")["souls"] == 0  # 1 mort ≠ 1 âme
    s.get("papis")["auto"] = False
    assert k.on_kill(kill(14, papis_code)) == []                          # auto coupé pour ce familier
    assert k.counts == {"counted": 1, "ignored": 3, "uncertain": 1, "duplicate": 1}
    lines = (tmp_path / "kills.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 5 and json.loads(lines[1])["decision"] == "ignorée"
    again = KillCounter(c, s, tmp_path / "kills.jsonl")                   # journal relu après session
    assert len(again.journal) == 5


@pytest.fixture(scope="module")
def app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_pets_tab_manual_and_overlay(app, tmp_path):
    from PyQt6.QtWidgets import QGridLayout
    from aion_target.config import Config
    from aion_target.pets_ui import PetsTab
    from tests.test_engine_hub import hub_with_fake
    hub, cfg = hub_with_fake()
    cfg.dps_warning_ack = True
    store = PetStore(tmp_path / "pets.json")
    c = Catalog.load()
    counter = KillCounter(c, store)

    def card(root, label):
        g = QGridLayout()
        root.addLayout(g)
        return g
    tab = PetsTab(cfg, c, store, counter, hub, card, lambda m: None)
    tab.search.setText("papis")
    assert tab.table.topLevelItemCount() >= 1
    tab._current = "papis"
    tab._show_current()
    tab.spin_souls.setValue(29)
    tab._add("souls", +1)
    tab.chk_track.setChecked(True)
    assert store.get("papis")["souls"] == 30 and store.tracked() == ["papis"]
    assert tab.overlay.lines()[0] == ("Papis", "Nv 2 · âmes 30/105 · morts 0")
    tab.chk_auto.setChecked(True)
    assert "pets" in hub.modes
    code = next(m["ids"][0] for m in c.by_id["papis"].mobs if m["match"] == "exact")
    hub.client.kill.emit(kill(77, code))
    assert store.get("papis")["kills"] == 1
    assert PetStore(tmp_path / "pets.json").get("papis")["kills"] == 1    # sauvegardé tout de suite


def test_diagnostic_report_has_no_sensitive_data(app, monkeypatch):
    from aion_target import diag
    from aion_target.metrics import STATS
    STATS.add("paint_first_cache", 42)
    monkeypatch.setattr(diag, "SECTIONS", list(diag.SECTIONS))
    diag.register("Test", lambda names: [f"/home/x 192.168.1.20 {'Krix' if names else 'anonyme'}",
                                         os.path.expanduser("~") + "/secret"])
    text = diag.build()
    assert "paint_first_cache" in text and "anonyme" in text and "Krix" not in text
    assert "192.168.1.20" not in text and os.path.expanduser("~") + "/secret" not in text
    assert "aucun nom de joueur" in text
