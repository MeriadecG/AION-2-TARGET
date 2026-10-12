"""Mémoire disque des joueurs déjà croisés (SQLite, inclus dans Python).

- Pas d'expiration dans le temps : seule la TAILLE est limitée (réglable, 100 Mo par défaut,
  soit ~650 000 joueurs à ~150 octets chacun ; toute l'Europe tient dans ~15 Mo).
- Plein : on retire les joueurs mis à jour il y a le plus longtemps.
- Chaque joueur garde l'heure de sa dernière mise à jour : le score connu s'affiche tout de
  suite, puis il est rafraîchi en arrière-plan (les scores sont toujours ceux du jour).
- Mémorise le serveur de chaque joueur : la recherche suivante ne fait qu'une requête.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from .shugo import CharacterInfo

DEFAULT_MAX_BYTES = 100 * 1024 * 1024
PURGE_EVERY = 500          # contrôle de la taille toutes les N écritures (et au démarrage)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    key     TEXT PRIMARY KEY,   -- nom en minuscules
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


class PlayerCache:
    def __init__(self, path: Path | str, clock=time.time, max_bytes: int = DEFAULT_MAX_BYTES):
        self.clock = clock
        self.max_bytes = max_bytes
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        self._writes = 0
        with self._lock, self._db:
            self._db.executescript(_SCHEMA)
            cols = {r[1] for r in self._db.execute("PRAGMA table_info(players)")}
            if "rank" not in cols:                       # base créée par une version précédente
                self._db.execute("ALTER TABLE players ADD COLUMN rank INTEGER")
        self.purge()

    def get(self, name: str) -> tuple[CharacterInfo | None, float | None]:
        """(profil, âge en secondes depuis sa dernière mise à jour) ou (None, None)."""
        with self._lock:
            row = self._db.execute(
                "SELECT name, server, gs, cp, level, class, url, updated, rank FROM players WHERE key = ?",
                (name.casefold(),)).fetchone()
        if not row:
            return None, None
        info = CharacterInfo(row[0], row[1], row[2], row[3], row[4], row[5], row[6] or "", row[8])
        return info, self.clock() - row[7]

    def server_of(self, name: str) -> str | None:
        info, _ = self.get(name)
        return info.server if info else None

    def put(self, info: CharacterInfo):
        with self._lock, self._db:
            # La fiche shugo.gg ne donne pas le rang : on garde celui de l'import.
            self._db.execute(
                "INSERT INTO players VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET name=excluded.name, server=excluded.server, "
                "gs=excluded.gs, cp=excluded.cp, level=excluded.level, class=excluded.class, "
                "url=excluded.url, updated=excluded.updated, "
                "rank=COALESCE(excluded.rank, players.rank)",
                (info.name.casefold(), info.name, info.server, info.gear_score, info.combat_power,
                 info.level, info.char_class, info.url, self.clock(), info.rank))
        self._writes += 1
        if self._writes % PURGE_EVERY == 0:
            self.purge()

    def put_many(self, items: list[tuple[CharacterInfo, float]]) -> int:
        """Écrit en lot des profils horodatés (import). Ne remplace JAMAIS un profil local
        plus récent (ex. joueur rafraîchi en jeu il y a 5 min). Renvoie le nombre écrit."""
        with self._lock, self._db:
            before = self._db.total_changes
            self._db.executemany(
                "INSERT INTO players VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET name=excluded.name, server=excluded.server, "
                "gs=excluded.gs, cp=excluded.cp, level=excluded.level, class=excluded.class, "
                "url=excluded.url, updated=excluded.updated, rank=excluded.rank "
                "WHERE excluded.updated > players.updated",
                [(i.name.casefold(), i.name, i.server, i.gear_score, i.combat_power, i.level,
                  i.char_class, i.url, ts, i.rank) for i, ts in items])
            written = self._db.total_changes - before
            # Le rang du classement est toujours le plus récent, même si le score local l'est aussi.
            self._db.executemany("UPDATE players SET rank = ? WHERE key = ?",
                                 [(i.rank, i.name.casefold()) for i, _ in items if i.rank])
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

    def clear(self):
        with self._lock, self._db:
            self._db.execute("DELETE FROM players")
        with self._lock:
            self._db.execute("VACUUM")

    def all_names(self) -> list[str]:
        with self._lock:
            return [r[0] for r in self._db.execute("SELECT name FROM players")]

    def __len__(self):
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM players").fetchone()[0]

    def close(self):
        with self._lock:
            self._db.close()
