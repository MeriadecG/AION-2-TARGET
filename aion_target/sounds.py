"""Sons des notifications : choix, volume, lecture non bloquante.

L'appli joue le son elle-même (le bureau reçoit « suppress-sound ») : même rendu sous KDE et
Windows, et le volume est réglable. Aucune dépendance en plus :
- Linux : pw-play (PipeWire, Nobara), sinon paplay, sinon aplay ;
- Windows : winsound (bibliothèque standard, fichiers WAV).
Le volume d'un WAV est appliqué en écrivant une copie mise à l'échelle (mise en cache).
"""
from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
import wave
from array import array
from pathlib import Path

SOUND_DIR = Path(__file__).parent / "assets" / "sounds"
BUILTIN = {"carillon": "Carillon", "cloche": "Cloche", "cristal": "Cristal", "gong": "Gong",
           "appel": "Appel (arpège)", "alerte": "Alerte (3 bips)"}
DEFAULT_SOUND = "builtin:carillon"
SYSTEM_DIRS = ("/usr/share/sounds/ocean/stereo", "/usr/share/sounds/freedesktop/stereo")
SYSTEM_EXT = (".oga", ".ogg", ".wav")
CUSTOM_EXT_LINUX = "Sons (*.wav *.ogg *.oga *.flac)"
CUSTOM_EXT_WIN = "Sons WAV (*.wav)"


def choices() -> list[tuple[str, str]]:
    """(identifiant, libellé) : sons intégrés puis sons du système (Linux)."""
    out = [(f"builtin:{k}", v) for k, v in BUILTIN.items() if (SOUND_DIR / f"{k}.wav").exists()]
    if sys.platform != "win32":
        seen = set()
        for d in SYSTEM_DIRS:
            p = Path(d)
            if not p.is_dir():
                continue
            theme = "KDE" if "ocean" in d else "système"
            for f in sorted(p.iterdir()):
                if f.suffix in SYSTEM_EXT and f.stem not in seen:
                    seen.add(f.stem)
                    out.append((f"file:{f}", f"{theme} : {f.stem.replace('-', ' ')}"))
    return out


def resolve(sound_id: str) -> Path | None:
    """Identifiant → fichier existant (None si introuvable)."""
    if sound_id.startswith("builtin:"):
        p = SOUND_DIR / f"{sound_id.split(':', 1)[1]}.wav"
    elif sound_id.startswith("file:"):
        p = Path(sound_id.split(":", 1)[1])
    else:
        return None
    return p if p.is_file() else None


def label(sound_id: str) -> str:
    for sid, lab in choices():
        if sid == sound_id:
            return lab
    p = resolve(sound_id)
    return f"Fichier : {p.name}" if p else "Son introuvable"


def scaled_wav(path: Path, volume: int) -> Path:
    """Copie du WAV au volume demandé (0-100), mise en cache. Original si format non géré."""
    volume = max(0, min(100, int(volume)))
    if volume >= 100 or path.suffix.lower() != ".wav":
        return path
    key = hashlib.sha1(f"{path}|{path.stat().st_mtime_ns}|{volume}".encode()).hexdigest()[:16]
    out = Path(tempfile.gettempdir()) / f"aion-target-sound-{key}.wav"
    if out.exists():
        return out
    try:
        with wave.open(str(path), "rb") as r:
            params = r.getparams()
            if params.sampwidth != 2:
                return path
            data = array("h", r.readframes(params.nframes))
        if sys.byteorder == "big":
            data.byteswap()
        f = volume / 100
        data = array("h", (int(s * f) for s in data))
        if sys.byteorder == "big":
            data.byteswap()
        with wave.open(str(out), "wb") as w:
            w.setparams(params)
            w.writeframes(data.tobytes())
        return out
    except (OSError, wave.Error, EOFError):
        return path


def player_command(path: Path, volume: int) -> list[str] | None:
    """Commande de lecture Linux (testable sans jouer)."""
    v = max(0, min(100, int(volume))) / 100
    if shutil.which("pw-play"):
        return ["pw-play", f"--volume={v:.2f}", str(path)]
    if shutil.which("paplay"):
        return ["paplay", f"--volume={int(v * 65536)}", str(path)]
    if shutil.which("aplay") and path.suffix.lower() == ".wav":
        return ["aplay", "-q", str(scaled_wav(path, volume))]
    return None


def play(sound_id: str, volume: int = 80) -> tuple[bool, str]:
    """Lance le son sans attendre. Renvoie (ok, détail)."""
    path = resolve(sound_id) or resolve(DEFAULT_SOUND)
    if path is None:
        return False, "son introuvable"
    if volume <= 0:
        return True, "volume à 0"
    if sys.platform == "win32":
        try:
            import winsound
            if path.suffix.lower() != ".wav":
                return False, "Windows ne lit que les fichiers WAV"
            winsound.PlaySound(str(scaled_wav(path, volume)),
                               winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            return True, "winsound"
        except Exception as e:
            return False, str(e)
    cmd = player_command(path, volume)
    if cmd is None:
        return False, "aucun lecteur audio (pw-play, paplay ou aplay)"
    from PyQt6.QtCore import QProcess
    ok = QProcess.startDetached(cmd[0], cmd[1:])[0]
    return ok, cmd[0]
