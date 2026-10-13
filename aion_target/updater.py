"""Mise à jour automatique depuis GitHub, au lancement.

1. Lit la version publiée : aion_target/__init__.py de la branche main (≈ 100 octets, 3 s max).
   Hors ligne ou GitHub lent → lancement normal, sans attendre davantage.
2. Si elle est plus récente : télécharge l'archive du dépôt (≈ 1 Mo), vérifie qu'elle contient
   la bonne version et que tout le code se compile, installe les nouvelles dépendances s'il y en a
   (pip), puis remplace le dossier aion_target d'un bloc (l'ancien est gardé si quelque chose
   échoue). Les réglages (~/.config/aion-target, %APPDATA%\\aion-target) ne sont jamais touchés.
3. __main__ recharge alors les modules : l'appli démarre directement sur la nouvelle version.

Pas de mise à jour : dossier de développement (présence de .git), dossier non modifiable,
option « auto_update » désactivée dans config.json, variable AION_TARGET_NO_UPDATE, ou une
autre instance d'Aion Target déjà ouverte (on ne remplace pas le code sous ses pieds).
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

REPO = "MeriadecG/AION-2-TARGET"
BRANCH = "main"
VERSION_URL = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/aion_target/__init__.py"
ZIP_URL = f"https://codeload.github.com/{REPO}/zip/refs/heads/{BRANCH}"
PROJECT_DIR = Path(__file__).resolve().parent.parent
PACKAGE = "aion_target"
TOP_FILES = ("requirements.txt", "README.md", "install-linux.sh", "uninstall-linux.sh")
CHECK_TIMEOUT = 3.0
DOWNLOAD_TIMEOUT = 30.0
MAX_ZIP = 50 * 1024 * 1024


@dataclass
class UpdateResult:
    status: str                 # updated | up_to_date | skipped | offline | error | needs_setup
    message: str
    version: str = ""

    @property
    def updated(self) -> bool:
        return self.status == "updated"


# --- fonctions pures ---------------------------------------------------------------------

def parse_version(text: str) -> tuple[int, ...] | None:
    """Version lue dans un __init__.py (« __version__ = "2026.10.12.1" ») ou une chaîne seule."""
    m = re.search(r'__version__\s*=\s*["\']([\d.]+)["\']', text)
    raw = m.group(1) if m else text.strip()
    if not re.fullmatch(r"\d+(\.\d+)*", raw):
        return None
    return tuple(int(p) for p in raw.split("."))


def fmt_version(v: tuple[int, ...]) -> str:
    return ".".join(map(str, v))


def requirement_names(text: str) -> dict[str, str]:
    """requirements.txt → {nom normalisé: version minimale ou ""} (pytest ignoré)."""
    out = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        m = re.match(r"([A-Za-z0-9_.\-]+)(\[[^\]]*\])?\s*(>=\s*([\d.]+))?", line)
        if not m:
            continue
        name = re.sub(r"[-_.]+", "-", m.group(1)).lower()
        if name != "pytest":
            out[name] = m.group(4) or ""
    return out


def missing_requirements(text: str) -> list[str]:
    """Dépendances absentes (ou trop anciennes) dans l'environnement Python actuel."""
    from importlib import metadata
    missing = []
    for name, minimum in requirement_names(text).items():
        try:
            have = metadata.version(name)
        except metadata.PackageNotFoundError:
            missing.append(name + (f">={minimum}" if minimum else ""))
            continue
        if minimum:
            hv, mv = parse_version(have.split("+")[0].split("rc")[0]), parse_version(minimum)
            if hv and mv and hv < mv:
                missing.append(f"{name}>={minimum}")
    return missing


def skip_reason(project_dir: Path) -> str | None:
    if os.environ.get("AION_TARGET_NO_UPDATE"):
        return "désactivée (AION_TARGET_NO_UPDATE)"
    if (project_dir / ".git").exists():
        return "dossier de développement (git) : utilise git pull"
    if not (project_dir / PACKAGE / "__init__.py").exists():
        return "installation non reconnue"
    if not os.access(project_dir, os.W_OK) or not os.access(project_dir / PACKAGE, os.W_OK):
        return "dossier de l'appli non modifiable"
    return None


def _extract_package(data: bytes, dest: Path) -> Path:
    """Extrait l'archive GitHub (dossier racine « REPO-main/ ») et renvoie sa racine."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():                # pas de chemin hors du dossier (sécurité)
            target = (dest / info.filename).resolve()
            if not str(target).startswith(str(dest.resolve())):
                raise ValueError(f"chemin suspect dans l'archive : {info.filename}")
        z.extractall(dest)
    inits = list(dest.glob(f"*/{PACKAGE}/__init__.py"))
    if len(inits) != 1:
        raise ValueError("archive sans dossier aion_target")
    return inits[0].parent.parent


# Types de fichiers acceptés dans une mise à jour : code Python et ressources. Jamais
# d'exécutable natif (.exe, .dll, .so, .pyd), de script système (.bat, .ps1, .sh) ni de lien.
ALLOWED_SUFFIXES = {".py", ".json", ".png", ".ico", ".ttf", ".txt", ".wav", ".md"}


def check_files(package_dir: Path):
    for f in package_dir.rglob("*"):
        if f.is_symlink():
            raise ValueError(f"lien symbolique refusé : {f.name}")
        if f.is_file() and f.suffix.lower() not in ALLOWED_SUFFIXES:
            raise ValueError(f"type de fichier refusé dans la mise à jour : {f.name}")


def _compile_all(package_dir: Path):
    for f in package_dir.rglob("*.py"):           # erreur de syntaxe → SyntaxError
        compile(f.read_bytes(), str(f), "exec", dont_inherit=True)


def _sync_files(src: Path, cur: Path):
    """Remplacement fichier par fichier (Windows : un dossier ne peut pas être renommé si un
    antivirus ou l'Explorateur y tient un fichier ouvert ; un fichier .py, si). Chaque fichier est
    écrit à côté puis échangé atomiquement ; les fichiers disparus de la nouvelle version sont
    supprimés (sauf ceux encore verrouillés, sans conséquence)."""
    wanted = set()
    for f in src.rglob("*"):
        if "__pycache__" in f.parts or f.suffix == ".pyc":
            continue
        rel = f.relative_to(src)
        dst = cur / rel
        if f.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
            continue
        wanted.add(rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".upd")
        shutil.copy2(f, tmp)
        for attempt in range(5):                 # antivirus qui inspecte le fichier : on réessaie
            try:
                os.replace(tmp, dst)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.4)
    for f in sorted(cur.rglob("*"), reverse=True):
        rel = f.relative_to(cur)
        if "__pycache__" in rel.parts:
            continue
        if f.is_file() and rel not in wanted and f.suffix in ALLOWED_SUFFIXES:
            try:
                f.unlink()
            except OSError:
                pass


def install_tree(src_root: Path, project_dir: Path):
    """Remplace project_dir/aion_target par src_root/aion_target.

    1. Échange de dossiers (presque atomique, ancien dossier gardé jusqu'au bout) ;
    2. si Windows refuse de renommer le dossier (fichier tenu ouvert par l'antivirus, l'Explorateur…),
       remplacement fichier par fichier."""
    new = project_dir / f"{PACKAGE}.new"
    old = project_dir / f"{PACKAGE}.old"
    cur = project_dir / PACKAGE
    shutil.rmtree(new, ignore_errors=True)
    shutil.rmtree(old, ignore_errors=True)
    shutil.copytree(src_root / PACKAGE, new, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    swapped = False
    if not old.exists():
        try:
            os.replace(cur, old)
            swapped = True
        except OSError:
            swapped = False
    if swapped:
        try:
            os.replace(new, cur)
        except OSError:
            os.replace(old, cur)                 # retour arrière, puis fichier par fichier
            swapped = False
    if not swapped:
        _sync_files(new, cur)
    shutil.rmtree(new, ignore_errors=True)
    shutil.rmtree(old, ignore_errors=True)
    for name in TOP_FILES:                       # fichiers annexes déjà présents (install Linux)
        if (project_dir / name).exists() and (src_root / name).exists():
            shutil.copy2(src_root / name, project_dir / name)


# --- réseau ------------------------------------------------------------------------------

def _get(url: str, timeout: float, limit: int) -> bytes:
    import httpx
    with httpx.Client(timeout=timeout, follow_redirects=True,
                      headers={"User-Agent": "aion-target-updater"}) as c:
        with c.stream("GET", url) as r:
            r.raise_for_status()
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf += chunk
                if len(buf) > limit:
                    raise ValueError("réponse trop grosse")
            return bytes(buf)


def _pip_install(reqs: list[str]) -> tuple[bool, str]:
    cmd = [sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check", *reqs]
    if sys.platform == "win32":                  # pythonw.exe → python.exe, sans fenêtre
        py = Path(sys.executable).with_name("python.exe")
        cmd[0] = str(py if py.exists() else sys.executable)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:
        return False, str(e)
    return p.returncode == 0, (p.stderr or p.stdout).strip()[-300:]


# --- mise à jour -------------------------------------------------------------------------

def check_and_update(project_dir: Path = PROJECT_DIR, current: str | None = None,
                     fetch=_get, pip_install=_pip_install) -> UpdateResult:
    if current is None:                          # version du dossier à mettre à jour
        try:
            v = parse_version((project_dir / PACKAGE / "__init__.py").read_text(encoding="utf-8"))
            current = fmt_version(v) if v else "0"
        except OSError:
            current = "0"
    local = parse_version(current) or (0,)
    reason = skip_reason(project_dir)
    if reason:
        return UpdateResult("skipped", f"Mise à jour automatique : {reason}.", current)
    try:
        remote_text = fetch(VERSION_URL, CHECK_TIMEOUT, 64 * 1024).decode("utf-8", "replace")
    except Exception as e:
        code = getattr(getattr(e, "response", None), "status_code", None)
        why = "rien de publié sur GitHub" if code == 404 else f"GitHub injoignable ({type(e).__name__})"
        return UpdateResult("offline", f"Mise à jour : {why}, version {current} conservée.", current)
    remote = parse_version(remote_text)
    if remote is None:
        return UpdateResult("error", "Mise à jour : version publiée illisible.", current)
    if remote <= local:
        return UpdateResult("up_to_date", f"Version {current} : à jour.", current)
    rv = fmt_version(remote)
    t0 = time.monotonic()
    try:
        data = fetch(ZIP_URL, DOWNLOAD_TIMEOUT, MAX_ZIP)
        with tempfile.TemporaryDirectory(prefix="aion-target-update-") as tmp:
            root = _extract_package(data, Path(tmp))
            got = parse_version((root / PACKAGE / "__init__.py").read_text(encoding="utf-8"))
            if got != remote:
                return UpdateResult("error", f"Mise à jour {rv} : archive incohérente ({got}).", current)
            check_files(root / PACKAGE)
            _compile_all(root / PACKAGE)
            req = root / "requirements.txt"
            missing = missing_requirements(req.read_text(encoding="utf-8")) if req.exists() else []
            if any(not re.fullmatch(r"[a-z0-9][a-z0-9\-]*(>=[\d.]+)?", m) for m in missing):
                return UpdateResult("error", f"Mise à jour {rv} : dépendance au format refusé.", current)
            if missing:
                ok, detail = pip_install(missing)
                if not ok or missing_requirements(req.read_text(encoding="utf-8")):
                    return UpdateResult("needs_setup",
                                        f"Version {rv} disponible mais elle demande de nouvelles "
                                        f"bibliothèques ({', '.join(missing)}) : réinstalle "
                                        f"(Linux : ./install-linux.sh, Windows : nouveau Setup). {detail}",
                                        current)
            install_tree(root, project_dir)
    except Exception as e:
        return UpdateResult("error", f"Mise à jour {rv} annulée ({e}) : version {current} conservée.",
                            current)
    return UpdateResult("updated", f"Mise à jour installée : {current} → {rv} "
                                   f"({time.monotonic() - t0:.1f} s).", rv)


def _auto_update_enabled() -> bool:
    try:
        from .config import config_path
        data = json.loads(config_path().read_text(encoding="utf-8"))
        return bool(data.get("auto_update", True))
    except Exception:
        return True


def pid_alive(pid: int) -> bool:
    """Le processus `pid` existe-t-il encore ? (Windows : OpenProcess ; ailleurs : signal 0)."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return bool(ok) and code.value == 259            # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_previous_instance(timeout: float = 15.0):
    """Relance après « Mettre à jour » : on attend que l'ancienne instance soit fermée."""
    pid = int(os.environ.pop("AION_TARGET_WAIT_PID", "0") or 0)
    end = time.monotonic() + timeout
    while pid and pid_alive(pid) and time.monotonic() < end:
        time.sleep(0.2)


def _other_instance_running() -> bool:
    """Une autre instance OUVERTE tient le verrou du module Événements.

    Un verrou laissé par une instance fermée brutalement (plantage, arrêt du PC) ne bloque plus
    la mise à jour : le processus indiqué dans le verrou doit vraiment exister."""
    try:
        from PyQt6.QtCore import QLockFile
        from .config import config_path
        lock = QLockFile(str(config_path().parent / "events.lock"))
        lock.setStaleLockTime(0)
        if lock.tryLock(0):
            lock.unlock()
            return False
        ok, pid, _host, _app = lock.getLockInfo()
        if ok and pid and not pid_alive(int(pid)):
            lock.removeStaleLockFile()
            return False
        return True
    except Exception:
        return False


def startup_update() -> UpdateResult:
    """Appelé par __main__ ; le message est transmis au journal via l'environnement."""
    wait_previous_instance()
    if not _auto_update_enabled():
        res = UpdateResult("skipped", "Mise à jour automatique désactivée (réglages).")
    elif _other_instance_running():
        res = UpdateResult("skipped", "Mise à jour reportée : Aion Target est déjà ouvert.")
    else:
        res = check_and_update()
        try:                                     # moteur natif : manifeste signé uniquement
            from .engine_update import update_engine
            eng = update_engine()
            if eng.status in ("updated", "error"):
                res.message += " " + eng.message
        except Exception as e:
            res.message += f" Moteur : vérification impossible ({e})."
    os.environ["AION_TARGET_UPDATE_MSG"] = res.message
    os.environ["AION_TARGET_UPDATE_STATUS"] = res.status
    return res
