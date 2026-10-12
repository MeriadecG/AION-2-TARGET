"""Préchargement des GS/CP des personnages OBSERVÉS par le moteur réseau.

But : quand on cible quelqu'un, sa fiche est déjà en mémoire → affichage immédiat.
Prudence envers shugo.gg / NCSoft :
  - un personnage n'est préchargé que si son SERVEUR est connu (pas d'homonyme possible) ;
  - au plus RATE requêtes par minute, une à la fois, les plus récemment vus d'abord ;
  - file bornée, doublons fusionnés, rien si la fiche en mémoire est récente (TTL du lookup) ;
  - refus du site (403 / 429) → pause de 5 min (backoff géré par le client) ;
  - jamais présenté comme la liste complète des joueurs de l'instance.
"""
from __future__ import annotations

import time
from collections import OrderedDict

RATE_PER_MIN = 20
QUEUE_MAX = 150


def server_slug_from_name(region: str, server_name: str | None, known: list[str]) -> str | None:
    """« Kaisinel » → « eu-kaisinel » (uniquement un serveur que l'appli connaît déjà)."""
    if not server_name:
        return None
    from .shugo import server_slug
    slug = server_slug(region, server_name)
    return slug if slug in known else None


class Prefetcher:
    def __init__(self, lookup_getter, region: str, servers: list[str], clock=time.monotonic,
                 rate_per_min: int = RATE_PER_MIN, log=None):
        self.lookup_getter = lookup_getter      # renvoie le PlayerLookup (créé par le Worker)
        self.region, self.servers = region, servers
        self.clock = clock
        self.interval = 60.0 / max(1, rate_per_min)
        self.log = log or (lambda m: None)
        self.queue: OrderedDict[tuple[str, str], float] = OrderedDict()
        self._last = float("-inf")
        self.sent = 0
        self.skipped_no_server = 0

    def observe(self, players: list[dict]):
        for p in players:
            name = (p.get("name") or "").strip()
            slug = server_slug_from_name(self.region, p.get("server"), self.servers)
            if not name:
                continue
            if not slug:
                self.skipped_no_server += 1
                continue
            key = (name, slug)
            self.queue.pop(key, None)
            self.queue[key] = self.clock()               # le plus récent passe devant
        while len(self.queue) > QUEUE_MAX:
            self.queue.popitem(last=False)               # on oublie les plus anciens

    def tick(self) -> tuple[str, str] | None:
        """Appelé régulièrement : lance AU PLUS une recherche si le débit le permet."""
        lookup = self.lookup_getter()
        if lookup is None or not self.queue:
            return None
        if self.clock() - self._last < self.interval:
            return None
        client = getattr(lookup, "client", None)
        if client is not None and getattr(client, "rate_limited", lambda: False)():
            return None
        while self.queue:
            (name, slug), _ = self.queue.popitem(last=True)
            if lookup.prefetch(name, hint=slug) is not None:   # None = déjà frais / connu absent
                self._last = self.clock()
                self.sent += 1
                return name, slug
        return None
