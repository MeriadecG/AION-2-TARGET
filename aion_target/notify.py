"""Notifications système et planificateur du module Événements.

- Linux : notifications de bureau freedesktop via D-Bus (QtDBus, déjà fourni par PyQt6 :
  aucune dépendance en plus). Vérifie que le service existe, lit ses capacités et l'état
  « Ne pas déranger » (propriété Inhibited de KDE Plasma).
- Ailleurs (Windows) ou si D-Bus est indisponible : bulle de l'icône de la barre système.
- EventNotifier : UN minuteur Qt, réarmé sur la prochaine alerte (au plus 60 s d'attente,
  ce qui rattrape une veille ou un changement d'heure système). Aucune boucle intensive.
  Un verrou (QLockFile) garantit une seule instance émettrice, même si l'appli est lancée
  deux fois. Les envois sont mémorisés sur disque (aucun doublon après redémarrage).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QLockFile, QObject, Qt, QTimer, pyqtSignal

from .events import GRACE, UTC, Alert, Catalog, EventPlanner, EventPrefs, EventState

SVC = "org.freedesktop.Notifications"
PATH = "/org/freedesktop/Notifications"
APP_NAME = "Aion Target"
MAX_PER_TICK = 6               # garde-fou : jamais de rafale


def _icon_path() -> str:
    p = Path(__file__).parent / "assets" / "icon.png"
    return str(p) if p.exists() else ""


class DBusBackend:
    """org.freedesktop.Notifications.Notify (susssasa{sv}i)."""
    name = "D-Bus (notifications du bureau)"

    def __init__(self, log: Callable[[str], None]):
        from PyQt6 import QtDBus
        self.QtDBus, self.log = QtDBus, log
        self.bus = QtDBus.QDBusConnection.sessionBus()
        self.capabilities: list[str] = []
        self.server = ""
        self._watchers: set = set()

    def _call(self, iface: str, method: str, *args, timeout: int = 2000):
        Q = self.QtDBus
        msg = Q.QDBusMessage.createMethodCall(SVC, PATH, iface, method)
        if args:
            msg.setArguments(list(args))
        return self.bus.call(msg, Q.QDBus.CallMode.Block, timeout)

    def probe(self) -> tuple[bool, str]:
        """(disponible, détail lisible). Appelé au démarrage et par « Vérifier »."""
        Q = self.QtDBus
        if not self.bus.isConnected():
            return False, "bus D-Bus de session inaccessible"
        reg = self.bus.interface().isServiceRegistered(SVC)
        registered = bool(reg.value()) if hasattr(reg, "value") else bool(reg)
        if not registered:
            act = self.bus.interface().activatableServiceNames()
            names = act.value() if hasattr(act, "value") else act
            if SVC not in (names or []):
                return False, "aucun service de notifications sur le bureau"
        rep = self._call(SVC, "GetCapabilities")
        if rep.type() != Q.QDBusMessage.MessageType.ReplyMessage:
            return False, f"service de notifications muet ({rep.errorMessage() or rep.errorName()})"
        self.capabilities = list(rep.arguments()[0]) if rep.arguments() else []
        info = self._call(SVC, "GetServerInformation")
        if info.type() == Q.QDBusMessage.MessageType.ReplyMessage and info.arguments():
            a = info.arguments()
            self.server = f"{a[0]} {a[2]}" if len(a) >= 3 else str(a[0])
        detail = f"{self.server or 'serveur inconnu'}"
        if self.inhibited():
            detail += " — « Ne pas déranger » est ACTIVÉ : les notifications sont masquées"
        return True, detail

    def inhibited(self) -> bool:
        """Mode « Ne pas déranger » de KDE Plasma (propriété Inhibited), si exposée."""
        Q = self.QtDBus
        rep = self._call("org.freedesktop.DBus.Properties", "Get", SVC, "Inhibited", timeout=1000)
        if rep.type() != Q.QDBusMessage.MessageType.ReplyMessage or not rep.arguments():
            return False
        val = rep.arguments()[0]
        val = val.variant() if hasattr(val, "variant") else val
        return bool(val)

    def send(self, title: str, body: str, done: Callable[[bool, str], None] | None = None):
        from PyQt6.QtCore import QMetaType, QVariant
        Q = self.QtDBus

        def typed(value, meta):
            v = QVariant(value)
            v.convert(QMetaType(meta.value))
            return v

        hints = {"urgency": typed(1, QMetaType.Type.UChar),           # normale
                 "desktop-entry": "aion-target",
                 "suppress-sound": True}      # le son choisi est joué par l'appli (sounds.py)
        msg = Q.QDBusMessage.createMethodCall(SVC, PATH, SVC, "Notify")
        msg.setArguments([APP_NAME, typed(0, QMetaType.Type.UInt), _icon_path(), title, body,
                          typed([], QMetaType.Type.QStringList), hints, 8000])
        # Appel asynchrone : jamais de blocage de l'interface ni de l'overlay.
        watcher = Q.QDBusPendingCallWatcher(self.bus.asyncCall(msg, 3000))
        self._watchers.add(watcher)

        def finished(w):
            self._watchers.discard(w)
            try:
                reply = Q.QDBusPendingReply(w)
                ok = not reply.isError()
                err = "" if ok else (reply.error().message() or reply.error().name())
                if "quick succession" in err or "ExcessNotification" in reply.error().name():
                    err = ("KDE a bloqué une notification identique à la précédente envoyée moins "
                           "d'une seconde avant (protection anti-spam)")
                if not ok:
                    self.log(f"Événements : notification refusée par D-Bus ({err})")
                if done:
                    done(ok, err)
            except Exception as e:              # une exception dans un slot Qt arrêterait l'appli
                self.log(f"Événements : réponse D-Bus illisible ({e})")
            w.deleteLater()

        watcher.finished.connect(finished)


class TrayBackend:
    """Bulle de l'icône de la barre système (Windows, ou Linux sans D-Bus)."""
    name = "barre système"

    def __init__(self, tray_getter: Callable[[], object], log: Callable[[str], None]):
        self.tray_getter, self.log = tray_getter, log

    def probe(self) -> tuple[bool, str]:
        from PyQt6.QtWidgets import QSystemTrayIcon
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return False, "pas de zone de notification"
        if not QSystemTrayIcon.supportsMessages():
            return False, "la barre système n'affiche pas de messages"
        detail = "notifications Windows" if sys.platform == "win32" else "bulle de la barre système"
        if sys.platform == "win32":
            detail += " (l'Assistant de concentration peut les masquer)"
        return True, detail

    def send(self, title: str, body: str, done=None):
        from PyQt6.QtWidgets import QSystemTrayIcon
        tray = self.tray_getter()
        if tray is None:
            if done:
                done(False, "icône de barre système absente")
            return
        tray.showMessage(title, body, QSystemTrayIcon.MessageIcon.Information, 8000)
        if done:
            done(True, "")


class NotificationService:
    """Choisit le meilleur moyen d'afficher une notification et mémorise son état."""

    def __init__(self, tray_getter: Callable[[], object], log: Callable[[str], None]):
        self.log = log
        self.backends = []
        if sys.platform.startswith("linux"):
            try:
                self.backends.append(DBusBackend(log))
            except ImportError:
                log("Événements : QtDBus absent, notifications par la barre système")
        self.backends.append(TrayBackend(tray_getter, log))
        self.active = None
        self.detail = "non vérifié"

    def check(self) -> tuple[bool, str]:
        """Vérifie les permissions / la disponibilité. Renvoie (ok, texte)."""
        reasons = []
        for b in self.backends:
            try:
                ok, detail = b.probe()
            except Exception as e:                  # D-Bus capricieux : on passe au suivant
                ok, detail = False, str(e)
            if ok:
                self.active, self.detail = b, f"{b.name} : {detail}"
                return True, self.detail
            reasons.append(f"{b.name} : {detail}")
        self.active, self.detail = None, " ; ".join(reasons) or "aucun moyen de notification"
        return False, self.detail

    def send(self, title: str, body: str, done=None) -> bool:
        if self.active is None and not self.check()[0]:
            self.log(f"Événements : notification impossible ({self.detail}) — {title}")
            if done:
                done(False, self.detail)
            return False
        try:
            self.active.send(title, body, done)
            return True
        except Exception as e:
            self.log(f"Événements : échec d'envoi ({e})")
            self.active = None
            if done:
                done(False, str(e))
            return False


class EventNotifier(QObject):
    """Planificateur léger : un seul QTimer, réarmé sur la prochaine alerte (≤ 60 s)."""
    fired = pyqtSignal(object)          # Alert envoyée (pour l'interface)
    changed = pyqtSignal()              # préférences / planning modifiés

    MAX_WAIT = 60.0                     # s : rattrape veille et changements d'horloge

    def __init__(self, catalog: Catalog, prefs: EventPrefs, state: EventState,
                 service: NotificationService, log: Callable[[str], None],
                 lock_path: Path | None = None, clock: Callable[[], datetime] | None = None,
                 parent: QObject | None = None):
        super().__init__(parent)
        self.catalog, self.prefs, self.state = catalog, prefs, state
        self.planner = EventPlanner(catalog, prefs, state)
        self.service, self.log = service, log
        self.clock = clock or (lambda: datetime.now(UTC))
        self.lock = QLockFile(str(lock_path)) if lock_path else None
        self.primary = False
        self.override_path: Path | None = None
        self.next: Alert | None = None
        self._last: datetime | None = None
        self._pruned: datetime | None = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._tick)

    # --- cycle de vie ---
    def start(self) -> bool:
        if self.lock is not None:
            Path(self.lock.fileName()).parent.mkdir(parents=True, exist_ok=True)
            self.lock.setStaleLockTime(0)           # verrou orphelin détecté par PID
            if not self.lock.tryLock(0):
                self.log("Événements : une autre instance d'Aion Target envoie déjà les "
                         "notifications — celle-ci reste silencieuse (pas de doublon).")
                return False
        self.primary = True
        ok, detail = self.service.check()
        self.log(f"Événements : notifications {'OK' if ok else 'INDISPONIBLES'} — {detail}")
        n = sum(1 for e in self.catalog.events if self.prefs.is_enabled(e.id) and e.schedulable)
        self.log(f"Événements : {n} alerte(s) activée(s)")
        self._last = self.clock()
        self._tick()
        return True

    def stop(self):
        self.timer.stop()
        if self.lock is not None and self.primary:
            self.lock.unlock()
        self.primary = False

    def reload_catalog(self) -> str:
        """Relit le calendrier (après modification de events_catalog.json)."""
        from .events import load_catalog
        try:
            self.catalog = load_catalog(self.catalog.region, self.override_path)
        except Exception as e:
            self.log(f"Événements : calendrier illisible ({e})")
            return f"Calendrier illisible : {e}"
        self.planner = EventPlanner(self.catalog, self.prefs, self.state)
        self.log(f"Événements : calendrier rechargé ({len(self.catalog.events)} entrées)")
        self.reschedule()
        return f"Calendrier rechargé : {len(self.catalog.events)} entrées."

    def reschedule(self):
        """À appeler après un changement de préférences."""
        self.changed.emit()
        if self.primary:
            QTimer.singleShot(0, self._tick)

    # --- moteur ---
    def _tick(self):
        if not self.primary:
            return
        now = self.clock()
        try:
            self._process(now)
        except Exception as e:                       # jamais de plantage du minuteur
            self.log(f"Événements : ERREUR planificateur ({e})")
        self._arm(now)

    def _process(self, now: datetime):
        last = self._last or now
        if now - last > GRACE + timedelta(seconds=self.MAX_WAIT):
            # Veille / horloge avancée : les alertes manquées sont IGNORÉES, pas envoyées en rafale.
            missed = self.planner.stale_alerts(now, last)
            for a in missed:
                self.planner.mark_sent(a, now, "skipped")
            self.log(f"Événements : reprise après {int((now - last).total_seconds() // 60)} min "
                     f"d'inactivité (veille ?) — {len(missed)} alerte(s) passée(s) ignorée(s)")
        due = self.planner.due_alerts(now)
        if due:
            self.play_sound()                         # un seul son, même pour plusieurs alertes
        for i, a in enumerate(due):
            if i >= MAX_PER_TICK:
                self.planner.mark_sent(a, now, "skipped")
                continue
            self.service.send(a.title, a.body)
            self.planner.mark_sent(a, now)
            self.log(f"Événements : notification « {a.title} » ({a.key})")
            self.fired.emit(a)
        self._last = now
        if self._pruned is None or now - self._pruned > timedelta(hours=6):
            self.state.prune(now)
            self._pruned = now

    def _arm(self, now: datetime):
        try:
            self.next = self.planner.next_alert(now)
        except Exception as e:
            self.log(f"Événements : ERREUR calcul prochaine alerte ({e})")
            self.next = None
        wait = self.MAX_WAIT
        if self.next is not None:
            wait = min(wait, max(0.2, (self.next.at - now).total_seconds() + 0.05))
        self.timer.start(int(wait * 1000))

    def test(self, done: Callable[[bool, str], None] | None = None):
        self.service.check()
        stamp = datetime.now().strftime("%H:%M:%S")      # texte unique : pas pris pour du spam
        self.service.send("AION 2 : test de notification",
                          f"Les alertes d'événements d'Aion Target s'afficheront ainsi ({stamp}).",
                          done=done)
        self.play_sound()

    def play_sound(self, force: bool = False) -> tuple[bool, str]:
        """Son choisi dans les préférences (si activé, ou forcé par « Écouter »)."""
        if not (self.prefs.sound or force):
            return True, "son désactivé"
        from .sounds import play
        ok, detail = play(self.prefs.sound_id, self.prefs.volume)
        if not ok:
            self.log(f"Événements : son non joué ({detail})")
        return ok, detail


def build_notifier(region: str, tray_getter: Callable[[], object], log: Callable[[str], None],
                   folder: Path | None = None) -> EventNotifier:
    """Notifier configuré avec les fichiers du dossier de configuration :
    events.json (préférences), events_state.json (envois), events_catalog.json (surcharge
    facultative du calendrier), events.lock (instance unique)."""
    from .config import config_path
    from .events import load_catalog
    folder = Path(folder) if folder else config_path().parent
    override = folder / "events_catalog.json"
    try:
        catalog = load_catalog(region, override)
    except Exception as e:                      # surcharge invalide : calendrier embarqué
        log(f"Événements : {override.name} ignoré ({e})")
        catalog = load_catalog(region)
    n = EventNotifier(catalog, EventPrefs.load(folder / "events.json"),
                      EventState(folder / "events_state.json"),
                      NotificationService(tray_getter, log), log, folder / "events.lock")
    n.override_path = override
    return n
