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
from .shugo import CharacterInfo, NetworkError, ShugoClient

# Un score rafraîchi dans cette session il y a moins de 60 s : pas de nouvelle requête.
FRESH_FOR = 60
# Un score (mémoire ou base shugo.gg) de moins de 10 min suffit : pas de fiche lente.
RECENT_SCORE = 600
# Temps laissé à la recherche rapide pour fournir le serveur exact avant de lancer la fiche.
QUICK_WAIT = 0.35
# Un nom introuvable (PNJ au nom d'un seul mot, faute d'OCR) n'est pas redemandé pendant 1 h.
MISSING_FOR = 3600


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
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="player")
        self.names = NameIndex(cache.all_names())      # ~0,3 s pour 21 000 noms

    def rebuild_names(self):
        """Après un import : dictionnaire reconstruit (remplacé d'un coup, sans blocage)."""
        self.names = NameIndex(self.cache.all_names())

    def resolve(self, ocr_name: str, raw_line: str = "") -> str | None:
        """Vrai nom de joueur correspondant à une lecture OCR (niveau collé, lettre mal lue…)."""
        return self.names.resolve(ocr_name) or self.names.resolve_line(raw_line)

    def known_missing(self, name: str) -> bool:
        ts = self._missing.get(name.casefold())
        return ts is not None and self.clock() - ts < MISSING_FOR

    def cached(self, name: str) -> CharacterInfo | None:
        info, _ = self.cache.get(name)
        return info

    def is_fresh(self, name: str) -> bool:
        ts = self._fresh.get(name.casefold())
        return ts is not None and self.clock() - ts < FRESH_FOR

    def prefetch(self, name: str, hint: str | None = None) -> Future | None:
        """Lance une recherche si rien de frais n'est connu et qu'aucune n'est en cours.

        hint : serveur probable (balise Abysse « [Isr] ») → requête directe sur ce serveur."""
        key = name.casefold()
        if self.known_missing(name) or self.is_fresh(name):
            return None
        _, age = self.cache.get(name)
        if age is not None and age < RECENT_SCORE:
            return None                                  # score récent en mémoire : rien à faire
        with self._lock:
            fut = self._inflight.get(key)
            if fut is None:
                fut = self._pool.submit(self._run, name, hint)
                self._inflight[key] = fut
            return fut

    def get(self, name: str, hint: str | None = None) -> CharacterInfo | None:
        """Dernier score connu (même ancien), + rafraîchissement lancé si besoin."""
        self.prefetch(name, hint)
        return self.cached(name)

    def _run(self, name: str, hint: str | None = None):
        key = name.casefold()
        t0 = time.monotonic()
        done = {"full": False, "quick": False}
        emit_lock = threading.Lock()

        def emit(info, stage):
            if self.on_result:
                self.on_result(name, info)
            self.log(f"  {stage} {name} : {(time.monotonic() - t0) * 1000:.0f} ms"
                     + (f" → CP {info.combat_power}" if info else " → introuvable"))

        # 1. Base shugo.gg (rapide), affichée dès qu'elle répond, sauf si la fiche est déjà là.
        def on_quick(fut):
            try:
                res = fut.result()
            except Exception:
                return
            if not res:
                return
            with emit_lock:
                if done["quick"] or done["full"]:
                    return                               # déjà traité (réponse dans les délais)
                done["quick"] = True
                self.cache.put_many([res])
                self.names.add(res[0].name)
                emit(self.cache.get(name)[0] or res[0], "base shugo.gg (tardive)")

        quick = self.client._pool.submit(self.client.quick_search, name)
        quick.add_done_callback(on_quick)
        try:
            res = quick.result(timeout=QUICK_WAIT)
        except FutureTimeout:
            res = None
        except Exception:
            res = None
        if res:
            with emit_lock:
                if not done["quick"]:
                    done["quick"] = True
                    self.cache.put_many([res])
                    self.names.add(res[0].name)
                    emit(self.cache.get(name)[0] or res[0], "base shugo.gg")

        try:
            if res and time.time() - res[1] < RECENT_SCORE:
                self._fresh[key] = self.clock()          # score de la base assez récent
                self._missing.pop(key, None)
                return self.cache.get(name)[0] or res[0]

            # 2. Fiche (lente), sur le serveur exact si la base l'a donné.
            server = hint or (res[0].server if res else None) or self.cache.server_of(name)
            try:
                info = self.client.lookup(name, hint=server)
            except NetworkError:
                info = None                              # pas de cache négatif sur une panne réseau
            else:
                if info:
                    self.cache.put(info)
                    self.names.add(info.name)
                    info = self.cache.get(name)[0] or info   # garde le rang issu de l'import
                    self._fresh[key] = self.clock()
                    self._missing.pop(key, None)
                elif not (res or self.cache.get(name)[0]):
                    self._missing[key] = self.clock()
            with emit_lock:
                done["full"] = True
                emit(info or self.cache.get(name)[0], "fiche shugo.gg")
            return info
        finally:
            with self._lock:
                self._inflight.pop(key, None)
