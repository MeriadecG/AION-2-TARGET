"""Préchargement des GS/CP des personnages OBSERVÉS par le moteur réseau.

But : quand on cible quelqu'un, sa fiche est déjà en mémoire → affichage immédiat.

1. À CHAQUE observation, consultation LOCALE immédiate (index RAM) : aucune limite, aucun
   réseau. Le nom rejoint l'index prioritaire de l'OCR.
2. Seuls les vrais manques partent sur le réseau, par une file à priorités :
     1 = jamais vu localement, serveur confirmé ; 2 = score local ancien ; plus récent d'abord.
   Prudence envers shugo.gg / NCSoft :
     - uniquement si le SERVEUR est connu (pas de requête ambiguë) ;
     - débit plafonné (RATE requêtes/min, une à la fois) ; jamais pendant une recherche de la
       cible (priorité absolue) ;
     - refus du site (403 / 429) → pause (Retry-After respecté, sinon 5 min croissantes).
3. Sans serveur : identité PARTIELLE conservée (nom, vu à, source) et comptée, jamais de
   requête ni d'attribution arbitraire.
Ce n'est JAMAIS la liste complète des joueurs de l'instance : « observé cette session »
(vu passer dans le flux du jeu à un moment) ≠ « proche maintenant ».
"""
from __future__ import annotations

import time
from dataclasses import dataclass

RATE_PER_MIN = 20
QUEUE_MAX = 150
SEEN_MAX = 5000


def server_slug_from_name(region: str, server_name: str | None, known: list[str]) -> str | None:
    """« Kaisinel » → « eu-kaisinel » (uniquement un serveur que l'appli connaît déjà)."""
    if not server_name:
        return None
    from .shugo import server_slug
    slug = server_slug(region, server_name)
    return slug if slug in known else None


@dataclass
class Observation:
    name: str
    server: str | None        # slug confirmé, ou None (identité partielle)
    source: str               # « reseau » / « groupe »
    first: float              # horloge monotone
    last: float
    count: int = 1
    local: str = "?"          # « récent », « ancien », « absent », « homonymes »


class Prefetcher:
    def __init__(self, lookup_getter, region: str, servers: list[str], clock=time.monotonic,
                 rate_per_min: int = RATE_PER_MIN, log=None):
        self.lookup_getter = lookup_getter      # renvoie le PlayerLookup (créé par le Worker)
        self.region, self.servers = region, servers
        self.clock = clock
        self.interval = 60.0 / max(1, rate_per_min)
        self.log = log or (lambda m: None)
        # File réseau : (nom, serveur) → (priorité, vu à). Plus petite priorité d'abord.
        self.queue: dict[tuple[str, str], tuple[int, int]] = {}   # (priorité, n° d'observation)
        self._seq = 0
        self.seen: dict[str, Observation] = {}
        self._pending: list[tuple[dict, str]] = []   # observations reçues avant le lookup
        self._last = float("-inf")
        self.stats = {"observed": 0, "eligible": 0, "no_server": 0, "local_recent": 0,
                      "local_old": 0, "local_missing": 0, "ambiguous": 0, "sent": 0,
                      "deferred_target": 0, "deferred_limit": 0}

    # compatibilité (anciens tests / rapport)
    @property
    def sent(self) -> int:
        return self.stats["sent"]

    @property
    def skipped_no_server(self) -> int:
        return self.stats["no_server"]

    def observe(self, players: list[dict], source: str = "reseau"):
        lookup = self.lookup_getter()
        if lookup is None:                       # moteur plus rapide que le chargement : on garde
            self._pending.extend((p, source) for p in players)
            self._pending = self._pending[-QUEUE_MAX:]
            return
        if self._pending:
            pending, self._pending = self._pending, []
            for p, src in pending:
                self._observe_one(lookup, p, src)
        for p in players:
            self._observe_one(lookup, p, source)
        if len(self.queue) > QUEUE_MAX:          # on oublie les plus anciennement vus
            for key, _ in sorted(self.queue.items(), key=lambda kv: kv[1][1])[:len(self.queue) - QUEUE_MAX]:
                del self.queue[key]

    def _observe_one(self, lookup, p: dict, source: str):
        from .lookup import RECENT_SCORE
        name = (p.get("name") or "").strip()
        if not name:
            return
        now = self.clock()
        slug = server_slug_from_name(self.region, p.get("server"), self.servers)
        k = name.casefold()
        obs = self.seen.get(k)
        if obs:
            obs.last, obs.count = now, obs.count + 1
            obs.server = obs.server or slug
        else:
            obs = self.seen[k] = Observation(name, slug, source, now, now)
            if len(self.seen) > SEEN_MAX:
                oldest = min(self.seen, key=lambda n: self.seen[n].last)
                del self.seen[oldest]
        self.stats["observed"] += 1
        observe_name = getattr(lookup, "observe_name", None)
        if observe_name:
            observe_name(name)
        if not slug:
            # Identité partielle : un score local n'est utilisable que si le nom est UNIQUE.
            self.stats["no_server"] += 1
            amb = lookup.ambiguous(name)
            info = None if amb else lookup.cached(name)
            obs.local = "homonymes" if amb else ("connu" if info else "absent")
            if amb:
                self.stats["ambiguous"] += 1
            return
        self.stats["eligible"] += 1
        info, age = lookup.cache.get(name, slug)          # index RAM : immédiat, sans quota
        if info is not None and age is not None and age < RECENT_SCORE:
            obs.local = "récent"
            self.stats["local_recent"] += 1
            self.queue.pop((name, slug), None)
            return
        obs.local = "ancien" if info is not None else "absent"
        self.stats["local_old" if info is not None else "local_missing"] += 1
        self._seq += 1
        self.queue[(name, slug)] = (2 if info is not None else 1, self._seq)

    def tick(self) -> tuple[str, str] | None:
        """Appelé régulièrement : lance AU PLUS une requête réseau si le débit le permet."""
        lookup = self.lookup_getter()
        if lookup is None:
            return None
        if self._pending:
            self.observe([])
        if not self.queue or self.clock() - self._last < self.interval:
            return None
        client = getattr(lookup, "client", None)
        if client is not None and getattr(client, "rate_limited", lambda: False)():
            self.stats["deferred_limit"] += 1
            return None
        if getattr(lookup, "target_busy", lambda: False)():
            self.stats["deferred_target"] += 1      # la cible d'abord
            return None
        order = sorted(self.queue.items(), key=lambda kv: (kv[1][0], -kv[1][1]))
        for (name, slug), _ in order:
            del self.queue[(name, slug)]
            needs = getattr(lookup, "needs_network", None)
            if needs is not None and not needs(name, slug):
                continue
            try:
                fut = lookup.prefetch(name, hint=slug, background=True)
            except TypeError:                        # lookup simplifié (tests)
                fut = lookup.prefetch(name, hint=slug)
            if fut is not None:
                self._last = self.clock()
                self.stats["sent"] += 1
                return name, slug
        return None

    def report(self) -> str:
        s = self.stats
        recent = sum(1 for o in self.seen.values() if self.clock() - o.last < 120)
        return (f"Préchargement : {len(self.seen)} personnages observés cette session "
                f"({recent} vus < 2 min, PAS la liste de l'instance) · serveur connu {s['eligible']} "
                f"obs. · sans serveur {s['no_server']} (homonymes {s['ambiguous']}) · "
                f"local récent {s['local_recent']} / ancien {s['local_old']} / absent {s['local_missing']} · "
                f"requêtes envoyées {s['sent']} · en file {len(self.queue)} · "
                f"reportées (cible {s['deferred_target']}, refus du site {s['deferred_limit']})")
