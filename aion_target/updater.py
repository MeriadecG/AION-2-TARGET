"""Mise à jour TRANSACTIONNELLE depuis GitHub.

Principe : la version en service n'est JAMAIS modifiée pendant que l'appli tourne, et une
installation incomplète n'est jamais considérée comme réussie.

  1. Préparation (au lancement ou bouton « Rechercher », en arrière-plan) — rien n'est touché :
     version publiée (aion_target/__init__.py de main) → archive téléchargée dans un dossier
     temporaire → extraction contrôlée (chemins, liens, tailles, noms Windows) → si un manifeste
     SIGNÉ (Ed25519, même clé que le moteur) est publié pour cette version : archive du commit
     exact et empreintes SHA-256 / tailles de chaque fichier vérifiées, refus au moindre écart →
     types de fichiers, compilation, compatibilité du moteur, dépendances → import de TOUS les
     modules dans un processus séparé (Python de l'appli) → copie dans `.update-staged/`,
     empreintes notées dans update-state.json (« prête à installer »).
  2. Bascule — UNIQUEMENT au démarrage, avant de charger l'appli, quand plus aucune instance ne
     tourne (bouton « Redémarrer » : la nouvelle instance attend la fermeture de l'ancienne) :
     copie préparée revérifiée, puis deux renommages de dossiers :
       aion_target → aion_target.prev   (la version qui marchait, GARDÉE)
       .update-staged/aion_target → aion_target
     Si Windows refuse le premier (fichier tenu ouvert) : rien n'a changé, « redémarrage
     nécessaire », nouvel essai au lancement suivant. Si le second échoue : retour immédiat.
     Jamais de remplacement fichier par fichier (installation mélangée impossible).
  3. Essai : la nouvelle version démarre « à l'essai ». Elle est confirmée après quelques
     secondes de fonctionnement (ou à une fermeture normale). Erreur au chargement, ou deux
     lancements sans confirmation (plantage) → retour automatique à aion_target.prev, et la
     version fautive n'est plus réinstallée automatiquement.
La version précédente reste dans aion_target.prev jusqu'à la mise à jour suivante réussie.

Pas de mise à jour automatique : dossier de développement (.git → git pull), dossier non
modifiable, option « auto_update » décochée, variable AION_TARGET_NO_UPDATE. Les réglages
(~/.config/aion-target, %APPDATA%\\aion-target) ne sont jamais touchés.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

REPO = "MeriadecG/AION-2-TARGET"
BRANCH = "main"
VERSION_URL = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/aion_target/__init__.py"
ZIP_URL = f"https://codeload.github.com/{REPO}/zip/refs/heads/{BRANCH}"
COMMIT_ZIP_URL = f"https://codeload.github.com/{REPO}/zip/{{commit}}"
APP_MANIFEST_URL = f"https://github.com/{REPO}/releases/download/v{{version}}/app-manifest.json"
APP_SIGNATURE_URL = f"https://github.com/{REPO}/releases/download/v{{version}}/app-manifest.sig"
PROJECT_DIR = Path(__file__).resolve().parent.parent
PACKAGE = "aion_target"
TOP_FILES = ("requirements.txt", "README.md", "install-linux.sh", "uninstall-linux.sh")
CHECK_TIMEOUT = 3.0
DOWNLOAD_TIMEOUT = 30.0
MAX_ZIP = 50 * 1024 * 1024
MAX_UNCOMPRESSED = 150 * 1024 * 1024        # archive « bombe » refusée
MAX_MEMBERS = 5000
MAX_RATIO = 200                             # taux de compression suspect par fichier
IMPORT_CHECK_TIMEOUT = 60.0

STAGE_DIR = ".update-staged"
PREV_DIR = f"{PACKAGE}.prev"
BAD_DIR = f"{PACKAGE}.bad"
STATE_FILE = "update-state.json"
LOCK_FILE = ".update.lock"
LOCK_STALE_S = 3600
MAX_TRIALS = 2                              # lancements sans confirmation avant retour arrière
CONFIRM_AFTER_MS = 15_000                   # durée de fonctionnement qui valide une version
# Une version déjà en manifeste signé rend la signature OBLIGATOIRE pour toutes les suivantes.
REQUIRE_SIGNED = False

# Types de fichiers acceptés dans une mise à jour : code Python et ressources. Jamais
# d'exécutable natif (.exe, .dll, .so, .pyd), de script système (.bat, .ps1, .sh) ni de lien.
ALLOWED_SUFFIXES = {".py", ".json", ".png", ".ico", ".ttf", ".txt", ".wav", ".md"}
_WIN_RESERVED = {"con", "prn", "aux", "nul", "conin$", "conout$",
                 *(f"com{i}" for i in range(10)), *(f"lpt{i}" for i in range(10)),
                 "com¹", "com²", "com³", "lpt¹", "lpt²", "lpt³"}
_WIN_BAD_CHARS = set('<>:"|?*\\') | {chr(c) for c in range(32)}


@dataclass
class UpdateResult:
    # up_to_date | staged (prête à installer) | pending (redémarrage nécessaire) | updated
    # (installée, à l'essai) | confirmed | rolled_back | skipped | offline | error | needs_setup
    # | incompatible | busy (autre mise à jour en cours) | trial (nouvelle version à l'essai)
    status: str
    message: str
    version: str = ""
    details: dict = field(default_factory=dict)

    @property
    def updated(self) -> bool:
        """Le code sur disque a changé : __main__ doit recharger les modules."""
        return self.status in ("updated", "rolled_back")


STATUS_LABELS = {
    "up_to_date": "Aucune mise à jour", "staged": "Prête à installer", "pending": "Redémarrage nécessaire",
    "updated": "Mise à jour installée (vérification au démarrage)", "confirmed": "Mise à jour réussie",
    "rolled_back": "Échec — version précédente restaurée", "skipped": "Désactivée", "offline": "Hors ligne",
    "error": "Échec", "needs_setup": "Réinstallation nécessaire", "incompatible": "Version incompatible",
    "busy": "Mise à jour déjà en cours", "trial": "Nouvelle version en vérification", "downloading": "Téléchargement", "verifying": "Vérification",
}


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


def installed_version(project_dir: Path) -> str:
    try:
        v = parse_version((project_dir / PACKAGE / "__init__.py").read_text(encoding="utf-8"))
        return fmt_version(v) if v else "0"
    except OSError:
        return "0"


# --- archive : extraction contrôlée -------------------------------------------------------

def safe_member_path(name: str) -> PurePosixPath:
    """Chemin d'un membre d'archive, refusé s'il est dangereux ou inutilisable sous Windows."""
    if not name or "\x00" in name or "\\" in name:
        raise ValueError(f"nom refusé dans l'archive : {name!r}")
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        raise ValueError(f"chemin absolu refusé : {name}")
    p = PurePosixPath(name)
    for part in p.parts:
        if part in ("..", "."):
            raise ValueError(f"chemin hors du dossier refusé : {name}")
        if any(c in _WIN_BAD_CHARS for c in part) or part.endswith((" ", ".")):
            raise ValueError(f"nom incompatible Windows : {name}")
        if part.split(".")[0].casefold() in _WIN_RESERVED:
            raise ValueError(f"nom réservé Windows : {name}")
    return p


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)


def _extract_package(data: bytes, dest: Path) -> Path:
    """Extrait de l'archive GitHub (dossier racine « REPO-xxx/ ») le paquet et les fichiers
    annexes, dans `dest`, et renvoie la racine. Toute anomalie → ValueError, rien n'est gardé."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ValueError(f"archive illisible ({e})") from None
    with z:
        infos = z.infolist()
        if not infos or len(infos) > MAX_MEMBERS:
            raise ValueError("archive vide ou démesurée")
        roots, seen, total, wanted = set(), set(), 0, []
        for info in infos:
            p = safe_member_path(info.filename)
            if _is_symlink(info):
                raise ValueError(f"lien symbolique refusé : {info.filename}")
            folded = str(p).casefold()               # A.py et a.py : collision sous Windows
            if folded in seen:
                raise ValueError(f"deux fichiers au même nom (Windows) : {info.filename}")
            seen.add(folded)
            roots.add(p.parts[0])
            total += info.file_size
            if total > MAX_UNCOMPRESSED:
                raise ValueError("archive trop volumineuse une fois décompressée")
            if info.compress_size and info.file_size / info.compress_size > MAX_RATIO \
                    and info.file_size > 1024 * 1024:
                raise ValueError(f"taux de compression suspect : {info.filename}")
            rel = p.parts[1:]
            if info.is_dir() or not rel:
                continue
            if rel[0] == PACKAGE or (len(rel) == 1 and rel[0] in TOP_FILES):
                wanted.append((info, p))
        if len(roots) != 1:
            raise ValueError("archive sans dossier racine unique")
        base = dest.resolve()
        for info, p in wanted:
            target = (base / Path(*p.parts)).resolve()
            if not target.is_relative_to(base):      # vérification par chemins, pas par texte
                raise ValueError(f"chemin hors du dossier refusé : {info.filename}")
            if target.suffix.lower() not in ALLOWED_SUFFIXES and p.parts[1] == PACKAGE:
                raise ValueError(f"type de fichier refusé dans la mise à jour : {p.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            size = 0
            try:
                with z.open(info) as src, open(target, "xb") as out:   # CRC vérifié à la lecture
                    while chunk := src.read(1 << 16):
                        size += len(chunk)
                        if size > info.file_size:
                            raise ValueError(f"taille incohérente : {info.filename}")
                        out.write(chunk)
            except (zipfile.BadZipFile, EOFError, zlib.error) as e:
                raise ValueError(f"archive endommagée ({info.filename} : {e})") from None
            except FileExistsError:
                raise ValueError(f"fichier en double : {info.filename}") from None
            if size != info.file_size:
                raise ValueError(f"fichier tronqué : {info.filename}")
    inits = list(dest.glob(f"*/{PACKAGE}/__init__.py"))
    if len(inits) != 1:
        raise ValueError("archive sans dossier aion_target")
    return inits[0].parent.parent


def check_files(package_dir: Path):
    for f in package_dir.rglob("*"):
        if f.is_symlink():
            raise ValueError(f"lien symbolique refusé : {f.name}")
        if f.is_file() and f.suffix.lower() not in ALLOWED_SUFFIXES:
            raise ValueError(f"type de fichier refusé dans la mise à jour : {f.name}")


def _compile_all(package_dir: Path):
    for f in package_dir.rglob("*.py"):           # erreur de syntaxe → SyntaxError
        compile(f.read_bytes(), str(f), "exec", dont_inherit=True)


def tree_digest(root: Path, names: tuple[str, ...] = (PACKAGE,)) -> dict[str, list]:
    """{chemin relatif (« aion_target/x.py ») : [taille, sha256]} sans les caches Python."""
    out = {}
    for name in names:
        top = root / name
        files = [top] if top.is_file() else sorted(top.rglob("*")) if top.is_dir() else []
        for f in files:
            if not f.is_file() or "__pycache__" in f.parts or f.suffix == ".pyc":
                continue
            data = f.read_bytes()
            out[f.relative_to(root).as_posix()] = [len(data), hashlib.sha256(data).hexdigest()]
    return out


def engine_protocol(package_dir: Path) -> int | None:
    try:
        m = re.search(r"^PROTOCOL\s*=\s*(\d+)", (package_dir / "engine_client.py").read_text(encoding="utf-8"), re.M)
    except OSError:
        return None
    return int(m.group(1)) if m else None


# --- manifeste signé ------------------------------------------------------------------------

def verify_app_manifest(raw: bytes, sig: bytes, version: tuple[int, ...],
                        public_key_b64: str | None = None) -> dict:
    """Manifeste de l'appli signé par la CI (clé du moteur). Lève ValueError si invalide."""
    from .engine_update import PUBLIC_KEY_B64, verify_signature
    if not verify_signature(raw, sig, public_key_b64 or PUBLIC_KEY_B64):
        raise ValueError("SIGNATURE INVALIDE du manifeste")
    try:
        m = json.loads(raw)
        files = m["files"]
        commit = str(m["commit"])
        mv = parse_version(str(m["version"]))
    except (KeyError, ValueError, TypeError) as e:
        raise ValueError(f"manifeste incomplet ({e})") from None
    if mv != version:
        raise ValueError(f"manifeste d'une autre version ({m.get('version')})")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("commit du manifeste illisible")
    if not isinstance(files, dict) or not files:
        raise ValueError("manifeste sans fichiers")
    for rel, entry in files.items():
        safe_member_path(rel)
        if not (isinstance(entry, dict) and isinstance(entry.get("size"), int)
                and re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256", "")))):
            raise ValueError(f"entrée de manifeste invalide : {rel}")
    return m


def check_against_manifest(root: Path, manifest: dict):
    """Chaque fichier extrait doit être dans le manifeste, avec la même taille et le même SHA-256,
    et chaque fichier du manifeste doit être présent."""
    got = tree_digest(root, (PACKAGE, *TOP_FILES))
    want = {k: [v["size"], v["sha256"]] for k, v in manifest["files"].items()}
    missing = sorted(set(want) - set(got))
    extra = sorted(set(got) - set(want))
    if missing:
        raise ValueError(f"fichier manquant : {missing[0]}")
    if extra:
        raise ValueError(f"fichier non signé : {extra[0]}")
    for rel, entry in want.items():
        if got[rel] != entry:
            raise ValueError(f"empreinte différente : {rel}")


# --- état, verrou ----------------------------------------------------------------------------

def read_state(project_dir: Path) -> dict:
    try:
        data = json.loads((project_dir / STATE_FILE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(project_dir: Path, state: dict):
    from .storage import atomic_write_json
    state = dict(state, at=time.time())
    atomic_write_json(project_dir / STATE_FILE, state, backup=False)


class UpdateBusy(Exception):
    pass


class UpdateLock:
    """Un seul processus de mise à jour à la fois (création exclusive d'un fichier qui contient
    le PID). Un verrou laissé par un processus disparu, ou très ancien, est repris."""

    def __init__(self, project_dir: Path):
        self.path = project_dir / LOCK_FILE
        self.held = False

    def _stale(self) -> bool:
        try:
            pid = int(self.path.read_text(encoding="utf-8").split()[0])
        except (OSError, ValueError, IndexError):
            pid = 0
        try:
            age = time.time() - self.path.stat().st_mtime
        except OSError:
            return True
        if pid == 0:                             # fichier en cours d'écriture, ou illisible
            return age > 30
        return age > LOCK_STALE_S or (pid != os.getpid() and not pid_alive(pid))

    def acquire(self):
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                if self._stale():
                    try:
                        self.path.unlink()
                    except OSError:
                        pass
                    continue
                raise UpdateBusy("une autre mise à jour est en cours") from None
            with os.fdopen(fd, "w") as f:
                f.write(f"{os.getpid()} {time.time():.0f}\n")
            self.held = True
            return self
        raise UpdateBusy("verrou de mise à jour impossible à reprendre")

    def release(self):
        if self.held:
            self.held = False
            try:
                self.path.unlink()
            except OSError:
                pass

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()


def _rmtree(path: Path):
    if path.exists() or path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)


def _rename(src: Path, dst: Path, attempts: int = 6, delay: float = 0.25):
    """os.replace avec quelques essais : sous Windows, un antivirus ou l'indexation peut tenir
    un fichier quelques instants. Lève l'erreur finale si le renommage reste impossible."""
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(delay * (i + 1))


# --- réseau ------------------------------------------------------------------------------

def _get(url: str, timeout: float, limit: int) -> bytes:
    import httpx
    with httpx.Client(timeout=timeout, follow_redirects=True,
                      headers={"User-Agent": "aion-target-updater"}) as c:
        with c.stream("GET", url) as r:
            r.raise_for_status()
            expected = r.headers.get("content-length")
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf += chunk
                if len(buf) > limit:
                    raise ValueError("réponse trop grosse")
            if expected and expected.isdigit() and int(expected) != len(buf) \
                    and not r.headers.get("content-encoding"):
                raise ValueError("téléchargement interrompu")
            return bytes(buf)


def _status_code(e: Exception) -> int | None:
    return getattr(getattr(e, "response", None), "status_code", None)


def _python_console() -> str:
    """Python avec console (Windows : pythonw.exe → python.exe), pour pip et le contrôle d'import."""
    if sys.platform == "win32":
        py = Path(sys.executable).with_name("python.exe")
        return str(py if py.exists() else sys.executable)
    return sys.executable


def _pip_install(reqs: list[str]) -> tuple[bool, str]:
    cmd = [_python_console(), "-m", "pip", "install", "-q", "--disable-pip-version-check", *reqs]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:
        return False, str(e)
    return p.returncode == 0, (p.stderr or p.stdout).strip()[-300:]


_IMPORT_CHECK = r"""
import importlib, pkgutil, sys
root = sys.argv[1]
sys.path.insert(0, root)
import aion_target
assert aion_target.__file__.startswith(root), aion_target.__file__
for m in pkgutil.iter_modules(aion_target.__path__):
    importlib.import_module("aion_target." + m.name)
print("OK", aion_target.__version__)
"""


def _import_check(root: Path) -> tuple[bool, str]:
    """Charge TOUS les modules de la nouvelle version dans un processus séparé, avec le Python et
    les bibliothèques de l'appli : dépendance manquante, erreur au chargement, Python trop ancien
    → refus AVANT toute bascule."""
    env = dict(os.environ, AION_TARGET_NO_UPDATE="1", PYTHONDONTWRITEBYTECODE="1")
    env.pop("PYTHONPATH", None)
    try:
        p = subprocess.run([_python_console(), "-c", _IMPORT_CHECK, str(root)], cwd=str(root),
                           capture_output=True, text=True, timeout=IMPORT_CHECK_TIMEOUT, env=env,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:
        return False, f"contrôle impossible ({e})"
    if p.returncode != 0 or not p.stdout.startswith("OK"):
        err = (p.stderr or p.stdout).strip().splitlines()
        return False, err[-1][:300] if err else f"code {p.returncode}"
    return True, ""


# --- préparation (aucune modification de la version en service) ------------------------------

def check_and_update(project_dir: Path = PROJECT_DIR, current: str | None = None,
                     fetch=_get, pip_install=_pip_install, import_check=_import_check,
                     progress=None, manual: bool = False,
                     public_key_b64: str | None = None) -> UpdateResult:
    """Cherche une version plus récente et la PRÉPARE (statut « staged »). N'installe rien :
    la bascule a lieu au prochain démarrage (apply_pending)."""
    progress = progress or (lambda status, text: None)
    current = current or installed_version(project_dir)
    local = parse_version(current) or (0,)
    reason = skip_reason(project_dir)
    if reason:
        return UpdateResult("skipped", f"Mise à jour automatique : {reason}.", current)
    try:
        remote_text = fetch(VERSION_URL, CHECK_TIMEOUT, 64 * 1024).decode("utf-8", "replace")
    except Exception as e:
        why = "rien de publié sur GitHub" if _status_code(e) == 404 else f"GitHub injoignable ({type(e).__name__})"
        return UpdateResult("offline", f"Mise à jour : {why}, version {current} conservée.", current)
    remote = parse_version(remote_text)
    if remote is None:
        return UpdateResult("error", "Mise à jour : version publiée illisible.", current)
    if remote <= local:
        return UpdateResult("up_to_date", f"Version {current} : à jour.", current)
    rv = fmt_version(remote)
    state = read_state(project_dir)
    if not manual and state.get("bad_version") == rv:
        return UpdateResult("up_to_date", f"Version {current} gardée : la {rv} a échoué au démarrage "
                                          f"et a été retirée (bouton « Rechercher » pour réessayer).", current)
    if state.get("status") == "ready" and state.get("version") == rv and (project_dir / STAGE_DIR / PACKAGE).is_dir():
        return UpdateResult("staged", f"Version {rv} prête à installer : redémarre Aion Target.", rv)
    try:
        with UpdateLock(project_dir):
            return _prepare(project_dir, current, remote, fetch, pip_install, import_check, progress,
                            state, public_key_b64)
    except UpdateBusy as e:
        return UpdateResult("busy", f"Mise à jour : {e}.", current)


def _prepare(project_dir, current, remote, fetch, pip_install, import_check, progress, state,
             public_key_b64) -> UpdateResult:
    rv = fmt_version(remote)
    t0 = time.monotonic()
    signed_required = REQUIRE_SIGNED or bool(state.get("signed_seen"))
    # 1. manifeste signé (s'il est publié pour cette version)
    manifest = None
    try:
        raw = fetch(APP_MANIFEST_URL.format(version=rv), CHECK_TIMEOUT * 2, 1024 * 1024)
        sig = fetch(APP_SIGNATURE_URL.format(version=rv), CHECK_TIMEOUT * 2, 4096)
    except Exception as e:
        if signed_required:
            why = "pas encore signée" if _status_code(e) == 404 else f"signature injoignable ({type(e).__name__})"
            return UpdateResult("offline", f"Version {rv} publiée mais {why} : installée dès que sa "
                                           f"signature sera disponible.", current)
    else:
        try:
            manifest = verify_app_manifest(raw, sig, remote, public_key_b64)
        except ValueError as e:
            return UpdateResult("error", f"Mise à jour {rv} REFUSÉE : {e}. Version {current} conservée.", current)
    # 2. archive (celle du commit signé si manifeste) dans un dossier temporaire
    progress("downloading", f"Mise à jour {rv} : téléchargement…")
    url = COMMIT_ZIP_URL.format(commit=manifest["commit"]) if manifest else ZIP_URL
    try:
        data = fetch(url, DOWNLOAD_TIMEOUT, MAX_ZIP)
    except Exception as e:
        return UpdateResult("offline", f"Mise à jour {rv} : téléchargement interrompu ({type(e).__name__}), "
                                       f"version {current} conservée.", current)
    progress("verifying", f"Mise à jour {rv} : vérification…")
    stage = project_dir / STAGE_DIR
    tmp_stage = project_dir / f"{STAGE_DIR}.tmp"
    try:
        with tempfile.TemporaryDirectory(prefix="aion-target-update-") as tmp:
            root = _extract_package(data, Path(tmp))
            got = parse_version((root / PACKAGE / "__init__.py").read_text(encoding="utf-8"))
            if got != remote:
                return UpdateResult("error", f"Mise à jour {rv} : archive incohérente ({got}).", current)
            if manifest:
                check_against_manifest(root, manifest)
            check_files(root / PACKAGE)
            _compile_all(root / PACKAGE)
            # compatibilité du moteur natif installé (protocole des messages)
            engine = project_dir / "bin" / ("aion-engine.exe" if sys.platform == "win32" else "aion-engine")
            cur_proto, new_proto = engine_protocol(project_dir / PACKAGE), engine_protocol(root / PACKAGE)
            if engine.exists() and cur_proto and new_proto and cur_proto != new_proto:
                return UpdateResult("incompatible", f"Version {rv} : elle demande un nouveau moteur DPS "
                                                    f"(protocole {new_proto}) — installe le nouveau Setup. "
                                                    f"Version {current} conservée.", current)
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
            ok, detail = import_check(root)
            if not ok:
                return UpdateResult("error", f"Mise à jour {rv} refusée : la nouvelle version ne se charge "
                                             f"pas ({detail}). Version {current} conservée.", current)
            # 3. copie préparée, à côté de la version en service (même disque : renommage possible)
            _rmtree(tmp_stage)
            tmp_stage.mkdir()
            shutil.copytree(root / PACKAGE, tmp_stage / PACKAGE,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            for name in TOP_FILES:
                if (root / name).is_file():
                    shutil.copy2(root / name, tmp_stage / name)
            digest = tree_digest(tmp_stage, (PACKAGE, *TOP_FILES))
            if digest != tree_digest(root, (PACKAGE, *TOP_FILES)):
                raise ValueError("copie préparée différente de l'archive vérifiée")
        _rmtree(stage)
        _rename(tmp_stage, stage)
        new_state = {k: v for k, v in state.items() if k in ("signed_seen", "bad_version", "confirmed")}
        new_state.update(status="ready", version=rv, from_version=current, files=digest,
                         signed=bool(manifest), signed_seen=bool(manifest) or bool(state.get("signed_seen")))
        write_state(project_dir, new_state)
    except Exception as e:
        _rmtree(tmp_stage)
        return UpdateResult("error", f"Mise à jour {rv} annulée ({e}) : version {current} conservée.", current)
    how = "signée et vérifiée" if manifest else "vérifiée"
    return UpdateResult("staged", f"Version {rv} {how}, prête à installer "
                                  f"({time.monotonic() - t0:.1f} s) : redémarre Aion Target.", rv,
                        {"signed": bool(manifest)})


# --- bascule, essai, confirmation, retour arrière -------------------------------------------

def apply_pending(project_dir: Path = PROJECT_DIR) -> UpdateResult:
    """Au démarrage, AVANT de charger l'appli : installe la version préparée par deux renommages
    de dossiers. Jamais d'état intermédiaire durable (voir l'en-tête)."""
    state = read_state(project_dir)
    stage, cur = project_dir / STAGE_DIR, project_dir / PACKAGE
    prev = project_dir / PREV_DIR
    current = installed_version(project_dir)
    if state.get("status") != "ready" or not (stage / PACKAGE).is_dir():
        return UpdateResult("up_to_date", "", current)
    rv = str(state.get("version", "?"))
    try:
        lock = UpdateLock(project_dir).acquire()
    except UpdateBusy as e:
        return UpdateResult("pending", f"Version {rv} prête : {e}, installation au prochain lancement.", current)
    try:
        if tree_digest(stage, (PACKAGE, *TOP_FILES)) != state.get("files"):
            _rmtree(stage)
            write_state(project_dir, {**_keep(state), "status": "failed", "version": rv,
                                      "message": "copie préparée altérée"})
            return UpdateResult("error", f"Mise à jour {rv} annulée : copie préparée altérée "
                                         f"(elle sera retéléchargée). Version {current} conservée.", current)
        write_state(project_dir, {**state, "status": "switching"})
        _rmtree(prev)
        try:
            _rename(cur, prev)
        except OSError as e:
            write_state(project_dir, {**state, "status": "ready"})
            return UpdateResult("pending", f"Version {rv} prête mais des fichiers de l'appli sont encore "
                                           f"utilisés ({e.__class__.__name__}) : redémarrage nécessaire, "
                                           f"version {current} conservée.", current)
        try:
            _rename(stage / PACKAGE, cur)
        except OSError as e:
            _rename(prev, cur, attempts=20)          # retour immédiat (dossier qu'on vient de libérer)
            write_state(project_dir, {**state, "status": "ready"})
            return UpdateResult("pending", f"Version {rv} : installation impossible pour l'instant "
                                           f"({e.__class__.__name__}), version {current} conservée.", current)
        for name in TOP_FILES:                       # fichiers annexes (install Linux), non critiques
            src = stage / name
            if src.is_file() and (project_dir / name).exists():
                try:
                    _rename(src, project_dir / name)
                except OSError:
                    pass
        _rmtree(stage)
        _rmtree(project_dir / BAD_DIR)
        write_state(project_dir, {**_keep(state), "status": "trial", "version": rv, "previous": current,
                                  "attempts": 1, "files": state.get("files")})
    finally:
        lock.release()
    return UpdateResult("updated", f"Mise à jour installée : {current} → {rv} (vérification en cours).", rv)


def _keep(state: dict) -> dict:
    return {k: state[k] for k in ("signed_seen", "bad_version", "confirmed") if k in state}


def recover(project_dir: Path = PROJECT_DIR) -> UpdateResult | None:
    """Au démarrage : termine ou annule proprement une opération interrompue (coupure, plantage)."""
    state = read_state(project_dir)
    cur, prev, stage = project_dir / PACKAGE, project_dir / PREV_DIR, project_dir / STAGE_DIR
    _rmtree(project_dir / f"{STAGE_DIR}.tmp")       # préparation interrompue : rien d'installé
    status = state.get("status")
    if status == "switching":
        if not (cur / "__init__.py").exists() and (prev / "__init__.py").exists():
            if cur.exists():
                _rmtree(cur)
            _rename(prev, cur, attempts=20)
        if (stage / PACKAGE).is_dir():
            write_state(project_dir, {**state, "status": "ready"})
        elif installed_version(project_dir) == state.get("version"):
            write_state(project_dir, {**_keep(state), "status": "trial", "version": state.get("version"),
                                      "previous": state.get("from_version"), "attempts": 0})
        else:
            write_state(project_dir, {**_keep(state), "status": "failed", "message": "bascule interrompue"})
        state = read_state(project_dir)
        status = state.get("status")
    if status == "trial":
        attempts = int(state.get("attempts", 0) or 0)
        if attempts >= MAX_TRIALS:
            return rollback(project_dir, f"{attempts} démarrages sans confirmation")
        write_state(project_dir, {**state, "attempts": attempts + 1})
    return None


def in_trial(project_dir: Path = PROJECT_DIR) -> bool:
    return read_state(project_dir).get("status") == "trial"


def confirm_update(project_dir: Path = PROJECT_DIR) -> UpdateResult | None:
    """La nouvelle version fonctionne : elle devient la version de référence. aion_target.prev
    (la précédente) est gardée jusqu'à la mise à jour suivante."""
    state = read_state(project_dir)
    if state.get("status") != "trial":
        return None
    v = state.get("version", "")
    write_state(project_dir, {**_keep(state), "status": "ok", "version": v, "confirmed": v,
                              "previous": state.get("previous")})
    return UpdateResult("confirmed", f"Mise à jour {v} réussie.", v)


def rollback(project_dir: Path = PROJECT_DIR, reason: str = "") -> UpdateResult:
    """Retour à aion_target.prev ; la version fautive est mise de côté (aion_target.bad)."""
    state = read_state(project_dir)
    cur, prev, bad = project_dir / PACKAGE, project_dir / PREV_DIR, project_dir / BAD_DIR
    v = str(state.get("version", installed_version(project_dir)))
    if not (prev / "__init__.py").exists():
        write_state(project_dir, {**_keep(state), "status": "ok", "version": v})
        return UpdateResult("error", f"Version {v} : retour arrière impossible (pas de version précédente).", v)
    try:
        lock = UpdateLock(project_dir).acquire()
    except UpdateBusy:
        lock = None
    try:
        _rmtree(bad)
        if cur.exists():
            _rename(cur, bad)
        _rename(prev, cur, attempts=20)
    except OSError as e:
        if not cur.exists() and bad.exists():
            _rename(bad, cur, attempts=20)
        return UpdateResult("error", f"Retour arrière de la version {v} impossible pour l'instant ({e}).", v)
    finally:
        if lock:
            lock.release()
    back = installed_version(project_dir)
    write_state(project_dir, {**_keep(state), "status": "rolled_back", "version": back, "bad_version": v,
                              "message": reason})
    return UpdateResult("rolled_back", f"La version {v} n'a pas démarré correctement ({reason}) : "
                                       f"version {back} restaurée.", back)


def describe_state(project_dir: Path = PROJECT_DIR) -> str:
    st = read_state(project_dir)
    s = st.get("status")
    if s == "ready":
        return f"Version {st.get('version')} prête à installer : redémarre Aion Target."
    if s == "trial":
        return f"Version {st.get('version')} installée, vérification au démarrage en cours."
    if s == "rolled_back":
        return (f"Version {st.get('bad_version')} retirée après un échec ; version "
                f"{installed_version(project_dir)} en service.")
    return ""


# --- démarrage ---------------------------------------------------------------------------

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


def wait_previous_instance(timeout: float = 20.0):
    """Relance après « Redémarrer » : on attend que l'ancienne instance soit fermée."""
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
        path = config_path().parent / "events.lock"
        if not path.exists():                    # aucun verrou (ou dossier de réglages absent)
            return False
        lock = QLockFile(str(path))
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


def restart_command(pid: int | None = None) -> tuple[list[str], dict, dict]:
    """Commande de relance de l'appli : (argv, env, options Popen)."""
    env = dict(os.environ, AION_TARGET_WAIT_PID=str(pid or os.getpid()))
    for k in ("AION_TARGET_UPDATE_MSG", "AION_TARGET_UPDATE_STATUS"):
        env.pop(k, None)
    exe = os.environ.get("AION_TARGET_EXE")
    if exe and os.path.exists(exe):                       # Windows : « Aion Target.exe »
        return [exe], env, {"close_fds": True, "cwd": str(Path(exe).parent),
                            "creationflags": getattr(subprocess, "DETACHED_PROCESS", 0)}
    return [sys.executable, "-m", PACKAGE], env, {"cwd": str(PROJECT_DIR), "start_new_session": True}


def restart_app(popen=subprocess.Popen) -> tuple[bool, str]:
    """Lance la nouvelle instance (qui attend la fermeture de celle-ci). Faux → ne pas quitter."""
    argv, env, opts = restart_command()
    try:
        popen(argv, env=env, **opts)
    except Exception as e:
        return False, f"Relance impossible ({e}) : ferme et rouvre Aion Target, la mise à jour s'installera."
    return True, ""


def startup_update(project_dir: Path = PROJECT_DIR, check=None) -> UpdateResult:
    """Appelé par __main__ AVANT de charger l'appli ; le message passe par l'environnement."""
    wait_previous_instance()
    messages = []
    res = UpdateResult("skipped", "", installed_version(project_dir))
    reason = skip_reason(project_dir)
    if reason:
        res = UpdateResult("skipped", f"Mise à jour automatique : {reason}.", res.version)
    elif _other_instance_running():
        st = read_state(project_dir)
        res = UpdateResult("pending" if st.get("status") == "ready" else "skipped",
                           "Mise à jour reportée : Aion Target est déjà ouvert.", res.version)
    else:
        back = recover(project_dir)
        if back:
            messages.append(back.message)
            res = back
        if not back and in_trial(project_dir):
            res = UpdateResult("trial", describe_state(project_dir), res.version)
        elif not back and _auto_update_enabled():
            res = (check or check_and_update)(project_dir)
        applied = apply_pending(project_dir) if not back and not in_trial(project_dir) else None
        if applied and applied.status != "up_to_date":
            if res.status not in ("skipped", "up_to_date", "staged") and res.message:
                messages.append(res.message)
            res = applied
        try:                                     # moteur natif : manifeste signé uniquement
            from .engine_update import update_engine
            eng = update_engine(project_dir)
            if eng.status in ("updated", "error"):
                messages.append(eng.message)
        except Exception as e:
            messages.append(f"Moteur : vérification impossible ({e}).")
    res.message = " ".join(dict.fromkeys(m for m in [res.message, *messages] if m))
    os.environ["AION_TARGET_UPDATE_MSG"] = res.message
    os.environ["AION_TARGET_UPDATE_STATUS"] = res.status
    return res
