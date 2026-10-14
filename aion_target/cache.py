"""Mémoire disque des joueurs déjà croisés (SQLite, inclus dans Python).

- Pas d'expiration dans le temps : seule la TAILLE est limitée (réglable, 100 Mo par défaut,
  soit ~650 000 joueurs à ~150 octets chacun ; toute l'Europe tient dans ~15 Mo).
- Plein : on retire les joueurs mis à jour il y a le plus longtemps.
- Chaque joueur garde l'heure de sa dernière mise à jour : le score connu s'affiche tout de
  suite, puis il est rafraîchi en arrière-plan (les scores sont toujours ceux du jour).
- Mémorise le serveur de chaque joueur : la recherche suivante ne fait qu'une requête.
- Lectures servies par un INDEX EN RAM (nom normalisé → serveur → profil), chargé une fois à
  l'ouverture puis tenu à jour à chaque écriture : la cible n'attend jamais SQLite (ni son
  verrou pendant un import). SQLite reste la source durable.
- Identité = NOM + SERVEUR (schéma v2) : deux homonymes de serveurs différents ne s'écrasent
  plus. Sans serveur connu, un nom présent sur plusieurs serveurs est AMBIGU : aucun score
  n'est attribué (get renvoie None, `candidates` liste les possibilités).
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from .shugo import CharacterInfo

DEFAULT_MAX_BYTES = 100 * 1024 * 1024
PURGE_EVERY = 500          # contrôle de la taille toutes les N écritures (et au démarrage)

SCHEMA_VERSION = 2
_SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    key     TEXT PRIMARY KEY,   -- « nom en minuscules@serveur » (v1 : nom seul)
    name    TEXT NOT NULL,
    server  TEXT NOT NULL,
    gs      INTEGER,
    cp      INTEGER,
    level   INTEGER,
    class   TEXT,
    url     TEXT,
    updated REAL NOT NULL,      -- horodatage (secondes) de la dernière mise à jour
    rank    INTEGER             -- rang régional par CP (import du classement)
);
CREATE INDEX IF NOT EXISTS players_updated ON players(updated);
"""


def make_key(name: str, server: str) -> str:
    return f"{name.casefold()}@{server}"


def _known(v):
    """Valeur exploitable : None, 0, négatif ou chaîne vide = inconnue (jamais « 0 » affiché)."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v <= 0:
        return None
    return v


def merge_profile(old: CharacterInfo | None, old_ts: float | None, new: CharacterInfo,
                  new_ts: float, prefer_new_on_tie: bool = False) -> tuple[tuple[CharacterInfo, float], bool]:
    """Règle UNIQUE de fusion d'un profil (même nom, MÊME serveur) — RAM et SQLite.

    - une valeur absente ne remplace jamais une valeur connue ;
    - une donnée plus récente remplace champ par champ les valeurs connues ;
    - une donnée plus ancienne (import du classement…) ne fait que compléter les champs vides ;
    - le rang régional vient toujours de la dernière donnée qui en fournit un ;
    - l'horodatage reste celui de la donnée la plus récente.
    Renvoie ((profil, horodatage), True si la donnée est nouvelle ou plus récente)."""
    fields = ("gear_score", "combat_power", "level", "char_class", "url")
    incoming = {f: _known(getattr(new, f)) for f in fields}
    if old is not None and old.server != new.server:
        raise ValueError("fusion de deux serveurs différents")
    if old is None:
        ts, newer = new_ts, True
        vals = incoming
        name = new.name
    else:
        newer = new_ts > old_ts or (prefer_new_on_tie and new_ts == old_ts)
        previous = {f: _known(getattr(old, f)) for f in fields}
        if newer:
            vals = {f: incoming[f] if incoming[f] is not None else previous[f] for f in fields}
            ts, name = new_ts, new.name
        else:
            vals = {f: previous[f] if previous[f] is not None else incoming[f] for f in fields}
            ts, name = old_ts, old.name
    rank = _known(new.rank)
    if rank is None and old is not None:
        rank = _known(old.rank)
    info = CharacterInfo(name, new.server, vals["gear_score"], vals["combat_power"], vals["level"],
                         vals["char_class"], vals["url"] or "", rank, updated=ts)
    return (info, ts), newer



class PlayerCache:
    def __init__(self, path: Path | str, clock=time.time, max_bytes: int = DEFAULT_MAX_BYTES):
        self.clock = clock
        self.max_bytes = max_bytes
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        self._writes = 0
        # Index RAM : nom en minuscules → {serveur: (profil, horodatage)}. Verrou court, séparé
        # de celui de SQLite : une lecture n'attend jamais une écriture disque.
        self._ram: dict[str, dict[str, tuple[CharacterInfo, float]]] = {}
        self._ram_lock = threading.Lock()
        self.last_error: str | None = None
        with self._lock, self._db:
            self._db.executescript(_SCHEMA)
            cols = {r[1] for r in self._db.execute("PRAGMA table_info(players)")}
            if "rank" not in cols:                       # base créée par une version précédente
                self._db.execute("ALTER TABLE players ADD COLUMN rank INTEGER")
        self._migrate(Path(path))
        with self._lock, self._db:
            self._db.execute("CREATE INDEX IF NOT EXISTS players_lname ON players(lower(name))")
        self.purge()
        self.reload_ram()

    # -- index RAM --
    def reload_ram(self):
        """(Re)charge tout l'index RAM depuis SQLite (~20 000 joueurs : quelques dizaines de ms)."""
        with self._lock:                         # aucune écriture ne peut se glisser entre les deux
            rows = self._db.execute(f"SELECT {self._COLS} FROM players").fetchall()
            ram: dict[str, dict[str, tuple[CharacterInfo, float]]] = {}
            for r in rows:
                info = CharacterInfo(r[0], r[1], r[2], r[3], r[4], r[5], r[6] or "", r[8], updated=r[7])
                ram.setdefault(r[0].casefold(), {})[r[1]] = (info, r[7])
            with self._ram_lock:
                self._ram = ram

    def _migrate(self, path: Path):
        """v1 (clé = nom seul) → v2 (clé = nom@serveur). Copie de sécurité avant toute écriture."""
        with self._lock:
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            old_rows = self._db.execute("SELECT COUNT(*) FROM players WHERE instr(key, '@') = 0"
                                        ).fetchone()[0]
        if version >= SCHEMA_VERSION:
            return
        if old_rows and path.exists():
            import shutil
            backup = path.with_name(path.name + ".bak-v1")
            if not backup.exists():
                with self._lock:
                    self._db.commit()
                shutil.copy2(path, backup)
        with self._lock, self._db:
            self._db.execute("UPDATE players SET key = lower(name) || '@' || server "
                             "WHERE instr(key, '@') = 0")
            self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    _COLS = "name, server, gs, cp, level, class, url, updated, rank"

    def _row(self, row) -> tuple[CharacterInfo, float]:
        info = CharacterInfo(row[0], row[1], row[2], row[3], row[4], row[5], row[6] or "", row[8],
                             updated=row[7])
        return info, self.clock() - row[7]

    def candidates(self, name: str) -> list[tuple[CharacterInfo, float]]:
        """Tous les personnages de ce nom (un par serveur), le plus récent d'abord (index RAM)."""
        with self._ram_lock:
            per = list((self._ram.get(name.casefold()) or {}).values())
        now = self.clock()
        per.sort(key=lambda e: e[1], reverse=True)
        return [(info, now - ts) for info, ts in per]

    def get(self, name: str, server: str | None = None) -> tuple[CharacterInfo | None, float | None]:
        """(profil, âge en secondes depuis sa dernière mise à jour) ou (None, None).

        Avec `server` : ce personnage précis. Sans : seulement si le nom est UNIQUE en mémoire
        (homonymes sur plusieurs serveurs → (None, None) : ne jamais attribuer un score douteux)."""
        if server:
            with self._ram_lock:
                e = (self._ram.get(name.casefold()) or {}).get(server)
            return (e[0], self.clock() - e[1]) if e else (None, None)
        found = self.candidates(name)
        return found[0] if len(found) == 1 else (None, None)

    def is_ambiguous(self, name: str) -> bool:
        return len(self.candidates(name)) > 1

    def server_of(self, name: str) -> str | None:
        info, _ = self.get(name)
        return info.server if info else None

    def put(self, info: CharacterInfo):
        """Observation en direct (fiche, groupe, réseau) : horodatée maintenant."""
        self._write([(info, self.clock())], live=True)

    def put_many(self, items: list[tuple[CharacterInfo, float]]) -> int:
        """Écrit en lot des profils horodatés (import, base shugo.gg). Ne remplace JAMAIS une
        valeur locale plus récente ; ne fait que compléter ce qui manque. Renvoie le nombre de
        profils nouveaux ou plus récents que la mémoire."""
        return self._write(items, live=False)

    def _write(self, items: list[tuple[CharacterInfo, float]], live: bool) -> int:
        """Seul chemin d'écriture : même règle (merge_profile) pour l'index RAM et SQLite.

        Les écritures sont sérialisées par le verrou SQLite ; la RAM est mise à jour APRÈS la
        base, sous ce même verrou : deux écritures concurrentes ne peuvent pas se croiser et la
        RAM reflète exactement la base. Les lectures (index RAM) n'attendent jamais ce verrou."""
        if not items:
            return 0
        written = 0
        with self._lock:
            merged: dict[tuple[str, str], tuple[CharacterInfo, float]] = {}
            with self._ram_lock:
                for info, ts in items:
                    if not info.name or not info.server:
                        continue                         # jamais de profil sans serveur
                    key = (info.name.casefold(), info.server)
                    old = merged.get(key) or (self._ram.get(key[0]) or {}).get(key[1])
                    m, newer = merge_profile(old[0] if old else None, old[1] if old else None,
                                             info, float(ts), prefer_new_on_tie=live)
                    written += newer
                    merged[key] = m
            rows = [(make_key(i.name, i.server), i.name, i.server, i.gear_score, i.combat_power,
                     i.level, i.char_class, i.url, ts, i.rank) for i, ts in merged.values()]
            try:
                with self._db:
                    self._db.executemany("INSERT OR REPLACE INTO players VALUES "
                                         "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
                self.last_error = None
            except sqlite3.Error as e:
                # Disque plein, base verrouillée… : l'affichage garde la donnée (RAM), la base
                # sera réécrite à la prochaine observation. Jamais d'exception vers la cible.
                self.last_error = f"{type(e).__name__}: {e}"
            with self._ram_lock:
                for (k, server), entry in merged.items():
                    self._ram.setdefault(k, {})[server] = entry
        self._writes += len(items)
        if self._writes >= PURGE_EVERY:
            self._writes = 0
            self.purge()
        return written

    def size_bytes(self) -> int:
        """Taille réellement occupée (pages utilisées, hors pages libres)."""
        with self._lock:
            pages = self._db.execute("PRAGMA page_count").fetchone()[0]
            free = self._db.execute("PRAGMA freelist_count").fetchone()[0]
            page = self._db.execute("PRAGMA page_size").fetchone()[0]
        return (pages - free) * page

    def purge(self):
        """Taille dépassée → suppression des joueurs les plus anciennement mis à jour."""
        if self.size_bytes() <= self.max_bytes:
            return
        while self.size_bytes() > self.max_bytes * 0.9 and len(self) > 0:
            n = max(1, len(self) // 10)                # par tranches de 10 %
            with self._lock, self._db:
                self._db.execute(
                    "DELETE FROM players WHERE key IN "
                    "(SELECT key FROM players ORDER BY updated ASC LIMIT ?)", (n,))
        with self._lock:
            self._db.execute("VACUUM")                 # rend la place au disque
        if self._ram:
            self.reload_ram()

    def clear(self):
        with self._lock, self._db:
            self._db.execute("DELETE FROM players")
        with self._ram_lock:
            self._ram = {}
        with self._lock:
            self._db.execute("VACUUM")

    def all_names(self) -> list[str]:
        with self._ram_lock:
            return [next(iter(per.values()))[0].name for per in self._ram.values() if per]

    def __len__(self):
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM players").fetchone()[0]

    def close(self):
        with self._lock:
            self._db.close()
