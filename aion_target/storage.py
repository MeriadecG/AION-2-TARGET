"""Écriture et lecture SÛRES des fichiers JSON de l'utilisateur (réglages, préférences, progression).

Écriture (atomic_write_json) :
  1. sérialisation puis relecture de contrôle (jamais de JSON invalide sur le disque) ;
  2. fichier temporaire dans le MÊME dossier, vidé sur le disque (fsync) ;
  3. la version précédente, si elle est lisible, devient la sauvegarde « .bak » ;
  4. os.replace (atomique : l'ancien fichier OU le nouveau, jamais un mélange), quelques essais
     si Windows le refuse un instant (antivirus, indexation) ;
  5. en cas d'erreur, le fichier temporaire est supprimé et l'ancien fichier reste intact.

Lecture (load_json) :
  - fichier principal lisible et valide → utilisé ;
  - illisible (JSON coupé, mauvais types…) → mis de côté en « .corrupt-DATE » (jamais supprimé),
    restauration de la sauvegarde « .bak » si elle est valide, sinon valeurs par défaut ;
  - fichier inaccessible (droits) → valeurs par défaut EN MÉMOIRE, et `writable=False` : l'appelant
    n'écrase pas un fichier qu'il n'a pas pu lire.
Chaque incident produit un message pour l'utilisateur (notices / pop_notices).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

_notices: list[str] = []
_notices_lock = threading.Lock()


def notify(message: str):
    with _notices_lock:
        if message not in _notices:
            _notices.append(message)


def pop_notices() -> list[str]:
    """Messages à montrer à l'utilisateur (fichier restauré, réglage invalide corrigé…)."""
    with _notices_lock:
        out, _notices[:] = list(_notices), []
    return out


def backup_path(path: Path) -> Path:
    return path.with_name(path.name + ".bak")


def _fsync_dir(folder: Path):
    if sys.platform == "win32":
        return                                   # pas de fsync de dossier sous Windows
    try:
        fd = os.open(str(folder), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _replace(src: Path, dst: Path, attempts: int = 5, delay: float = 0.1):
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:                  # Windows : fichier ouvert un instant ailleurs
            if i == attempts - 1:
                raise
            time.sleep(delay * (i + 1))


def atomic_write_bytes(path: Path | str, data: bytes, *, fsync: bool = True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            if fsync:
                os.fsync(f.fileno())
        _replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    if fsync:
        _fsync_dir(path.parent)


def _parse(raw: bytes, validate: Callable[[Any], Any] | None):
    data = json.loads(raw.decode("utf-8"))
    return validate(data) if validate else data


def atomic_write_json(path: Path | str, obj: Any, *, backup: bool = True, indent: int | None = 2,
                      validate: Callable[[Any], Any] | None = None, fsync: bool = True):
    """Écrit `obj` en JSON de façon atomique. `backup` : la version précédente valide → `.bak`."""
    path = Path(path)
    raw = json.dumps(obj, indent=indent, ensure_ascii=False).encode("utf-8")
    _parse(raw, validate)                        # relecture de contrôle : lève si invalide
    if backup and path.exists():
        try:
            previous = path.read_bytes()
            _parse(previous, validate)           # on ne sauvegarde qu'une version VALIDE
            try:
                same = backup_path(path).read_bytes() == previous
            except OSError:
                same = False
            if not same:                         # .bak = dernière version valide AVANT celle-ci
                atomic_write_bytes(backup_path(path), previous, fsync=fsync)
        except (OSError, ValueError, TypeError, KeyError):
            pass                                 # version actuelle illisible : .bak inchangé
    atomic_write_bytes(path, raw, fsync=fsync)


@dataclass
class LoadResult:
    data: Any                                    # None → l'appelant prend ses valeurs par défaut
    source: str                                  # main | backup | missing | default | unreadable
    message: str = ""
    quarantined: Path | None = None

    @property
    def writable(self) -> bool:
        """Faux si le fichier existe mais n'a pas pu être LU : ne pas l'écraser."""
        return self.source != "unreadable"


def _quarantine(path: Path) -> Path | None:
    dest = path.with_name(f"{path.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}")
    n = 1
    while dest.exists():
        dest = path.with_name(f"{path.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}-{n}")
        n += 1
    try:
        shutil.copy2(path, dest)
        return dest
    except OSError:
        return None


def load_json(path: Path | str, *, validate: Callable[[Any], Any] | None = None,
              label: str | None = None) -> LoadResult:
    """Lit un JSON avec repli sur la sauvegarde. `validate(data)` renvoie les données acceptées
    ou lève (ValueError / TypeError / KeyError) si le contenu est inutilisable."""
    path = Path(path)
    label = label or path.name
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return LoadResult(None, "missing")
    except OSError as e:
        msg = f"{label} : lecture impossible ({e.__class__.__name__}) — réglages par défaut pour " \
              f"cette session, le fichier n'est pas modifié."
        notify(msg)
        return LoadResult(None, "unreadable", msg)
    try:
        return LoadResult(_parse(raw, validate), "main")
    except (ValueError, TypeError, KeyError, AttributeError) as e:
        error = e
    quarantined = _quarantine(path)
    kept = f" (copie gardée : {quarantined.name})" if quarantined else ""
    bak = backup_path(path)
    try:
        braw = bak.read_bytes()
        data = _parse(braw, validate)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        msg = (f"{label} endommagé ({error.__class__.__name__}) et pas de sauvegarde valide : "
               f"réglages par défaut{kept}.")
        notify(msg)
        return LoadResult(None, "default", msg, quarantined)
    try:
        atomic_write_bytes(path, braw)
    except OSError:
        pass                                     # restauré en mémoire ; l'écriture suivante réessaiera
    msg = f"{label} endommagé : dernière sauvegarde valide restaurée{kept}."
    notify(msg)
    return LoadResult(data, "backup", msg, quarantined)
