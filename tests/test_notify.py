"""Module Événements côté Qt : planificateur, anti-doublon, redémarrage, veille, verrou,
fenêtre (cases, sauvegarde) et vraie notification D-Bus (faux serveur freedesktop)."""
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6 import QtWidgets  # noqa: E402
from PyQt6.QtCore import Qt  # noqa: E402

from aion_target.events import EventPrefs, EventState, load_catalog  # noqa: E402
from aion_target.notify import EventNotifier  # noqa: E402

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class FakeService:
    def __init__(self):
        self.sent, self.active, self.detail = [], True, "faux service"

    def check(self):
        return True, self.detail

    def send(self, title, body, sound=False, done=None):
        self.sent.append((title, body, sound))
        if done:
            done(True, "")
        return True


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def simulated_catalog(tmp: Path, start_paris: datetime) -> Path:
    """Événement de test « dans 2 minutes » (heure de Paris), via la surcharge du calendrier."""
    over = tmp / "events_catalog.json"
    over.write_text(json.dumps({"events": [{
        "id": "sim", "name": "Événement simulé", "category": "daily", "kind": "event",
        "tz": "Europe/Paris", "rule": {"type": "daily", "times": [start_paris.strftime("%H:%M")]},
        "status": "confirmed", "sources": ["shugo", "aion2meta"], "phrase": "L'événement simulé"}]}),
        encoding="utf-8")
    return over


def make(tmp: Path, clock, enabled=("sim",), service=None, lock=True):
    from zoneinfo import ZoneInfo
    cat = load_catalog("eu", tmp / "events_catalog.json")
    prefs = EventPrefs.load(tmp / "events.json")
    for eid in enabled:
        prefs.set_enabled(eid, True)
    prefs.save()
    n = EventNotifier(cat, prefs, EventState(tmp / "events_state.json"), service or FakeService(),
                      lambda m: None, tmp / "events.lock" if lock else None, clock)
    return n


@pytest.fixture
def sim(tmp_path, app):
    from zoneinfo import ZoneInfo
    now = datetime(2026, 10, 14, 10, 0, 0, tzinfo=UTC)        # 12:00 à Paris (été)
    start = now + timedelta(minutes=2)
    simulated_catalog(tmp_path, start.astimezone(ZoneInfo("Europe/Paris")))
    return tmp_path, Clock(now), start


def test_simulated_event_alerts_at_t_minus_1_once(sim):
    tmp, clock, start = sim
    n = make(tmp, clock)
    assert n.start()
    assert n.service.sent == []                              # T-2 : rien
    assert n.next.at == start - timedelta(minutes=1)         # alerte prévue à T-1
    assert 59 <= n.timer.remainingTime() / 1000 <= 60.1       # minuteur réarmé, pas de boucle
    clock.t = start - timedelta(minutes=1)
    n._tick()
    assert [s[0] for s in n.service.sent] == ["AION 2 : Événement simulé"]
    assert n.service.sent[0][1] == "L'événement simulé commence dans 1 minute !"
    for dt in (1, 5, 30, 80):                                 # ticks suivants : aucun doublon
        clock.t = start - timedelta(minutes=1) + timedelta(seconds=dt)
        n._tick()
    assert len(n.service.sent) == 1
    n.stop()


def test_restart_does_not_resend(sim):
    tmp, clock, start = sim
    clock.t = start - timedelta(minutes=1)
    n = make(tmp, clock)
    n.start()
    assert len(n.service.sent) == 1
    n.stop()
    clock.t += timedelta(seconds=20)                          # redémarrage 20 s plus tard
    m = make(tmp, clock)
    m.start()
    assert m.service.sent == []
    m.stop()


def test_start_slightly_late_still_alerts(sim):
    tmp, clock, start = sim
    clock.t = start - timedelta(seconds=30)                   # lancé 30 s après l'heure d'alerte
    n = make(tmp, clock)
    n.start()
    assert len(n.service.sent) == 1
    n.stop()


def test_disabled_event_never_notifies(sim):
    tmp, clock, start = sim
    n = make(tmp, clock, enabled=())
    n.start()
    clock.t = start - timedelta(minutes=1)
    n._tick()
    assert n.service.sent == [] and n.next is None
    n.stop()


def test_wake_from_sleep_no_avalanche(tmp_path, app):
    clock = Clock(datetime(2026, 10, 14, 10, 0, 30, tzinfo=UTC))
    n = make(tmp_path, clock, enabled=("shugo_festival", "dimensional_invasion"))
    n.start()
    clock.t += timedelta(hours=5)                             # veille de 5 h (≈ 10 alertes manquées)
    n._tick()
    assert n.service.sent == []
    skipped = [v for v in n.state.sent.values() if v[1] == "skipped"]
    assert len(skipped) >= 9
    clock.t = clock.t.replace(minute=59, second=0)            # la suivante part normalement
    n._tick()
    assert [s[0] for s in n.service.sent] == ["AION 2 : Festival des Shugos"]
    n.stop()


def test_single_instance_lock(sim):
    tmp, clock, start = sim
    a, b = make(tmp, clock), make(tmp, clock)
    assert a.start() and not b.start()
    clock.t = start - timedelta(minutes=1)
    a._tick()
    b._tick()
    assert len(a.service.sent) == 1 and b.service.sent == []
    a.stop()


def test_dst_change_alert_time(tmp_path, app):
    """Nuit du 24 au 25/10/2026 : reset à 07:00 UTC = 09:00 → 08:00 à Paris ; alerte à 07:59 Paris."""
    clock = Clock(datetime(2026, 10, 25, 5, 50, tzinfo=UTC))
    n = make(tmp_path, clock, enabled=("daily_reset",))
    n.prefs.reset_reminders = {"daily": [], "weekly": []}
    n.start()
    from zoneinfo import ZoneInfo
    assert n.next.at.astimezone(ZoneInfo("Europe/Paris")).strftime("%d/%m %H:%M") == "25/10 07:59"
    assert n.next.body == "La réinitialisation quotidienne a lieu dans 1 minute !"
    n.stop()


def test_game_closed_does_not_matter(sim):
    """Le planificateur n'utilise ni capture ni réseau : il tourne seul (jeu fermé)."""
    import aion_target.notify as notify
    src = Path(notify.__file__).read_text(encoding="utf-8") + \
        Path(notify.__file__).with_name("events.py").read_text(encoding="utf-8")
    for forbidden in ("capture", "httpx", "mss", "ocr", "lookup", "shugo.gg"):
        assert f"import {forbidden}" not in src and f"from .{forbidden}" not in src


# --- fenêtre ------------------------------------------------------------------------------

def test_window_toggle_saves_prefs_and_reschedules(sim):
    from aion_target.events_ui import EventsWindow
    tmp, clock, start = sim
    n = make(tmp, clock, enabled=())
    n.start()
    w = EventsWindow(n, clock=clock)
    it = w._items["shugo_festival"]
    it.setCheckState(0, Qt.CheckState.Checked)
    assert json.loads((tmp / "events.json").read_text())["enabled"]["shugo_festival"] is True
    assert w.count.text() == "1 activé"
    it.setCheckState(0, Qt.CheckState.Unchecked)
    assert json.loads((tmp / "events.json").read_text())["enabled"]["shugo_festival"] is False
    # entrée sans horaire fiable : pas de case à cocher
    assert not (w._items["arena_tactics"].flags() & Qt.ItemFlag.ItemIsUserCheckable)
    assert w._items["arena_tactics"].text(1) == "Horaire à confirmer"
    # prochain déclenchement en heure de Paris + compte à rebours
    w._refresh_times()
    assert w._items["shugo_festival"].text(1) == "aujourd'hui 13:00"
    assert w._items["shugo_festival"].text(2) == "60:00" or w._items["shugo_festival"].text(2) == "1 h 00"
    n.stop()


def test_window_all_on_off_and_filter(sim):
    from aion_target.events_ui import EventsWindow
    tmp, clock, start = sim
    n = make(tmp, clock, enabled=())
    n.start()
    w = EventsWindow(n, clock=clock)
    w.cat_box.setCurrentIndex(w.cat_box.findData("hourly"))
    w._set_all(True)                                         # n'active que le filtre en cours
    on = {k for k, v in n.prefs.enabled.items() if v}
    assert on == {"shugo_festival", "dimensional_invasion", "spacetime_rift", "watcher_kaira"}
    w.cat_box.setCurrentIndex(0)
    w._set_all(True)
    on = {k for k, v in n.prefs.enabled.items() if v}
    assert "arena_tactics" not in on and "daily_reset" in on   # jamais d'horaire inventé
    w.search.setText("nahma")
    visible = [eid for eid, it in w._items.items() if not it.isHidden()]
    assert visible == ["guardian_nahma"]
    w._set_all(False)
    assert not any(n.prefs.enabled.values())
    # rappels de reset et son
    w.reminder_btns[("weekly", 1440)].setChecked(True)
    w.sound.setChecked(True)
    saved = json.loads((tmp / "events.json").read_text())
    assert saved["reset_reminders"]["weekly"] == [60, 1440] and saved["sound"] is True
    n.stop()


def test_prefs_survive_restart(sim):
    tmp, clock, _ = sim
    n = make(tmp, clock, enabled=("guardian_nahma",))
    n.prefs.sound = True
    n.prefs.save()
    m = make(tmp, clock, enabled=())
    assert m.prefs.is_enabled("guardian_nahma") and m.prefs.sound


def test_sound_off_by_default(tmp_path):
    assert EventPrefs.load(tmp_path / "absent.json").sound is False


# --- vraie notification D-Bus -------------------------------------------------------------

@pytest.mark.skipif(not shutil.which("dbus-run-session"), reason="dbus-run-session absent")
def test_real_dbus_notification(tmp_path):
    out = tmp_path / "notifs.jsonl"
    script = tmp_path / "client.py"
    script.write_text(f"""
import subprocess, sys, time
sys.path.insert(0, {str(ROOT)!r})
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer
srv = subprocess.Popen([sys.executable, {str(ROOT / 'tools' / 'fake_notify_server.py')!r}, {str(out)!r}],
                       stdout=subprocess.PIPE, text=True)
srv.stdout.readline()
app = QApplication([])
from aion_target.notify import NotificationService
s = NotificationService(lambda: None, print)
ok, detail = s.check()
print("CHECK", ok, detail)
res = []
try:
    s.send("AION 2 : Festival des Shugos", "Le Festival des Shugos commence dans 1 minute !",
           done=lambda ok, e: res.append(ok))
    QTimer.singleShot(1500, app.quit)
    app.exec()
finally:
    srv.terminate(); srv.wait(3)
print("RESULT", res)
""", encoding="utf-8")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    p = subprocess.run(["dbus-run-session", "--", sys.executable, str(script)], env=env,
                       capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    assert "CHECK True" in p.stdout, p.stdout + p.stderr
    assert "RESULT [True]" in p.stdout, p.stdout + p.stderr
    lines = [json.loads(l) for l in out.read_text().splitlines()]
    assert len(lines) == 1
    n = lines[0]
    assert n["summary"] == "AION 2 : Festival des Shugos"
    assert n["body"] == "Le Festival des Shugos commence dans 1 minute !"
    assert n["app"] == "Aion Target" and n["hints"]["suppress-sound"] == "True"


# --- sons ---------------------------------------------------------------------------------

def test_builtin_sounds_exist_and_resolve():
    from aion_target import sounds
    ids = [sid for sid, _ in sounds.choices()]
    assert ids[:6] == [f"builtin:{k}" for k in sounds.BUILTIN]
    for sid in ids[:6]:
        assert sounds.resolve(sid).stat().st_size > 1000
    assert sounds.resolve("file:/nope.wav") is None
    assert sounds.resolve("n'importe quoi") is None


def test_scaled_wav_volume(tmp_path):
    import wave
    from array import array
    from aion_target import sounds
    src = sounds.resolve("builtin:cloche")
    half = sounds.scaled_wav(src, 50)
    assert half != src and sounds.scaled_wav(src, 100) == src

    def peak(p):
        with wave.open(str(p)) as w:
            return max(abs(x) for x in array("h", w.readframes(w.getnframes())))
    assert abs(peak(half) - peak(src) / 2) <= 1


def test_player_command(monkeypatch):
    from aion_target import sounds
    p = sounds.resolve("builtin:gong")
    monkeypatch.setattr(sounds.shutil, "which", lambda n: "/usr/bin/" + n if n == "pw-play" else None)
    assert sounds.player_command(p, 40) == ["pw-play", "--volume=0.40", str(p)]
    monkeypatch.setattr(sounds.shutil, "which", lambda n: "/usr/bin/" + n if n == "paplay" else None)
    assert sounds.player_command(p, 50) == ["paplay", "--volume=32768", str(p)]
    monkeypatch.setattr(sounds.shutil, "which", lambda n: None)
    assert sounds.player_command(p, 50) is None


def test_one_sound_for_simultaneous_alerts(tmp_path, app, monkeypatch):
    from aion_target import sounds
    played = []
    monkeypatch.setattr(sounds, "play", lambda sid, vol: played.append((sid, vol)) or (True, "x"))
    # 00:59 Paris : Festival (01:00) + Observateur Kaira (01:00) en même temps
    clock = Clock(datetime(2026, 10, 13, 22, 59, 0, tzinfo=UTC))
    n = make(tmp_path, clock, enabled=("shugo_festival", "watcher_kaira"))
    n.prefs.sound, n.prefs.sound_id, n.prefs.volume = True, "builtin:cristal", 35
    n.start()
    assert len(n.service.sent) == 2
    assert played == [("builtin:cristal", 35)]
    n.stop()


def test_no_sound_when_disabled(tmp_path, app, monkeypatch):
    from aion_target import sounds
    played = []
    monkeypatch.setattr(sounds, "play", lambda sid, vol: played.append(sid) or (True, "x"))
    clock = Clock(datetime(2026, 10, 13, 22, 59, 0, tzinfo=UTC))
    n = make(tmp_path, clock, enabled=("shugo_festival",))
    n.start()
    assert len(n.service.sent) == 1 and played == []
    n.stop()


def test_window_sound_choice_saved(sim, monkeypatch):
    from aion_target import sounds
    from aion_target.events_ui import EventsWindow
    played = []
    monkeypatch.setattr(sounds, "play", lambda sid, vol: played.append((sid, vol)) or (True, "x"))
    tmp, clock, _ = sim
    n = make(tmp, clock, enabled=())
    n.start()
    w = EventsWindow(n, clock=clock)
    assert w.sound_box.currentData() == "builtin:carillon" and w.volume.value() == 80
    w._sound_chosen(w.sound_box.findData("builtin:gong"))
    w.volume.setValue(30)
    saved = json.loads((tmp / "events.json").read_text())
    assert saved["sound_id"] == "builtin:gong" and saved["volume"] == 30
    assert played[-1] == ("builtin:gong", 30)                  # aperçu joué à chaque réglage
    assert saved["sound"] is False                              # choisir un son ne l'active pas
    n.stop()
    m = make(tmp, clock, enabled=())
    assert m.prefs.sound_id == "builtin:gong" and m.prefs.volume == 30
