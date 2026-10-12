"""Mise à jour du moteur natif : signature Ed25519, intégrité, retour arrière."""
import base64
import hashlib
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aion_target import engine_update as eu


@pytest.fixture
def keys():
    k = Ed25519PrivateKey.generate()
    pub = base64.b64encode(k.public_key().public_bytes(serialization.Encoding.Raw,
                                                       serialization.PublicFormat.Raw)).decode()
    return k, pub


def release(k, version="0.2.0", binary=b"\x7fELF-moteur", tamper=False, wrong_key=False):
    entry = {"name": "aion-engine-" + eu.platform_key(), "sha256": hashlib.sha256(binary).hexdigest(),
             "size": len(binary)}
    manifest = json.dumps({"version": version, "files": {eu.platform_key(): entry}}).encode()
    signer = Ed25519PrivateKey.generate() if wrong_key else k
    sig = base64.b64encode(signer.sign(manifest))
    served = binary + (b"!" if tamper else b"")

    def fetch(url, timeout, limit):
        if url == eu.MANIFEST_URL:
            return manifest
        if url == eu.SIGNATURE_URL:
            return sig
        return served
    return fetch


def proj(tmp_path):
    (tmp_path / "aion_target").mkdir()
    (tmp_path / "aion_target" / "__init__.py").write_text('__version__ = "1"')
    return tmp_path


def exe(p):
    import sys
    return p / "bin" / ("aion-engine.exe" if sys.platform == "win32" else "aion-engine")


def test_signed_engine_is_installed(tmp_path, keys):
    k, pub = keys
    p = proj(tmp_path)
    res = eu.update_engine(p, fetch=release(k), public_key_b64=pub)
    assert res.status == "updated", res.message
    assert exe(p).read_bytes() == b"\x7fELF-moteur"
    assert eu.installed_version(p) == (0, 2, 0)
    assert eu.update_engine(p, fetch=release(k), public_key_b64=pub).status == "up_to_date"


def test_bad_signature_refused(tmp_path, keys):
    k, pub = keys
    p = proj(tmp_path)
    res = eu.update_engine(p, fetch=release(k, wrong_key=True), public_key_b64=pub)
    assert res.status == "error" and "SIGNATURE" in res.message
    assert not exe(p).exists()


def test_tampered_binary_refused(tmp_path, keys):
    k, pub = keys
    p = proj(tmp_path)
    res = eu.update_engine(p, fetch=release(k, tamper=True), public_key_b64=pub)
    assert res.status == "error" and "altéré" in res.message
    assert not exe(p).exists()


def test_upgrade_keeps_previous_as_rollback(tmp_path, keys):
    k, pub = keys
    p = proj(tmp_path)
    eu.update_engine(p, fetch=release(k, "0.2.0", b"v2"), public_key_b64=pub)
    eu.update_engine(p, fetch=release(k, "0.3.0", b"v3"), public_key_b64=pub)
    assert exe(p).read_bytes() == b"v3"
    assert exe(p).with_name(exe(p).name + ".old").read_bytes() == b"v2"


def test_embedded_public_key_is_valid():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    Ed25519PublicKey.from_public_bytes(base64.b64decode(eu.PUBLIC_KEY_B64))


def test_dev_checkout_never_downloads_engine(tmp_path, keys):
    k, pub = keys
    p = proj(tmp_path)
    (p / ".git").mkdir()
    assert eu.update_engine(p, fetch=release(k), public_key_b64=pub).status == "skipped"
