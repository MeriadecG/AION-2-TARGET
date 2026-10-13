"""Service de recherche : mémoire disque + recherche anticipée + rafraîchissement en fond.

Utilisation par la boucle de lecture :
  - `prefetch(nom)` dès la PREMIÈRE lecture d'un nom (avant confirmation) ;
  - `get(nom)` quand le nom est confirmé : renvoie tout de suite le dernier score connu,
    et les résultats arrivent ensuite par le callback `on_result(nom, info)` (1 ou 2 fois).

Ordre, du plus rapide au plus lent :
  1. mémoire locale : score de moins de 10 min → affiché, AUCUNE requête ;
  2. base shugo.gg (recherche par nom, ~0,1-0,3 s) → affiché dès réception ;
     si ce score a moins de 10 min, on s'arrête là ;
  3. fiche shugo.gg (relue chez NCSoft, 1-5 s) → score du moment, affiché à son arrivée.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from .cache import PlayerCache
from .names import NameIndex
from .shugo import AmbiguousName, CharacterInfo, NetworkError, ShugoClient

# Un score rafraîchi dans cette session il y a moins de 60 s : pas de nouvelle requête.
FRESH_FOR = 60
# Un score (mémoire ou base shugo.gg) de moins de 10 min suffit : pas de fiche lente.
RECENT_SCORE = 600
# Temps laissé à la recherche rapide pour fournir le serveur exact avant de lancer la fiche.
QUICK_WAIT = 0.35
# Un nom introuvable (PNJ au nom d'un seul mot, faute d'OCR) n'est pas redemandé pendant 1 h.
MISSING_FOR = 3600


def incomplete(info) -> bool:
    """Profil connu mais sans GS ou sans CP (ex. reçu du groupe) : la fiche le complétera."""
    return info is not None and (info.gear_score is None or info.combat_power is None)


class PlayerLookup:
    def __init__(self, client: ShugoClient, cache: PlayerCache, on_result=None, clock=time.monotonic,
                 log=None):
        self.client = client
        self.log = log or (lambda msg: None)
        self.cache = cache
        self.on_result = on_result          # appelé (nom, CharacterInfo | None) depuis un thread
        self.clock = clock
        self._missing: dict[str, float] = {}
        self._inflight: dict[str, Future] = {}
        self._fresh: dict[str, float] = {}  # dernière mise à jour réussie (horloge monotone)
        self._ambiguous: dict[str, tuple[list[str], float]] = {}   # nom → (serveurs, quand)
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="player")
        self._target_keys: set[str] = set()   # recherches lancées pour la CIBLE (prioritaires)
        self.names = NameIndex(cache.all_names())      # ~0,3 s pour 21 000 noms
        # Personnages OBSERVÉS cette session (réseau, groupe) : index prioritaire pour l'OCR.
        self.observed = NameIndex()

    def rebuild_names(self):
        """Après un import : dictionnaire reconstruit (remplacé d'un coup, sans blocage)."""
        self.names = NameIndex(self.cache.all_names())

    def resolve(self, ocr_name: str, raw_line: str = "") -> str | None:
        """Vrai nom de joueur correspondant à une lecture OCR (niveau collé, lettre mal lue…).

        Ordre : correspondance exacte (personnages observés, puis dictionnaire), puis une lettre
        mal lue — observés d'abord, mais refusé si le dictionnaire propose un AUTRE nom."""
        def find(idx, **kw):
            return idx.resolve(ocr_name, **kw) or idx.resolve_line(raw_line, **kw)
        for idx in (self.observed, self.names):
            hit = find(idx, fuzzy=False)
            if hit:
                return hit
        obs, gen = find(self.observed, exact=False), find(self.names, exact=False)
        if obs:
            return obs if gen in (None, obs) else None    # rapprochement incertain : refusé
        return gen

    def report(self) -> str:
        client = self.client
        limited = getattr(client, "rate_limited", lambda: False)()
        return (f"Mémoire : {len(self.names)} noms au dictionnaire · {len(self.observed)} observés · "
                f"recherches en cours {len(self._inflight)} · introuvables mémorisés {len(self._missing)}"
                + (" · site en pause (refus 403/429)" if limited else ""))

    def observe_name(self, name: str):
        """Nom vu par le réseau (pas forcément proche maintenant) : prioritaire pour l'OCR."""
        if name and name not in self.observed:
            self.observed.add(name)

    def cached_age(self, name: str, hint: str | None = None) -> tuple[CharacterInfo | None, float | None]:
        """(score, âge en s) pour CE personnage, depuis l'index RAM ; jamais un homonyme."""
        if not hint and self.ambiguous(name):
            return None, None
        return self.cache.get(name, hint)

    def target_busy(self) -> bool:
        """Une recherche réseau pour la cible est en cours (le préchargement attend)."""
        with self._lock:
            return any(k in self._inflight for k in self._target_keys)

    def known_missing(self, name: str) -> bool:
        ts = self._missing.get(name.casefold())
        return ts is not None and self.clock() - ts < MISSING_FOR

    def cached(self, name: str, hint: str | None = None) -> CharacterInfo | None:
        """Score en mémoire pour CE personnage : jamais celui d'un homonyme d'un autre serveur."""
        if not hint and self.ambiguous(name):
            return None
        info, _ = self.cache.get(name, hint)
        return info

    def ambiguous(self, name: str, hint: str | None = None) -> list[str]:
        """Serveurs possibles si le nom est ambigu (et qu'aucun serveur n'est connu), sinon []."""
        if hint:
            return []
        servers = {i.server for i, _ in self.cache.candidates(name)}
        memo = self._ambiguous.get(name.casefold())
        if memo and self.clock() - memo[1] < MISSING_FOR:
            servers |= set(memo[0])
        return sorted(servers) if len(servers) > 1 else []

    def is_fresh(self, name: str, hint: str | None = None) -> bool:
        ts = self._fresh.get(self._key(name, hint))
        return ts is not None and self.clock() - ts < FRESH_FOR

    @staticmethod
    def _key(name: str, hint: str | None) -> str:
        return f"{name.casefold()}@{hint or ''}"

    def needs_network(self, name: str, hint: str | None = None) -> bool:
        """Vrai si une requête externe apporterait quelque chose (rien de récent localement)."""
        if self.known_missing(name) or self.is_fresh(name, hint) or self.ambiguous(name, hint):
            return False
        info, age = self.cache.get(name, hint)
        return age is None or age >= RECENT_SCORE or incomplete(info)

    def prefetch(self, name: str, hint: str | None = None, background: bool = False) -> Future | None:
        """Lance une recherche si rien de frais n'est connu et qu'aucune n'est en cours.

        hint : serveur probable (balise Abysse « [Isr] ») → requête directe sur ce serveur.
        background : préchargement (personnage observé) — jamais pendant une recherche de cible."""
        key = self._key(name, hint)
        if background and self.target_busy():
            return None
        if self.known_missing(name) or self.is_fresh(name, hint):
            return None
        if self.ambiguous(name, hint):
            return None                                  # homonymes sans serveur : rien à chercher
        info, age = self.cache.get(name, hint)
        if age is not None and age < RECENT_SCORE and not incomplete(info):
            return None                                  # score récent et complet : rien à faire
        with self._lock:
            if not background and not hint and key not in self._inflight:
                # Déjà en cours pour ce nom sur un serveur précis (préchargement d'un personnage
                # observé) : on réutilise cette requête au lieu d'en lancer une seconde.
                prefix = name.casefold() + "@"
                same = [k for k in self._inflight if k.startswith(prefix)]
                if len(same) == 1:
                    key = same[0]
            if not background:
                self._target_keys = {k for k in self._target_keys if k in self._inflight}
                self._target_keys.add(key)
            fut = self._inflight.get(key)
            if fut is None:
                fut = self._pool.submit(self._run, name, hint)
                self._inflight[key] = fut
            return fut

    def get(self, name: str, hint: str | None = None) -> CharacterInfo | None:
        """Dernier score connu (même ancien), + rafraîchissement lancé si besoin."""
        self.prefetch(name, hint)
        return self.cached(name, hint)

    def _pick(self, found: list, hint: str | None):
        """Résultats de la base pour un nom → celui de la cible, ou None si ambigu."""
        if hint:
            return next((r for r in found if r[0].server == hint), None)
        return found[0] if len(found) == 1 else None

    def _run(self, name: str, hint: str | None = None):
        key = self._key(name, hint)
        t0 = time.monotonic()
        done = {"full": False, "quick": False}
        emit_lock = threading.Lock()

        def emit(info, stage):
            from .metrics import STATS
            STATS.add("remote_quick" if stage.startswith("base") else "remote_full",
                      (time.monotonic() - t0) * 1000)
            if self.on_result:
                self.on_result(name, info)
            self.log(f"  {stage} {name} : {(time.monotonic() - t0) * 1000:.0f} ms"
                     + (f" → CP {info.combat_power}" if info else " → introuvable"))

        # 1. Base shugo.gg (rapide), affichée dès qu'elle répond, sauf si la fiche est déjà là.
        def store(found):
            self.cache.put_many(found)
            for info, _ in found:
                self.names.add(info.name)

        def on_quick(fut):
            try:
                found = fut.result()
            except Exception:
                return
            if not found:
                return
            with emit_lock:
                if done["quick"] or done["full"]:
                    return                               # déjà traité (réponse dans les délais)
                done["quick"] = True
                store(found)
                res = self._pick(found, hint)
                if res:
                    emit(self.cache.get(name, res[0].server)[0] or res[0], "base shugo.gg (tardive)")

        quick = self.client._pool.submit(self.client.quick_search_all, name)
        quick.add_done_callback(on_quick)
        try:
            found = quick.result(timeout=QUICK_WAIT)
        except FutureTimeout:
            found = []
        except Exception:
            found = []
        res = self._pick(found, hint) if found else None
        if found:
            with emit_lock:
                if not done["quick"]:
                    done["quick"] = True
                    store(found)
                    if res:
                        emit(self.cache.get(name, res[0].server)[0] or res[0], "base shugo.gg")
        if not hint and self.ambiguous(name):
            self.log(f"  {name} : homonymes sur {', '.join(self.ambiguous(name))} — score non attribué")
            with emit_lock:
                done["full"] = True
                if self.on_result:
                    self.on_result(name, None)
            with self._lock:
                self._inflight.pop(key, None)
            return None

        try:
            if res and time.time() - res[1] < RECENT_SCORE and not incomplete(res[0]):
                self._fresh[key] = self.clock()          # score de la base assez récent
                self._missing.pop(name.casefold(), None)
                return self.cache.get(name, res[0].server)[0] or res[0]

            # 2. Fiche (lente), sur le serveur exact si la base ou la mémoire le donne.
            server = hint or (res[0].server if res else None) or self.cache.server_of(name)
            try:
                info = self.client.lookup(name, hint=server)
            except NetworkError:
                info = None                              # pas de cache négatif sur une panne réseau
            except AmbiguousName as amb:
                self._ambiguous[name.casefold()] = (amb.servers, self.clock())
                self.log(f"  {name} : homonymes sur {', '.join(amb.servers)} — score non attribué")
                info = None
            else:
                if info:
                    self.cache.put(info)
                    self.names.add(info.name)
                    info = self.cache.get(name, info.server)[0] or info   # garde le rang importé
                    self._fresh[key] = self.clock()
                    self._missing.pop(name.casefold(), None)
                elif not (res or self.cache.get(name, hint)[0]):
                    self._missing[name.casefold()] = self.clock()
            with emit_lock:
                done["full"] = True
                emit(info or self.cached(name, hint), "fiche shugo.gg")
            return info
        finally:
            with self._lock:
                self._inflight.pop(key, None)
