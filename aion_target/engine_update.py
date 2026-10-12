"""Mise à jour du MOTEUR natif (aion-engine) : seulement depuis un manifeste SIGNÉ.

Le code Python se met à jour depuis `main` (updater.py), mais un exécutable natif exige plus :
  1. `engine-manifest.json` (version, fichiers, SHA-256, taille) publié avec la dernière version ;
  2. `engine-manifest.sig` : signature Ed25519 du manifeste par la clé privée de la CI
     (secret GitHub ENGINE_SIGNING_KEY, jamais dans le dépôt) ; la clé PUBLIQUE est ci-dessous ;
  3. téléchargement → taille et SHA-256 vérifiés → `bin/aion-engine.new` → échange atomique,
     l'ancien gardé en `.old` (retour arrière). Jamais pendant que le moteur tourne : appelé au
     lancement, avant que le module DPS ne le démarre.
Linux : remplacer le fichier efface l'autorisation de capture ; l'onglet DPS propose de la
redonner (mot de passe), comme à la première installation.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import sys
from pathlib import Path

from . import updater

PUBLIC_KEY_B64 = "aCnsCbCCWJkTOkjGlZVvoVestabdduUAxNv9958wSa8="
RELEASE = f"https://github.com/{updater.REPO}/releases/latest/download"
MANIFEST_URL = f"{RELEASE}/engine-manifest.json"
SIGNATURE_URL = f"{RELEASE}/engine-manifest.sig"
MAX_BINARY = 60 * 1024 * 1024


def platform_key() -> str:
    machine = platform.machine().lower()
    arch = "x86_64" if machine in ("x86_64", "amd64") else machine
    return f"{'windows' if sys.platform == 'win32' else 'linux'}-{arch}"


def verify_signature(manifest: bytes, signature_b64: bytes, public_key_b64: str = PUBLIC_KEY_B64) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
        key.verify(base64.b64decode(signature_b64.strip()), manifest)
        return True
    except (InvalidSignature, ValueError):
        return False


def bin_dir(project_dir: Path = updater.PROJECT_DIR) -> Path:
    return project_dir / "bin"


def installed_version(project_dir: Path = updater.PROJECT_DIR) -> tuple[int, ...] | None:
    f = bin_dir(project_dir) / "aion-engine.version"
    try:
        return updater.parse_version(f.read_text(encoding="utf-8"))
    except OSError:
        return None


def update_engine(project_dir: Path = updater.PROJECT_DIR, fetch=updater._get, force: bool = False,
                  public_key_b64: str = PUBLIC_KEY_B64) -> updater.UpdateResult:
    """Installe le moteur publié s'il est plus récent (ou absent). Toujours signé et vérifié."""
    reason = updater.skip_reason(project_dir) if not force else None
    if reason:
        return updater.UpdateResult("skipped", f"Moteur : {reason}.")
    try:
        manifest_raw = fetch(MANIFEST_URL, updater.CHECK_TIMEOUT * 2, 256 * 1024)
        sig = fetch(SIGNATURE_URL, updater.CHECK_TIMEOUT * 2, 4096)
    except Exception as e:
        return updater.UpdateResult("offline", f"Moteur : manifeste injoignable ({type(e).__name__}).")
    if not verify_signature(manifest_raw, sig, public_key_b64):
        return updater.UpdateResult("error", "Moteur : SIGNATURE INVALIDE, mise à jour refusée.")
    try:
        manifest = json.loads(manifest_raw)
        remote = updater.parse_version(str(manifest["version"]))
        entry = manifest["files"][platform_key()]
        name, sha, size = entry["name"], entry["sha256"].lower(), int(entry["size"])
    except (KeyError, ValueError, TypeError) as e:
        return updater.UpdateResult("error", f"Moteur : manifeste incomplet ({e}).")
    local = installed_version(project_dir)
    exe = bin_dir(project_dir) / ("aion-engine.exe" if sys.platform == "win32" else "aion-engine")
    if not force and local and remote and remote <= local and exe.exists():
        return updater.UpdateResult("up_to_date", f"Moteur {updater.fmt_version(local)} : à jour.")
    if size <= 0 or size > MAX_BINARY:
        return updater.UpdateResult("error", "Moteur : taille annoncée invalide.")
    if "/" in name or "\\" in name or ".." in name:
        return updater.UpdateResult("error", "Moteur : nom de fichier refusé.")
    try:
        data = fetch(f"{RELEASE}/{name}", updater.DOWNLOAD_TIMEOUT * 4, MAX_BINARY)
    except Exception as e:
        return updater.UpdateResult("error", f"Moteur : téléchargement impossible ({e}).")
    if len(data) != size or hashlib.sha256(data).hexdigest() != sha:
        return updater.UpdateResult("error", "Moteur : fichier altéré (taille / SHA-256), refusé.")
    d = bin_dir(project_dir)
    d.mkdir(parents=True, exist_ok=True)
    new, old = exe.with_name(exe.name + ".new"), exe.with_name(exe.name + ".old")
    new.write_bytes(data)
    if sys.platform != "win32":
        os.chmod(new, 0o755)
    if exe.exists():
        os.replace(exe, old)
    try:
        os.replace(new, exe)
    except OSError:
        if old.exists():
            os.replace(old, exe)
        raise
    v = updater.fmt_version(remote) if remote else str(manifest.get("version"))
    (d / "aion-engine.version").write_text(v, encoding="utf-8")
    return updater.UpdateResult("updated", f"Moteur mis à jour : {v}.", v)


if __name__ == "__main__":                      # utilisé par install-linux.sh
    res = update_engine(force="--force" in sys.argv)
    print(res.message)
    sys.exit(0 if res.status in ("updated", "up_to_date") else 1)
