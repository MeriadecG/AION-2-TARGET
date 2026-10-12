"""Client du moteur réseau (binaire Rust `aion-engine`, cœur d'analyse d'A2Tools, GPL-3.0).

Le moteur est un PROCESSUS SÉPARÉ, lancé seulement si le module DPS est activé : s'il manque,
plante ou n'a pas les droits de capture, le reste de l'appli (Target OCR, Notifications,
Farm) continue normalement. Protocole : une ligne JSON par message, champ « v » = version.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from PyQt6.QtCore import QObject, QProcess, QTimer, pyqtSignal

PROTOCOL = 1
EXE = "aion-engine.exe" if sys.platform == "win32" else "aion-engine"
ROOT = Path(__file__).resolve().parent.parent

# Noms de classes renvoyés par le cœur d'analyse (coréen) → français.
JOBS = {"검성": "Gladiateur", "수호성": "Templier", "살성": "Assassin", "궁성": "Rôdeur",
        "마도성": "Sorcier", "정령성": "Spiritualiste", "치유성": "Clerc", "호법성": "Aède"}


def job_fr(job: str) -> str:
    return JOBS.get(job, job or "?")


def find_engine() -> Path | None:
    """Binaire du moteur : à côté de l'appli installée (bin/), sinon build de développement."""
    cands = [ROOT / "bin" / EXE, ROOT.parent / "bin" / EXE,
             ROOT / "engine" / "target" / "release" / EXE]
    env = os.environ.get("AION_TARGET_ENGINE")
    if env:
        cands.insert(0, Path(env))
    for c in cands:
        if c.is_file():
            return c
    return None


def has_capture_capability(path: Path) -> bool | None:
    """Linux : le binaire a-t-il cap_net_raw ? (None = impossible à savoir ici)."""
    if not sys.platform.startswith("linux"):
        return None
    getcap = shutil.which("getcap") or "/usr/sbin/getcap"
    if not Path(getcap).exists():
        return None
    import subprocess
    try:
        out = subprocess.run([getcap, str(path)], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return None
    return "cap_net_raw" in out


def grant_capture_command(path: Path) -> list[str]:
    """Commande (avec demande de mot de passe graphique) qui autorise la capture sous Linux."""
    setcap = shutil.which("setcap") or "/usr/sbin/setcap"
    return ["pkexec", setcap, "cap_net_raw=ep", str(path)]


class EngineClient(QObject):
    status = pyqtSignal(dict)       # état de la capture (running, missing_library, no_permission…)
    dps = pyqtSignal(dict)          # tableau DPS en cours (2 fois par seconde)
    players = pyqtSignal(list)      # personnages OBSERVÉS (pas la liste complète de l'instance)
    party = pyqtSignal(list)        # liste du groupe (GS / CP envoyés par le jeu)
    fight = pyqtSignal(dict)        # combat de boss terminé (enregistrement complet)
    kill = pyqtSignal(dict)         # monstre mort en combat (verdict d'attribution inclus)
    stopped = pyqtSignal(str)       # moteur arrêté (raison)

    MAX_RESTARTS = 3

    def __init__(self, log, mode: str = "bossTargets", parent=None):
        super().__init__(parent)
        self.log = log
        self.mode = mode
        self.proc: QProcess | None = None
        self.path: Path | None = None
        self._buf = b""
        self._restarts = 0
        self._wanted = False
        self.last_status: dict = {}
        self.ping_ms: int | None = None
        self.publish_dps = True

    # --- cycle de vie ---
    def start(self) -> str | None:
        """Démarre le moteur. Renvoie un message d'erreur, ou None."""
        self._wanted = True
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning:
            return None
        self.path = find_engine()
        if not self.path:
            return "moteur introuvable (bin/aion-engine) : réinstalle Aion Target"
        self.proc = QProcess(self)
        self.proc.setProgram(str(self.path))
        self.proc.setArguments(["--mode", self.mode])
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.readyReadStandardError.connect(
            lambda: self.log("Moteur : " + bytes(self.proc.readAllStandardError()).decode("utf-8", "replace").strip()[:300]))
        self.proc.finished.connect(self._finished)
        self.proc.started.connect(lambda: self.publish_dps or self.send({"cmd": "publish_dps", "value": False}))
        self.proc.start()
        self.log(f"Moteur : démarrage ({self.path.name})")
        return None

    def stop(self):
        self._wanted = False
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning:
            self.send({"cmd": "quit"})
            self.proc.closeWriteChannel()
            if not self.proc.waitForFinished(1500):
                self.proc.kill()
                self.proc.waitForFinished(1000)
        self.proc = None

    @property
    def running(self) -> bool:
        return bool(self.proc and self.proc.state() == QProcess.ProcessState.Running)

    def send(self, cmd: dict):
        if self.proc and self.proc.state() == QProcess.ProcessState.Running:
            self.proc.write((json.dumps(cmd) + "\n").encode())

    def reset(self):
        self.send({"cmd": "reset"})

    def set_publish_dps(self, on: bool):
        """Observation seule : le moteur n'envoie plus le DPS (commande ignorée par un ancien moteur)."""
        self.publish_dps = on
        self.send({"cmd": "publish_dps", "value": on})

    def set_mode(self, mode: str):
        self.mode = mode
        self.send({"cmd": "mode", "value": mode})

    # --- messages ---
    def _read(self):
        self._buf += bytes(self.proc.readAllStandardOutput())
        *lines, self._buf = self._buf.split(b"\n")
        for raw in lines:
            if raw.strip():
                self.handle_line(raw.decode("utf-8", "replace"))

    def handle_line(self, line: str):
        try:
            msg = json.loads(line)
        except ValueError:
            return
        if msg.get("v") != PROTOCOL:
            self.log(f"Moteur : protocole {msg.get('v')} inattendu (attendu {PROTOCOL}) — réinstalle")
            return
        kind = msg.get("type")
        if kind == "status":
            self.last_status = {**self.last_status, **msg}
            self.status.emit(dict(self.last_status))
            if msg.get("game_port"):
                self.log(f"Moteur : flux du jeu repéré (port {msg['game_port']})")
        elif kind == "dps":
            self.ping_ms = msg.get("ping_ms")
            self.dps.emit(msg)
        elif kind == "players":
            self.players.emit(msg.get("observed") or [])
        elif kind == "party":
            self.party.emit(msg.get("members") or [])
        elif kind == "fight":
            self.fight.emit(msg.get("record") or {})
        elif kind == "kill":
            self.kill.emit(msg)
        elif kind in ("log", "error"):
            self.log("Moteur : " + str(msg.get("msg") or msg.get("detail")))
            if kind == "error":
                self.last_status = {**self.last_status, "error": msg.get("detail")}
                self.status.emit(dict(self.last_status))

    def _finished(self, code, _status):
        self.log(f"Moteur arrêté (code {code})")
        reason = self.last_status.get("capture") or f"code {code}"
        self.stopped.emit(str(reason))
        fatal = self.last_status.get("capture") in ("missing_library", "no_permission")
        if self._wanted and not fatal and self._restarts < self.MAX_RESTARTS:
            self._restarts += 1
            QTimer.singleShot(2000 * self._restarts, self.start)
