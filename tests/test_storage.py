"""Réglages persistants : écriture atomique, sauvegarde, restauration, validation, migrations."""
import json
import threading

import pytest

from aion_target import storage
from aion_target.config import CONFIG_VERSION, Config, validate_fields


@pytest.fixture(autouse=True)
def _clear_notices():
    storage.pop_notices()
    yield
    storage.pop_notices()


def leftovers(folder):
    return sorted(p.name for p in folder.iterdir() if p.name.endswith(".tmp"))


# --- utilitaire commun ------------------------------------------------------------------------------

def test_normal_save_keeps_previous_valid_version_as_backup(tmp_path):
    p = tmp_path / "x.json"
    storage.atomic_write_json(p, {"a": 1})
    storage.atomic_write_json(p, {"a": 2})
    assert json.loads(p.read_text()) == {"a": 2}
    assert json.loads(storage.backup_path(p).read_text()) == {"a": 1}
    assert leftovers(tmp_path) == []


def test_missing_config_folder_is_created(tmp_path):
    p = tmp_path / "a" / "b" / "x.json"
    storage.atomic_write_json(p, {"ok": True})
    assert storage.load_json(p).data == {"ok": True}


def test_crash_during_write_leaves_previous_file_intact(tmp_path, monkeypatch):
    p = tmp_path / "x.json"
    storage.atomic_write_json(p, {"a": 1})

    def crash(fd):
        raise OSError("coupure de courant simulée")
    monkeypatch.setattr(storage.os, "fsync", crash)
    with pytest.raises(OSError):
        storage.atomic_write_json(p, {"a": 2}, backup=False)
    assert json.loads(p.read_text()) == {"a": 1}
    assert leftovers(tmp_path) == []                    # fichier temporaire nettoyé


def test_unserializable_data_is_never_written(tmp_path):
    p = tmp_path / "x.json"
    storage.atomic_write_json(p, {"a": 1})
    with pytest.raises(TypeError):
        storage.atomic_write_json(p, {"a": object()})
    assert json.loads(p.read_text()) == {"a": 1}


def test_incomplete_json_restores_backup_and_quarantines(tmp_path):
    p = tmp_path / "x.json"
    storage.atomic_write_json(p, {"a": 1})
    storage.atomic_write_json(p, {"a": 2})
    p.write_text('{"a": 3, "b": [')                    # fichier coupé
    res = storage.load_json(p, label="Test")
    assert res.source == "backup" and res.data == {"a": 1}
    assert json.loads(p.read_text()) == {"a": 1}        # restauré sur le disque
    assert res.quarantined and res.quarantined.read_text() == '{"a": 3, "b": ['   # jamais supprimé
    assert any("restaurée" in n for n in storage.pop_notices())


def test_corrupt_backup_falls_back_to_defaults_without_destroying_anything(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("{pas du json")
    storage.backup_path(p).write_text("[1, 2")
    res = storage.load_json(p, label="Test")
    assert res.data is None and res.source == "default" and res.writable
    assert res.quarantined.read_text() == "{pas du json"
    assert any("pas de sauvegarde valide" in n for n in storage.pop_notices())


def test_unreadable_file_is_not_overwritten(tmp_path, monkeypatch):
    p = tmp_path / "x.json"
    p.write_text('{"a": 1}')
    real = type(p).read_bytes

    def denied(self):
        if self == p:
            raise PermissionError("accès refusé")
        return real(self)
    monkeypatch.setattr(type(p), "read_bytes", denied)
    res = storage.load_json(p)
    assert res.source == "unreadable" and not res.writable
    assert p.read_text() == '{"a": 1}'


def test_missing_file_is_simply_missing(tmp_path):
    res = storage.load_json(tmp_path / "absent.json")
    assert res.data is None and res.source == "missing" and storage.pop_notices() == []


def test_rapid_successive_saves_from_several_threads(tmp_path):
    p = tmp_path / "x.json"
    errors = []

    def writer(k):
        for i in range(40):
            try:
                storage.atomic_write_json(p, {"writer": k, "i": i, "pad": "x" * 2000})
            except Exception as e:                    # noqa: BLE001
                errors.append(e)
    threads = [threading.Thread(target=writer, args=(k,)) for k in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    data = json.loads(p.read_text())                     # toujours un JSON complet
    assert data["i"] in range(40) and len(data["pad"]) == 2000
    json.loads(storage.backup_path(p).read_text())
    assert leftovers(tmp_path) == []


# --- Config --------------------------------------------------------------------------------------

def test_config_normal_save_and_reload(tmp_path):
    p = tmp_path / "config.json"
    cfg = Config.load(p)
    cfg.dps_overlay_rect = [-1900, -200, 300, 170]        # 2e écran à gauche / au-dessus : valide
    cfg.pets_overlay_opacity = 0.4
    cfg.save(p)
    again = Config.load(p)
    assert again.dps_overlay_rect == [-1900, -200, 300, 170] and again.pets_overlay_opacity == 0.4
    assert storage.pop_notices() == []


def test_config_corrupt_main_restores_backup_not_defaults(tmp_path):
    p = tmp_path / "config.json"
    cfg = Config.load(p)
    cfg.capture = [100, 200, 300, 40]
    cfg.save(p)
    cfg.ui_theme = "dark"
    cfg.save(p)                                          # .bak = capture réglée, thème clair
    p.write_text(p.read_text()[:120])                    # arrêt brutal pendant une écriture d'un autre outil
    again = Config.load(p)
    assert again.capture == [100, 200, 300, 40]          # préférences gardées, pas les défauts
    assert any("restaurée" in n for n in storage.pop_notices())


def test_config_valid_json_with_wrong_types_keeps_valid_fields(tmp_path):
    p = tmp_path / "config.json"
    data = {"version": CONFIG_VERSION, "capture": [10, 20, 300, 30], "frame_gs": [10, 60, 130, 24],
            "dps_overlay_opacity": "beaucoup", "poll_interval_ms": -5, "ui_theme": "fluo",
            "gs_color": "rouge", "servers": "eu-siel", "dps_overlay_rect": [1, 2, 0, 5],
            "auto_update": "oui", "pets_overlay_rect": [40, 440, 300, 90]}
    p.write_text(json.dumps(data))
    cfg = Config.load(p)
    d = Config()
    assert cfg.capture == [10, 20, 300, 30] and cfg.frame_gs == [10, 60, 130, 24]   # positions gardées
    assert cfg.pets_overlay_rect == [40, 440, 300, 90]
    assert cfg.dps_overlay_opacity == d.dps_overlay_opacity
    assert cfg.poll_interval_ms == d.poll_interval_ms and cfg.ui_theme == d.ui_theme
    assert cfg.gs_color == d.gs_color and cfg.servers == d.servers
    assert cfg.dps_overlay_rect == d.dps_overlay_rect and cfg.auto_update is True
    notices = " ".join(storage.pop_notices())
    for name in ("dps_overlay_opacity", "poll_interval_ms", "ui_theme", "gs_color", "servers"):
        assert name in notices
    assert json.loads(p.read_text())["dps_overlay_opacity"] == d.dps_overlay_opacity   # corrigé sur disque


def test_config_top_level_not_an_object_uses_backup(tmp_path):
    p = tmp_path / "config.json"
    cfg = Config.load(p)
    cfg.region = "na"
    cfg.save(p)
    cfg.save(p)
    p.write_text("[1, 2, 3]")
    assert Config.load(p).region == "na"


def test_config_migration_v1_is_preserved(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"version": 1, "poll_interval_ms": 400, "font_size": 14,
                             "capture": [5, 6, 200, 30], "frame_cp": [5, 60, 150, 24]}))
    cfg = Config.load(p)
    assert cfg.poll_interval_ms == 100 and cfg.version == CONFIG_VERSION
    assert cfg.capture == [5, 6, 200, 30] and cfg.frame_cp == [5, 60, 150, 24]
    assert "font_size" not in json.loads(p.read_text())


def test_unknown_keys_from_a_newer_version_survive_a_save(tmp_path):
    """Retour arrière de mise à jour : les réglages de la version plus récente ne sont pas perdus."""
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"version": CONFIG_VERSION, "reglage_futur": {"x": 1}}))
    cfg = Config.load(p)
    cfg.region = "na"
    cfg.save(p)
    data = json.loads(p.read_text())
    assert data["reglage_futur"] == {"x": 1} and data["region"] == "na"


def test_config_unreadable_file_is_left_untouched(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"version": CONFIG_VERSION, "region": "na"}))
    real = type(p).read_bytes

    def denied(self):
        if self == p:
            raise PermissionError("accès refusé")
        return real(self)
    monkeypatch.setattr(type(p), "read_bytes", denied)
    cfg = Config.load(p)
    cfg.save()                                           # chemin par défaut : ignoré
    assert json.loads(p.read_text())["region"] == "na"


def test_config_missing_dir_is_created(tmp_path):
    p = tmp_path / "nouveau" / "config.json"
    Config.load(p)
    assert p.exists()


def test_validate_fields_accepts_unusual_but_valid_coordinates():
    clean, bad = validate_fields({"capture": [-3840, -1080, 240, 32], "frame_rank": [],
                                  "currency_zone": [5000.0, 10.0, 310.0, 26.0], "currency_last": None})
    assert bad == set()
    assert clean["capture"] == [-3840, -1080, 240, 32] and clean["currency_zone"] == [5000, 10, 310, 26]
    _, bad = validate_fields({"capture": [1, 2, 3], "frame_gs": [1, 2, 3, float("nan")],
                              "currency_last": "12"})
    assert bad == {"capture", "frame_gs", "currency_last"}


def test_rapid_successive_config_saves(tmp_path):
    p = tmp_path / "config.json"
    cfg = Config.load(p)

    def worker(k):
        for i in range(25):
            cfg.control_tab = i % 5
            cfg.save(p)
    threads = [threading.Thread(target=worker, args=(k,)) for k in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert Config.load(p).control_tab in range(5)
    assert leftovers(tmp_path) == []


# --- autres fichiers de l'utilisateur -------------------------------------------------------------

def test_event_prefs_corrupt_file_restores_backup(tmp_path):
    from aion_target.events import EventPrefs, EventState
    p = tmp_path / "events.json"
    prefs = EventPrefs.load(p)
    prefs.set_enabled("boss", True)
    prefs.save()
    prefs.volume = 30
    prefs.save()
    p.write_text('{"enabled": {"boss": tr')
    again = EventPrefs.load(p)
    assert again.is_enabled("boss")                      # pas de remise à zéro silencieuse
    assert storage.pop_notices()
    s = tmp_path / "events_state.json"
    s.write_text('{"sent": {"k": ["pas une date", "sent"]}}')
    st = EventState(s)
    from datetime import datetime, timezone
    st.prune(datetime.now(timezone.utc))                 # entrée illisible : oubliée sans planter
    assert st.sent == {}


def test_pet_store_corrupt_file_is_not_overwritten_by_empty_progress(tmp_path):
    from aion_target.pets import PetStore
    p = tmp_path / "pets.json"
    store = PetStore(p)
    store.get("loup")["kills"] = 12
    store.save()
    store.get("loup")["kills"] = 13
    store.save()
    p.write_text('{"version": 1, "progress": {"loup": {"kil')
    again = PetStore(p)
    assert again.get("loup")["kills"] == 12              # sauvegarde restaurée
    assert any("restaurée" in n for n in storage.pop_notices())
    p.write_text('{"version": 1, "progress": []}')        # JSON valide, mauvais type
    storage.backup_path(p).unlink()
    third = PetStore(p)
    assert third.data["progress"] == {}
    assert any(n.startswith("Progression") for n in storage.pop_notices())
    assert list(tmp_path.glob("pets.json.corrupt-*"))    # l'original est gardé


def test_pet_journal_skips_only_the_cut_line(tmp_path):
    from aion_target.pets import Catalog, KillCounter, PetStore
    j = tmp_path / "journal.jsonl"
    j.write_text('{"a": 1}\n{"b": 2}\n{"c": ')          # dernière ligne coupée (arrêt brutal)
    counter = KillCounter(Catalog.load(), PetStore(tmp_path / "pets.json"), journal_path=j)
    assert list(counter.journal) == [{"a": 1}, {"b": 2}]
