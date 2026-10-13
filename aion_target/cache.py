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
        with self._lock:
            rows = self._db.execute(f"SELECT {self._COLS} FROM players").fetchall()
        ram: dict[str, dict[str, tuple[CharacterInfo, float]]] = {}
        for r in rows:
            info = CharacterInfo(r[0], r[1], r[2], r[3], r[4], r[5], r[6] or "", r[8], updated=r[7])
            ram.setdefault(r[0].casefold(), {})[r[1]] = (info, r[7])
        with self._ram_lock:
            self._ram = ram

    def _ram_put(self, info: CharacterInfo, ts: float, only_newer: bool = False, keep_rank: bool = True):
        k = info.name.casefold()
        with self._ram_lock:
            per = self._ram.setdefault(k, {})
            old = per.get(info.server)
            if old and only_newer and ts <= old[1]:
                if info.rank and old[0].rank != info.rank:     # le rang du classement, toujours
                    o = old[0]
                    per[info.server] = (CharacterInfo(o.name, o.server, o.gear_score, o.combat_power,
                                                      o.level, o.char_class, o.url, info.rank,
                                                      updated=o.updated), old[1])
                return
            o = old[0] if old else None
            rank = info.rank if info.rank is not None or not keep_rank or not o else o.rank
            gs, cp = info.gear_score, info.combat_power
            if keep_rank and o:        # écriture partielle (groupe, fiche sans GS) : on garde ce qu'on savait
                level, cls, url = info.level or o.level, info.char_class or o.char_class, info.url or o.url
                gs = gs if gs is not None else o.gear_score
                cp = cp if cp is not None else o.combat_power
            else:
                level, cls, url = info.level, info.char_class, info.url or ""
            per[info.server] = (CharacterInfo(info.name, info.server, gs, cp,
                                              level, cls, url, rank, updated=ts), ts)

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
        ts = self.clock()
        self._ram_put(info, ts)
        with self._lock, self._db:
            # La fiche shugo.gg ne donne pas le rang : on garde celui de l'import.
            self._db.execute(
                "INSERT INTO players VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET name=excluded.name, server=excluded.server, "
                "gs=COALESCE(excluded.gs, players.gs), cp=COALESCE(excluded.cp, players.cp), "
                "level=COALESCE(excluded.level, players.level), "
                "class=COALESCE(excluded.class, players.class), "
                "url=COALESCE(NULLIF(excluded.url, ''), players.url), updated=excluded.updated, "
                "rank=COALESCE(excluded.rank, players.rank)",
                (make_key(info.name, info.server), info.name, info.server, info.gear_score, info.combat_power,
                 info.level, info.char_class, info.url, ts, info.rank))
        self._writes += 1
        if self._writes % PURGE_EVERY == 0:
            self.purge()

    def put_many(self, items: list[tuple[CharacterInfo, float]]) -> int:
        """Écrit en lot des profils horodatés (import). Ne remplace JAMAIS un profil local
        plus récent (ex. joueur rafraîchi en jeu il y a 5 min). Renvoie le nombre écrit."""
        for i, ts in items:
            self._ram_put(i, ts, only_newer=True, keep_rank=False)
        with self._lock, self._db:
            before = self._db.total_changes
            self._db.executemany(
                "INSERT INTO players VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET name=excluded.name, server=excluded.server, "
                "gs=excluded.gs, cp=excluded.cp, level=excluded.level, class=excluded.class, "
                "url=excluded.url, updated=excluded.updated, rank=excluded.rank "
                "WHERE excluded.updated > players.updated",
                [(make_key(i.name, i.server), i.name, i.server, i.gear_score, i.combat_power, i.level,
                  i.char_class, i.url, ts, i.rank) for i, ts in items])
            written = self._db.total_changes - before
            # Le rang du classement est toujours le plus récent, même si le score local l'est aussi.
            self._db.executemany("UPDATE players SET rank = ? WHERE key = ?",
                                 [(i.rank, make_key(i.name, i.server)) for i, _ in items if i.rank])
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
