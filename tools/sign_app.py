"""Manifeste signé du CODE de l'appli (exécuté par la CI, à la publication d'une version).

  python tools/sign_app.py DOSSIER_DE_SORTIE

Écrit app-manifest.json (version, commit exact, et pour chaque fichier du paquet aion_target et
des fichiers annexes : taille + SHA-256) et app-manifest.sig (signature Ed25519, base64), avec la
MÊME clé que le moteur (secret GitHub ENGINE_SIGNING_KEY ; jamais dans le dépôt). Les
installations vérifient la signature avec la clé publique de engine_update.py, téléchargent
l'archive de CE commit (immuable) et refusent toute différence.

Sans clé : rien n'est signé (les installations continuent avec la vérification non signée tant
qu'elles n'ont jamais vu de manifeste signé).
"""
import base64
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def tracked_files() -> list[str]:
    from aion_target.updater import PACKAGE, TOP_FILES
    out = subprocess.run(["git", "ls-files", "-z", PACKAGE, *TOP_FILES], cwd=ROOT, check=True,
                         capture_output=True).stdout.decode()
    return sorted(f for f in out.split("\0") if f)


def build_manifest(commit: str) -> dict:
    import aion_target
    files = {}
    for rel in tracked_files():
        data = (ROOT / rel).read_bytes()
        files[rel] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    return {"version": aion_target.__version__, "commit": commit, "files": files}


def main():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    out = Path(sys.argv[1])
    commit = os.environ.get("GITHUB_SHA") or subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--", "aion_target"], cwd=ROOT,
                           capture_output=True, text=True).stdout.strip()
    if dirty:
        sys.exit(f"aion_target modifié par rapport au commit : manifeste refusé\n{dirty}")
    raw = json.dumps(build_manifest(commit), indent=1, sort_keys=True).encode()
    key = os.environ.get("ENGINE_SIGNING_KEY", "").strip()
    if not key:
        print("ENGINE_SIGNING_KEY absent : manifeste de l'appli NON publié.")
        return
    k = Ed25519PrivateKey.from_private_bytes(base64.b64decode(key))
    out.mkdir(parents=True, exist_ok=True)
    (out / "app-manifest.json").write_bytes(raw)
    (out / "app-manifest.sig").write_bytes(base64.b64encode(k.sign(raw)))
    print(f"Manifeste de l'appli signé : commit {commit[:12]}")


if __name__ == "__main__":
    main()
