"""Calendrier des événements : récurrences, fuseaux, changements d'heure, planification des alertes."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from aion_target.events import (EventPlanner, EventPrefs, EventState, load_catalog, next_occurrence,
                                occurrences)

UTC = timezone.utc
PARIS = ZoneInfo("Europe/Paris")


def utc(*a):
    return datetime(*a, tzinfo=UTC)


def paris(dt):
    return dt.astimezone(PARIS).strftime("%a %d/%m %H:%M")


@pytest.fixture(scope="module")
def cat():
    return load_catalog(region="eu", override_path=None)


def ev(cat, eid):
    return cat.by_id[eid]


# --- catalogue -----------------------------------------------------------------------

def test_catalog_loads_with_sources_and_statuses(cat):
    assert len(cat.events) >= 30
    for e in cat.events:
        assert e.id and e.name and e.category in ("hourly", "daily", "weekly", "monthly")
        assert e.status in ("confirmed", "to_verify", "unconfirmed")
        for s in e.sources:
            assert s in cat.sources, f"{e.id}: source inconnue {s}"
        if e.status == "confirmed":
            assert len(e.sources) >= 2, f"{e.id} confirmé avec moins de 2 sources"


def test_unconfirmed_events_have_no_occurrence(cat):
    for eid in ("arena_tactics", "world_bosses", "maintenance", "odyle_energy", "monthly_shops"):
        assert next_occurrence(ev(cat, eid), utc(2026, 10, 12, 8)) is None


def test_override_file_merges_by_id(tmp_path):
    import json
    p = tmp_path / "events_catalog.json"
    p.write_text(json.dumps({"events": [
        {"id": "arena_tactics", "status": "confirmed", "rule": {"type": "daily", "times": ["11:00", "19:00"]},
         "sources": ["metabot_events", "aion2meta"]},
        {"id": "my_custom", "name": "Mon événement", "category": "daily", "kind": "event", "tz": "server",
         "rule": {"type": "daily", "times": ["20:00"]}, "status": "confirmed", "sources": ["shugo", "aion2meta"]}]}))
    c = load_catalog("eu", p)
    assert c.by_id["arena_tactics"].status == "confirmed"
    assert c.by_id["arena_tactics"].name == "Arène des tactiques (10 contre 10)"     # champs conservés
    assert paris(next_occurrence(c.by_id["arena_tactics"], utc(2026, 10, 12, 8))) == "Mon 12/10 11:00"
    assert "my_custom" in c.by_id


# --- récurrences ---------------------------------------------------------------------

def test_hourly_shugo_and_invasion(cat):
    now = utc(2026, 10, 12, 8, 5)                                   # 10:05 à Paris
    assert paris(next_occurrence(ev(cat, "shugo_festival"), now)) == "Mon 12/10 11:00"
    assert paris(next_occurrence(ev(cat, "dimensional_invasion"), now)) == "Mon 12/10 10:30"


def test_every_3_hours_rift_and_kaira(cat):
    now = utc(2026, 10, 12, 8, 5)                                   # 10:05 à Paris
    rifts = occurrences(ev(cat, "spacetime_rift"), now, now + timedelta(days=1))
    assert [paris(r)[-5:] for r in rifts] == ["11:00", "14:00", "17:00", "20:00", "23:00",
                                               "02:00", "05:00", "08:00"]
    assert paris(next_occurrence(ev(cat, "watcher_kaira"), now)) == "Mon 12/10 13:00"


def test_weekly_siege_bosses_nahma(cat):
    now = utc(2026, 10, 12, 8)                                      # lundi 12/10
    sieges = occurrences(ev(cat, "artifact_siege"), now, now + timedelta(days=7))
    assert [paris(s) for s in sieges] == ["Mon 12/10 21:00", "Thu 15/10 21:00", "Sat 17/10 21:00"]
    assert paris(next_occurrence(ev(cat, "siege_bosses"), now)) == "Mon 12/10 21:30"
    assert paris(next_occurrence(ev(cat, "guardian_nahma"), now)) == "Fri 16/10 21:00"


def test_resets_follow_utc_so_paris_hour_changes_with_dst(cat):
    daily = ev(cat, "daily_reset")
    assert paris(next_occurrence(daily, utc(2026, 10, 12, 8))) == "Tue 13/10 09:00"     # été
    assert paris(next_occurrence(daily, utc(2026, 10, 25, 12))) == "Mon 26/10 08:00"    # hiver
    weekly = ev(cat, "weekly_reset")
    assert paris(next_occurrence(weekly, utc(2026, 10, 12, 8))) == "Wed 14/10 09:00"
    assert paris(next_occurrence(weekly, utc(2026, 10, 22, 8))) == "Wed 28/10 08:00"


def test_reset_items_follow_their_reset(cat):
    assert next_occurrence(ev(cat, "nightmare_tickets"), utc(2026, 10, 12, 8)) == \
        next_occurrence(ev(cat, "daily_reset"), utc(2026, 10, 12, 8))
    assert next_occurrence(ev(cat, "ascension_trial"), utc(2026, 10, 12, 8)) == \
        next_occurrence(ev(cat, "weekly_reset"), utc(2026, 10, 12, 8))


def test_server_time_events_keep_local_hour_across_dst(cat):
    """Siège à 21:00 heure serveur : reste 21:00 à Paris avant ET après le 25/10."""
    sieges = occurrences(ev(cat, "artifact_siege"), utc(2026, 10, 22), utc(2026, 11, 1))
    assert [paris(s) for s in sieges] == ["Thu 22/10 21:00", "Sat 24/10 21:00", "Mon 26/10 21:00",
                                          "Thu 29/10 21:00", "Sat 31/10 21:00"]
    assert sieges[0].hour == 19 and sieges[2].hour == 20          # UTC change, heure locale non


def test_fall_back_ambiguous_hour_happens_once(cat):
    """25/10 : 02:00–03:00 existe deux fois. La faille de 02:00 n'est notée qu'une fois."""
    rifts = occurrences(ev(cat, "spacetime_rift"), utc(2026, 10, 24, 22), utc(2026, 10, 25, 4))
    assert [paris(r) for r in rifts] == ["Sun 25/10 02:00", "Sun 25/10 05:00"]


def test_spring_forward_missing_hour_is_skipped(cat):
    """29/03 : 02:00–03:00 n'existe pas. Pas de faille fantôme à « 02:00 »."""
    rifts = occurrences(ev(cat, "spacetime_rift"), utc(2026, 3, 28, 22), utc(2026, 3, 29, 5))
    assert [paris(r) for r in rifts] == ["Sun 29/03 05:00"]


def test_hourly_events_are_every_real_hour_even_on_dst_days(cat):
    shugo = occurrences(ev(cat, "shugo_festival"), utc(2026, 10, 24, 22), utc(2026, 10, 25, 4))
    assert len(shugo) == 6                                         # une par heure réelle, ni plus ni moins
    assert all(b - a == timedelta(hours=1) for a, b in zip(shugo, shugo[1:]))


def test_blackout_skips_occurrences(tmp_path):
    import json
    p = tmp_path / "events_catalog.json"
    p.write_text(json.dumps({"blackouts": [{"start": "2026-10-14T05:00:00+00:00",
                                            "end": "2026-10-14T10:00:00+00:00", "label": "Maintenance"}]}))
    c = load_catalog("eu", p)
    shugo = occurrences(c.by_id["shugo_festival"], utc(2026, 10, 14, 4), utc(2026, 10, 14, 12))
    assert [s.hour for s in shugo] == [10, 11, 12]                 # 05h–09h UTC supprimées


def test_other_region_uses_its_server_timezone():
    c = load_catalog("naw", None)
    siege = next_occurrence(c.by_id["artifact_siege"], utc(2026, 10, 12, 8))
    assert siege.astimezone(ZoneInfo("America/Los_Angeles")).hour == 21


# --- planification des alertes -------------------------------------------------------

def planner(cat, enabled=(), resets=None, now=None, state=None, tmp=None):
    prefs = EventPrefs(enabled={e: True for e in enabled},
                       reset_reminders=resets or {"daily": [60], "weekly": [60]})
    return EventPlanner(cat, prefs, state or EventState())


def test_alert_one_minute_before_event(cat):
    p = planner(cat, ["shugo_festival"])
    nxt = p.next_alert(utc(2026, 10, 12, 8, 5))
    assert nxt.at == utc(2026, 10, 12, 8, 59) and "Festival des Shugos" in nxt.title
    assert nxt.body == "Le Festival des Shugos commence dans 1 minute !"


def test_disabled_and_unconfirmed_never_alert(cat):
    p = planner(cat, ["arena_tactics", "maintenance", "odyle_energy"])
    assert p.next_alert(utc(2026, 10, 12, 8)) is None


def test_due_alert_sent_once_even_after_restart(cat, tmp_path):
    state = EventState(path=tmp_path / "state.json")
    p = planner(cat, ["dimensional_invasion"], state=state)
    t = utc(2026, 10, 12, 8, 29, 2)
    due = p.due_alerts(t)
    assert len(due) == 1
    p.mark_sent(due[0], t)
    assert p.due_alerts(t + timedelta(seconds=20)) == []
    restarted = planner(cat, ["dimensional_invasion"], state=EventState(path=tmp_path / "state.json"))
    assert restarted.due_alerts(t + timedelta(seconds=30)) == []      # pas de doublon au redémarrage


def test_no_avalanche_after_sleep(cat):
    p = planner(cat, ["shugo_festival", "dimensional_invasion"])
    woke = utc(2026, 10, 12, 14, 29, 30)                           # réveil après 6 h de veille
    due = p.due_alerts(woke)
    assert len(due) == 1 and "Invasion" in due[0].title            # seule l'alerte de 14:29 (encore valable)
    skipped = p.stale_alerts(woke, since=utc(2026, 10, 12, 8))
    assert len(skipped) >= 10                                       # les autres sont ignorées, pas envoyées


def test_reset_reminders_grouped_in_one_notification(cat):
    p = planner(cat, ["nightmare_tickets", "duty_missions", "shugo_keys", "login_rewards"],
                resets={"daily": [60, 720], "weekly": []})
    a = p.next_alert(utc(2026, 10, 12, 8))
    # reset du 13/10 à 07:00 UTC → rappels à 19:00 UTC (12 h) puis 06:00 UTC (1 h)
    assert a.at == utc(2026, 10, 12, 19, 0)
    assert "12 h" in a.title and "Tickets de Cauchemar" in a.body and "Missions quotidiennes" in a.body
    assert "si" in a.body                                           # formulation neutre (pas « tu as »)
    assert "Récompenses de connexion" in a.body                    # à vérifier : activable


def test_reset_without_enabled_items_sends_nothing(cat):
    p = planner(cat, ["shugo_festival"], resets={"daily": [60], "weekly": [60]})
    alerts = p.alerts_between(utc(2026, 10, 12, 8), utc(2026, 10, 13, 8))
    assert all("Festival" in a.title for a in alerts)


def test_weekly_reset_reminder_24h(cat):
    p = planner(cat, ["ascension_trial"], resets={"daily": [], "weekly": [1440]})
    a = p.next_alert(utc(2026, 10, 12, 8))
    assert a.at == utc(2026, 10, 13, 7, 0) and "Épreuve d'Ascension" in a.body


def test_dst_week_alert_times(cat):
    p = planner(cat, ["artifact_siege"])
    a = p.next_alert(utc(2026, 10, 25, 12))
    assert paris(a.at) == "Mon 26/10 20:59"                        # 1 min avant 21:00, heure d'hiver


def test_prefs_roundtrip(tmp_path):
    path = tmp_path / "events.json"
    p = EventPrefs(path=path)
    p.set_enabled("shugo_festival", True)
    p.sound = True
    p.reset_reminders["weekly"] = [60, 1440]
    p.save()
    q = EventPrefs.load(path)
    assert q.is_enabled("shugo_festival") and q.sound and q.reset_reminders["weekly"] == [60, 1440]
    assert not q.is_enabled("dimensional_invasion")


def test_state_prunes_old_keys(tmp_path):
    s = EventState(path=tmp_path / "s.json")
    s.mark("old", utc(2026, 10, 1))
    s.mark("new", utc(2026, 10, 12))
    s.prune(utc(2026, 10, 12, 1))
    assert not s.was_sent("old") and s.was_sent("new")
