"""Historique local : combats (moteur DPS) et rencontres de joueurs (Target + moteur).

SQLite `history.db` dans le dossier de configuration, migrations par `PRAGMA user_version`
(jamais de suppression de données existantes). Identité d'un joueur = NOM + SERVEUR ; chaque
rencontre garde sa source, sa confiance et l'heure de la donnée GS/CP.

Confiance : « exact » (serveur lu : balise Abysse, paquet du jeu, liste de groupe),
« unique » (nom présent sur un seul serveur connu), « ambigu » (homonymes : jamais de score).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = 1
MIGRATIONS = {
    1: """
    CREATE TABLE IF NOT EXISTS fights (
        id          TEXT PRIMARY KEY,
        started_ms  INTEGER NOT NULL,
        duration_ms INTEGER NOT NULL,
        boss        TEXT,
        mob_code    INTEGER,
        dungeon_id  INTEGER,
        server_id   INTEGER,
        total       INTEGER,
        ping_ms     INTEGER,            -- ping moyen mesuré pendant le combat (NULL = inconnu)
        record      TEXT NOT NULL,      -- enregistrement complet du moteur (JSON)
        saved_at    REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS fights_started ON fights(started_ms);
    CREATE TABLE IF NOT EXISTS fight_actors (
        fight_id TEXT NOT NULL REFERENCES fights(id) ON DELETE CASCADE,
        actor_id INTEGER, name TEXT, job TEXT, damage INTEGER, dps REAL,
        PRIMARY KEY (fight_id, actor_id)
    );
    CREATE TABLE IF NOT EXISTS encounters (
        name        TEXT NOT NULL,
        server      TEXT NOT NULL DEFAULT '',   -- '' = serveur inconnu
        first_seen  REAL NOT NULL,
        last_seen   REAL NOT NULL,
        seen_count  INTEGER NOT NULL DEFAULT 1,
        gs          INTEGER,
        cp          INTEGER,
        data_at     REAL,                       -- âge de la donnée GS/CP (horodatage)
        source      TEXT,                       -- ocr | reseau | groupe
        confidence  TEXT,                       -- exact | unique | ambigu
        char_id     TEXT,                       -- identifiant stable si connu (dbid du groupe)
        favorite    INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (name, server)
    );
    CREATE INDEX IF NOT EXISTS encounters_seen ON encounters(last_seen);
    """,
}


@dataclass
class Encounter:
    name: str
    server: str
    first_seen: float
    last_seen: float
    seen_count: int
    gs: int | None
    cp: int | None
    data_at: float | None
    source: str
    confidence: str
    char_id: str | None
    favorite: bool


class History:
    def __init__(self, path: Path | str, max_fights: int = 500, max_encounters: int = 20000, clock=time.time):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.max_fights, self.max_encounters = max_fights, max_encounters
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.Lock()
        self._migrate()

    def _migrate(self):
        with self._lock, self._db:
            v = self._db.execute("PRAGMA user_version").fetchone()[0]
            for target in range(v + 1, SCHEMA_VERSION + 1):
                self._db.executescript(MIGRATIONS[target])
                self._db.execute(f"PRAGMA user_version = {target}")

    @property
    def version(self) -> int:
        with self._lock:
            return self._db.execute("PRAGMA user_version").fetchone()[0]

    # --- combats ---
    def add_fight(self, record: dict, ping_ms: int | None = None) -> bool:
        """Enregistre un combat du moteur. Renvoie False s'il est déjà connu."""
        fid = str(record.get("id") or "")
        if not fid:
            return False
        actors = record.get("actors") or []
        details = record.get("details") or {}
        dmg_by_actor: dict = {}
        for sk in details.get("skills") or []:
            dmg_by_actor[sk.get("actorId")] = dmg_by_actor.get(sk.get("actorId"), 0) + int(sk.get("dmg") or 0)
        pings = [p.get("pingMs") for p in details.get("pingHistory") or [] if p.get("pingMs")]
        if ping_ms is None and pings:
            ping_ms = round(sum(pings) / len(pings))
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT OR IGNORE INTO fights VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (fid, int(record.get("startTimeMs") or record.get("start_time_ms") or 0),
                 int(record.get("durationMs") or record.get("duration_ms") or 0),
                 record.get("bossName") or record.get("boss_name") or "",
                 record.get("mobCode") or record.get("mob_code"),
                 record.get("dungeonId") or record.get("dungeon_id"),
                 record.get("serverId") or record.get("server_id"),
                 record.get("totalDamage") or record.get("total_damage"),
                 ping_ms, json.dumps(record, ensure_ascii=False), self.clock()))
            if cur.rowcount == 0:
                return False
            dur = max(1, int(record.get("durationMs") or record.get("duration_ms") or 0)) / 1000
            for a in actors:
                aid = a.get("actorId", a.get("actor_id"))
                dmg = int(dmg_by_actor.get(aid) or a.get("totalDamage") or a.get("damage") or 0)
                self._db.execute("INSERT OR REPLACE INTO fight_actors VALUES (?,?,?,?,?,?)",
                                 (fid, aid, a.get("nickname") or a.get("name") or str(aid),
                                  a.get("job") or "", dmg, dmg / dur))
        self._trim_fights()
        return True

    def fight_count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM fights").fetchone()[0]

    def fights(self, search: str = "", limit: int = 200) -> list[dict]:
        q = "SELECT id, started_ms, duration_ms, boss, total, ping_ms FROM fights"
        args: list = []
        if search:
            q += (" WHERE boss LIKE ? OR id IN (SELECT fight_id FROM fight_actors WHERE name LIKE ?)")
            args += [f"%{search}%", f"%{search}%"]
        q += " ORDER BY started_ms DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._db.execute(q, args).fetchall()
        return [{"id": r[0], "started_ms": r[1], "duration_ms": r[2], "boss": r[3], "total": r[4],
                 "ping_ms": r[5]} for r in rows]

    def fight_detail(self, fid: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT record FROM fights WHERE id = ?", (fid,)).fetchone()
            actors = self._db.execute(
                "SELECT actor_id, name, job, damage, dps FROM fight_actors WHERE fight_id = ? "
                "ORDER BY damage DESC", (fid,)).fetchall()
        if not row:
            return None
        rec = json.loads(row[0])
        skills: dict = {}
        for sk in (rec.get("details") or {}).get("skills") or []:
            skills.setdefault(sk.get("actorId"), []).append(
                {"name": sk.get("name"), "damage": sk.get("dmg"), "hits": sk.get("time"), "crit": sk.get("crit")})
        for lst in skills.values():
            lst.sort(key=lambda x: -(x["damage"] or 0))
        return {"record": rec, "skills": skills,
                "actors": [{"id": a[0], "name": a[1], "job": a[2], "damage": a[3], "dps": a[4]} for a in actors]}

    def delete_fight(self, fid: str):
        with self._lock, self._db:
            self._db.execute("DELETE FROM fights WHERE id = ?", (fid,))

    def _trim_fights(self):
        with self._lock, self._db:
            self._db.execute("DELETE FROM fights WHERE id IN (SELECT id FROM fights ORDER BY started_ms DESC "
                             "LIMIT -1 OFFSET ?)", (self.max_fights,))

    # --- rencontres ---
    def record_encounter(self, name: str, server: str | None, *, source: str, confidence: str,
                         gs: int | None = None, cp: int | None = None, data_at: float | None = None,
                         char_id: str | None = None, min_gap: float = 60.0):
        """Note une rencontre. Plusieurs lectures en moins de `min_gap` s = une seule rencontre.
        Un joueur AMBIGU n'est jamais enregistré avec un score."""
        if not name:
            return
        server = server or ""
        if confidence == "ambigu":
            gs = cp = data_at = None
        now = self.clock()
        with self._lock, self._db:
            row = self._db.execute("SELECT last_seen, data_at FROM encounters WHERE name = ? AND server = ?",
                                   (name, server)).fetchone()
            if row is None:
                self._db.execute(
                    "INSERT INTO encounters (name, server, first_seen, last_seen, seen_count, gs, cp, data_at,"
                    " source, confidence, char_id) VALUES (?,?,?,?,1,?,?,?,?,?,?)",
                    (name, server, now, now, gs, cp, data_at, source, confidence, char_id))
            else:
                newer = data_at is not None and (row[1] is None or data_at >= row[1])
                self._db.execute(
                    "UPDATE encounters SET last_seen = ?, seen_count = seen_count + ?, source = ?,"
                    " confidence = ?, char_id = COALESCE(?, char_id),"
                    " gs = CASE WHEN ? THEN ? ELSE gs END, cp = CASE WHEN ? THEN ? ELSE cp END,"
                    " data_at = CASE WHEN ? THEN ? ELSE data_at END WHERE name = ? AND server = ?",
                    (now, 1 if now - row[0] >= min_gap else 0, source, confidence, char_id,
                     newer, gs, newer, cp, newer, data_at, name, server))
        self._trim_encounters()

    def encounters(self, search: str = "", favorites_only: bool = False, order: str = "recent",
                   limit: int = 500) -> list[Encounter]:
        order_sql = {"recent": "last_seen DESC", "count": "seen_count DESC", "cp": "cp DESC",
                     "name": "name COLLATE NOCASE"}.get(order, "last_seen DESC")
        q = "SELECT * FROM encounters WHERE 1=1"
        args: list = []
        if search:
            q += " AND (name LIKE ? OR server LIKE ?)"
            args += [f"%{search}%", f"%{search}%"]
        if favorites_only:
            q += " AND favorite = 1"
        q += f" ORDER BY {order_sql} LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._db.execute(q, args).fetchall()
        return [Encounter(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8] or "", r[9] or "", r[10],
                          bool(r[11])) for r in rows]

    def set_favorite(self, name: str, server: str, on: bool):
        with self._lock, self._db:
            self._db.execute("UPDATE encounters SET favorite = ? WHERE name = ? AND server = ?",
                             (1 if on else 0, name, server or ""))

    def delete_encounter(self, name: str, server: str):
        with self._lock, self._db:
            self._db.execute("DELETE FROM encounters WHERE name = ? AND server = ?", (name, server or ""))

    def _trim_encounters(self):
        with self._lock, self._db:
            self._db.execute(
                "DELETE FROM encounters WHERE favorite = 0 AND rowid IN (SELECT rowid FROM encounters "
                "WHERE favorite = 0 ORDER BY last_seen DESC LIMIT -1 OFFSET ?)", (self.max_encounters,))

    def counts(self) -> tuple[int, int]:
        with self._lock:
            f = self._db.execute("SELECT COUNT(*) FROM fights").fetchone()[0]
            e = self._db.execute("SELECT COUNT(*) FROM encounters").fetchone()[0]
        return f, e

    def close(self):
        with self._lock:
            self._db.close()
