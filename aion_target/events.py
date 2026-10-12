"""Module Événements : calendrier, préférences, état des envois et planification des alertes.

Logique pure (aucune dépendance à Qt) : testable et indépendante du module GS/CP.

- Calendrier : assets/events_catalog.json, surchargeable par events_catalog.json dans le dossier
  de configuration (fusion par identifiant). Chaque entrée porte son fuseau d'origine (« server »
  = fuseau du serveur de la région, ou un nom IANA comme « UTC »).
- Les calculs se font en instants UTC (zoneinfo) : changements d'heure gérés sans jamais coder
  UTC+1 / UTC+2 en dur. Heure locale inexistante (passage à l'heure d'été) → occurrence sautée ;
  heure locale ambiguë (passage à l'heure d'hiver) → une seule occurrence (la première).
- Alertes : « événement qui commence » (N minutes avant, 1 par défaut) et « période qui se
  termine » (rappels avant réinitialisation, regroupés en UNE notification par reset).
- Anti-doublon : chaque alerte a une clé stable (événement + instant + type), mémorisée sur disque.
- Pas d'avalanche après une veille : une alerte en retard de plus de GRACE est ignorée.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

UTC = timezone.utc
BUNDLED = Path(__file__).parent / "assets" / "events_catalog.json"
CATEGORIES = {"hourly": "Récurrents (toutes les heures)", "daily": "Quotidiens",
              "weekly": "Hebdomadaires", "monthly": "Mensuels"}
STATUS_LABELS = {"confirmed": "confirmé", "to_verify": "à vérifier", "unconfirmed": "Horaire à confirmer"}
DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
GRACE = timedelta(seconds=90)          # retard toléré pour envoyer une alerte (réveil, charge…)
RESET_GROUPS = {"daily": "daily_reset", "weekly": "weekly_reset"}


# --- calendrier ----------------------------------------------------------------------

@dataclass
class Event:
    id: str
    name: str
    category: str
    kind: str                       # event | reset | reset_item
    description: str = ""
    tz: str = "UTC"                 # fuseau résolu (IANA)
    tz_origin: str = "UTC"          # tel qu'écrit dans le catalogue (« server », « UTC »…)
    rule: dict = field(default_factory=lambda: {"type": "unknown"})
    duration_min: int | None = None
    status: str = "unconfirmed"
    sources: list = field(default_factory=list)
    note: str = ""
    group: str = ""
    phrase: str = ""
    plural: bool = False
    blackouts: list = field(default_factory=list, repr=False)   # maintenances (instants UTC)

    @property
    def schedulable(self) -> bool:
        return self.status != "unconfirmed" and self.rule.get("type", "unknown") != "unknown"


@dataclass
class Catalog:
    events: list[Event]
    sources: dict
    blackouts: list[tuple[datetime, datetime, str]]
    regions: dict
    region: str
    verified_on: str = ""

    @property
    def by_id(self) -> dict[str, Event]:
        return {e.id: e for e in self.events}


def _merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for key in ("sources", "regions"):
        out[key] = {**base.get(key, {}), **extra.get(key, {})}
    out["blackouts"] = list(base.get("blackouts", [])) + list(extra.get("blackouts", []))
    events = {e["id"]: dict(e) for e in base.get("events", [])}
    order = [e["id"] for e in base.get("events", [])]
    for e in extra.get("events", []):
        if e["id"] in events:
            events[e["id"]].update(e)
        else:
            events[e["id"]] = dict(e)
            order.append(e["id"])
    out["events"] = [events[i] for i in order]
    return out


def load_catalog(region: str = "eu", override_path: Path | None = None) -> Catalog:
    data = json.loads(BUNDLED.read_text(encoding="utf-8"))
    if override_path and Path(override_path).exists():
        data = _merge(data, json.loads(Path(override_path).read_text(encoding="utf-8")))
    regions = data.get("regions", {})
    server_tz = regions.get(region, regions.get("eu", {})).get("server_tz", "Europe/Berlin")
    events = []
    for raw in data.get("events", []):
        origin = raw.get("tz", "UTC")
        e = Event(id=raw["id"], name=raw.get("name", raw["id"]), category=raw.get("category", "daily"),
                  kind=raw.get("kind", "event"), description=raw.get("description", ""),
                  tz=server_tz if origin == "server" else origin, tz_origin=origin,
                  rule=dict(raw.get("rule", {"type": "unknown"})), duration_min=raw.get("duration_min"),
                  status=raw.get("status", "unconfirmed"), sources=list(raw.get("sources", [])),
                  note=raw.get("note", ""), group=raw.get("group", ""),
                  phrase=raw.get("phrase", ""), plural=bool(raw.get("plural", False)))
        events.append(e)
    by_id = {e.id: e for e in events}
    for e in events:                       # un élément de reset suit l'horaire de son reset
        if e.kind == "reset_item":
            reset = by_id.get(RESET_GROUPS.get(e.group, ""))
            if reset and e.status != "unconfirmed":
                e.rule, e.tz, e.tz_origin = dict(reset.rule), reset.tz, reset.tz_origin
            else:
                e.rule = {"type": "unknown"}
    blackouts = []
    for b in data.get("blackouts", []):
        try:
            blackouts.append((datetime.fromisoformat(b["start"]).astimezone(UTC),
                              datetime.fromisoformat(b["end"]).astimezone(UTC), b.get("label", "")))
        except (KeyError, ValueError):
            continue
    for e in events:
        e.blackouts = blackouts
    return Catalog(events, data.get("sources", {}), blackouts, regions, region, data.get("verified_on", ""))


# --- récurrences -----------------------------------------------------------------------

def _hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def _localize(d: date, t: time, tz: ZoneInfo) -> datetime | None:
    """Heure locale → instant UTC ; None si l'heure n'existe pas (passage à l'heure d'été).
    Heure ambiguë (passage à l'heure d'hiver) : première occurrence (fold=0)."""
    naive = datetime.combine(d, t)
    aware = naive.replace(tzinfo=tz, fold=0)
    if aware.astimezone(UTC).astimezone(tz).replace(tzinfo=None) != naive:
        return None
    return aware.astimezone(UTC)


def _local_times(rule: dict) -> list[time]:
    kind = rule["type"]
    if kind == "every_hours":
        start, step = _hm(rule.get("start", "00:00")), int(rule["interval"])
        out, minutes = [], start.hour * 60 + start.minute
        while minutes < 24 * 60:
            out.append(time(minutes // 60, minutes % 60))
            minutes += step * 60
        return out
    if kind == "daily":
        return [_hm(t) for t in rule.get("times", [])]
    if kind in ("weekly", "monthly"):
        return [_hm(rule.get("time", "00:00"))]
    return []


def _in_blackout(at: datetime, blackouts) -> bool:
    return any(start <= at < end for start, end, _ in blackouts)


def occurrences(event: Event, after: datetime, until: datetime, blackouts=None) -> list[datetime]:
    """Débuts de l'événement (instants UTC) dans l'intervalle ]after, until]."""
    if blackouts is None:
        blackouts = event.blackouts
    if not event.schedulable:
        return []
    rule, tz = event.rule, ZoneInfo(event.tz)
    after, until = after.astimezone(UTC), until.astimezone(UTC)
    out: list[datetime] = []
    if rule["type"] == "hourly":
        # Toutes les heures RÉELLES : calcul en UTC (aucun doublon / trou aux changements d'heure).
        minute = int(rule.get("minute", 0))
        offset_min = int(after.astimezone(tz).utcoffset().total_seconds() // 60) % 60
        t = after.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=(minute - offset_min) % 60)
        if t <= after:
            t += timedelta(hours=1)
        while t <= until:
            out.append(t)
            t += timedelta(hours=1)
    else:
        times = _local_times(rule)
        day = after.astimezone(tz).date() - timedelta(days=1)
        last = until.astimezone(tz).date() + timedelta(days=1)
        wanted_days = {DAYS[d] for d in rule.get("days", [])}
        while day <= last:
            if rule["type"] == "weekly" and day.weekday() not in wanted_days:
                pass
            elif rule["type"] == "monthly" and day.day != int(rule.get("day", 1)):
                pass
            else:
                for t in times:
                    at = _localize(day, t, tz)
                    if at is not None and after < at <= until:
                        out.append(at)
            day += timedelta(days=1)
        out.sort()
    return [o for o in out if not _in_blackout(o, blackouts)]


_HORIZON = {"hourly": timedelta(hours=2), "every_hours": timedelta(days=1), "daily": timedelta(days=2),
            "weekly": timedelta(days=8), "monthly": timedelta(days=63)}


def next_occurrence(event: Event, after: datetime, blackouts=None) -> datetime | None:
    if not event.schedulable:
        return None
    span = _HORIZON.get(event.rule["type"], timedelta(days=8))
    start = after
    for _ in range(12):                  # blackouts longs : on élargit la fenêtre
        found = occurrences(event, start, start + span, blackouts)
        if found:
            return found[0]
        start, span = start + span, span * 2
    return None


# --- préférences & état ----------------------------------------------------------------

def _atomic_write(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)                # pas de fichier à moitié écrit en cas de coupure


@dataclass
class EventPrefs:
    enabled: dict = field(default_factory=dict)
    sound: bool = False                   # son de notification : désactivé par défaut
    sound_id: str = "builtin:carillon"    # voir sounds.py (intégré, système ou fichier)
    volume: int = 80                      # 0-100
    lead_minutes: int = 1                 # alerte « événement qui commence »
    reset_reminders: dict = field(default_factory=lambda: {"daily": [60], "weekly": [60]})
    path: Path | None = None

    @classmethod
    def load(cls, path: Path) -> "EventPrefs":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        p = cls(path=Path(path))
        p.enabled = {k: bool(v) for k, v in data.get("enabled", {}).items()}
        p.sound = bool(data.get("sound", False))
        if isinstance(data.get("sound_id"), str) and data["sound_id"]:
            p.sound_id = data["sound_id"]
        try:
            p.volume = max(0, min(100, int(data.get("volume", 80))))
        except (TypeError, ValueError):
            p.volume = 80
        p.lead_minutes = max(1, int(data.get("lead_minutes", 1)))
        rr = data.get("reset_reminders", {})
        for g in ("daily", "weekly"):
            if isinstance(rr.get(g), list):
                p.reset_reminders[g] = sorted({int(x) for x in rr[g] if int(x) > 0})
        return p

    def save(self):
        if self.path:
            _atomic_write(self.path, {"version": 1, "enabled": self.enabled, "sound": self.sound,
                                      "sound_id": self.sound_id, "volume": self.volume,
                                      "lead_minutes": self.lead_minutes, "reset_reminders": self.reset_reminders})

    def is_enabled(self, event_id: str) -> bool:
        return bool(self.enabled.get(event_id, False))

    def set_enabled(self, event_id: str, on: bool):
        self.enabled[event_id] = bool(on)


@dataclass
class EventState:
    """Alertes déjà envoyées (ou ignorées) : survit aux redémarrages, évite les doublons."""
    path: Path | None = None
    sent: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.path and Path(self.path).exists():
            try:
                self.sent = dict(json.loads(Path(self.path).read_text(encoding="utf-8")).get("sent", {}))
            except (OSError, ValueError):
                self.sent = {}

    def was_sent(self, key: str) -> bool:
        return key in self.sent

    def mark(self, key: str, when: datetime, status: str = "sent"):
        self.sent[key] = [when.astimezone(UTC).isoformat(), status]
        self.save()

    def prune(self, now: datetime, keep: timedelta = timedelta(days=3)):
        limit = now - keep
        self.sent = {k: v for k, v in self.sent.items()
                     if datetime.fromisoformat(v[0] if isinstance(v, list) else v) >= limit}
        self.save()

    def save(self):
        if self.path:
            _atomic_write(Path(self.path), {"version": 1, "sent": self.sent})


# --- planification ---------------------------------------------------------------------

@dataclass(frozen=True)
class Alert:
    at: datetime
    key: str
    title: str
    body: str
    event_ids: tuple


def fmt_offset(minutes: int) -> str:
    if minutes % 60 == 0:
        return f"{minutes // 60} h"
    return f"{minutes} min" if minutes < 60 else f"{minutes // 60} h {minutes % 60:02d}"


class EventPlanner:
    def __init__(self, catalog: Catalog, prefs: EventPrefs, state: EventState):
        self.catalog, self.prefs, self.state = catalog, prefs, state

    def _start_alerts(self, start: datetime, end: datetime) -> list[Alert]:
        lead = timedelta(minutes=self.prefs.lead_minutes)
        out = []
        for e in self.catalog.events:
            if e.kind not in ("event", "reset") or not e.schedulable or not self.prefs.is_enabled(e.id):
                continue
            for occ in occurrences(e, start + lead, end + lead):
                if e.kind == "reset":
                    verb = "a lieu"
                else:
                    verb = "commencent" if e.plural else "commence"
                unit = "minute" if self.prefs.lead_minutes == 1 else "minutes"
                out.append(Alert(occ - lead, f"{e.id}@{occ.isoformat()}#start", f"AION 2 : {e.name}",
                                 f"{e.phrase or e.name} {verb} dans {self.prefs.lead_minutes} {unit} !",
                                 (e.id,)))
        return out

    def _reset_alerts(self, start: datetime, end: datetime) -> list[Alert]:
        out = []
        by_id = self.catalog.by_id
        for group, reset_id in RESET_GROUPS.items():
            reset = by_id.get(reset_id)
            if not reset or not reset.schedulable:
                continue
            items = [e for e in self.catalog.events
                     if e.kind == "reset_item" and e.group == group and e.schedulable
                     and self.prefs.is_enabled(e.id)]
            if not items and not self.prefs.is_enabled(reset_id):
                continue
            label = "quotidienne" if group == "daily" else "hebdomadaire"
            if items:
                body = ("Avant la réinitialisation, pense à utiliser si besoin : "
                        + ", ".join(e.name for e in items) + ".")
            else:
                body = f"La réinitialisation {label} approche."
            for off in self.prefs.reset_reminders.get(group, []):
                delta = timedelta(minutes=off)
                for occ in occurrences(reset, start + delta, end + delta):
                    out.append(Alert(occ - delta, f"reset-{group}@{occ.isoformat()}#{off}",
                                     f"AION 2 : réinitialisation {label} dans {fmt_offset(off)}", body,
                                     tuple(e.id for e in items) or (reset_id,)))
        return out

    def alerts_between(self, start: datetime, end: datetime) -> list[Alert]:
        """Alertes dont l'heure d'envoi est dans ]start, end]."""
        return sorted(self._start_alerts(start, end) + self._reset_alerts(start, end), key=lambda a: a.at)

    def due_alerts(self, now: datetime, grace: timedelta = GRACE) -> list[Alert]:
        return [a for a in self.alerts_between(now - grace, now) if not self.state.was_sent(a.key)]

    def stale_alerts(self, now: datetime, since: datetime, grace: timedelta = GRACE) -> list[Alert]:
        """Alertes manquées (veille, ordinateur éteint) : à ignorer, jamais à envoyer en rafale."""
        if since >= now - grace:
            return []
        return [a for a in self.alerts_between(since, now - grace) if not self.state.was_sent(a.key)]

    def next_alert(self, now: datetime, horizon: timedelta = timedelta(days=8)) -> Alert | None:
        span = min(timedelta(hours=6), horizon)      # fenêtre courte d'abord : calcul léger
        while True:
            for a in self.alerts_between(now, now + span):
                if not self.state.was_sent(a.key):
                    return a
            if span >= horizon:
                return None
            span = min(span * 4, horizon)

    def mark_sent(self, alert: Alert, now: datetime, status: str = "sent"):
        self.state.mark(alert.key, now, status)
