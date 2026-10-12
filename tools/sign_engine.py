"""Manifeste signé du moteur DPS (exécuté par la CI).

  python tools/sign_engine.py DOSSIER
DOSSIER contient aion-engine-linux-x86_64 et aion-engine-windows-x86_64.exe. Écrit
engine-manifest.json (version, nom, SHA-256, taille par plateforme) et engine-manifest.sig
(signature Ed25519, base64) avec la clé privée lue dans la variable ENGINE_SIGNING_KEY
(secret GitHub ; base64 des 32 octets de la clé). Sans clé : rien n'est signé et les
installations ne mettront pas le moteur à jour automatiquement (elles gardent le leur).

  python tools/sign_engine.py --new-key     génère une paire de clés (à faire UNE fois)
"""
import base64
import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = {"linux-x86_64": "aion-engine-linux-x86_64", "windows-x86_64": "aion-engine-windows-x86_64.exe"}


def engine_version() -> str:
    m = re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "engine" / "Cargo.toml").read_text(), re.M)
    return m.group(1)


def main():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    if sys.argv[1:] == ["--new-key"]:
        k = Ed25519PrivateKey.generate()
        seed = k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                               serialization.NoEncryption())
        pub = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        print("Secret GitHub ENGINE_SIGNING_KEY :", base64.b64encode(seed).decode())
        print("Clé publique (aion_target/engine_update.py, PUBLIC_KEY_B64) :", base64.b64encode(pub).decode())
        return
    folder = Path(sys.argv[1])
    manifest = {"version": engine_version(), "files": {}}
    for plat, name in FILES.items():
        data = (folder / name).read_bytes()
        manifest["files"][plat] = {"name": name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    raw = json.dumps(manifest, indent=1, sort_keys=True).encode()
    key = os.environ.get("ENGINE_SIGNING_KEY", "").strip()
    if not key:
        print("ENGINE_SIGNING_KEY absent : manifeste NON publié (pas de mise à jour auto du moteur).")
        return
    k = Ed25519PrivateKey.from_private_bytes(base64.b64decode(key))
    (folder / "engine-manifest.json").write_bytes(raw)
    (folder / "engine-manifest.sig").write_bytes(base64.b64encode(k.sign(raw)))
    print(f"Manifeste signé : moteur {manifest['version']}")


if __name__ == "__main__":
    main()
