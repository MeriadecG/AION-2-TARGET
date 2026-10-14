"""Mise à jour transactionnelle : préparation, vérifications, bascule, essai, retour arrière.

Critère principal : après CHAQUE échec simulé, l'appli retrouve une version complète et cohérente
(jamais de mélange ancien / nouveau)."""
import base64
import hashlib
import io
import json
import os
import re
import stat
import sys
import threading
import zipfile
from pathlib import Path

import pytest

from aion_target import updater

OLD, NEW = "2026.10.12.1", "2026.10.13"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.delenv("AION_TARGET_NO_UPDATE", raising=False)
    monkeypatch.delenv("AION_TARGET_WAIT_PID", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "cfg"))
    monkeypatch.setattr(updater, "REQUIRE_SIGNED", False)


def make_install(tmp: Path, version=OLD, mod="X = 'ancien'\n") -> Path:
    proj = tmp / "app"
    pkg = proj / "aion_target"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(f'__version__ = "{version}"\n')
    (pkg / "mod.py").write_text(mod)
    (proj / "requirements.txt").write_text("PyQt6>=6.6\n")
    return proj


def package_files(version: str, mod="X = 'nouveau'\n", req="PyQt6>=6.6\npytest\n", extra=None) -> dict:
    files = {"aion_target/__init__.py": f'__version__ = "{version}"\n', "aion_target/mod.py": mod,
             "aion_target/assets/x.json": "{}", "requirements.txt": req}
    files.update(extra or {})
    return files


def github_zip(version: str, mod="X = 'nouveau'\n", req="PyQt6>=6.6\npytest\n", extra=None,
               root="AION-2-TARGET-main/", compression=zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as z:
        z.writestr(root, "")
        for k, v in package_files(version, mod, req, extra).items():
            z.writestr(root + k, v)
    return buf.getvalue()


def fake_fetch(version: str, zipdata: bytes | None = None, fail=False, manifest=None, zip_error=None):
    calls = []

    def fetch(url, timeout, limit):
        calls.append(url)
        if fail:
            raise OSError("hors ligne")
        if url == updater.VERSION_URL:
            return f'"""doc"""\n__version__ = "{version}"\n'.encode()
        if url.endswith("app-manifest.json") or url.endswith("app-manifest.sig"):
            if manifest is None:
                raise _http_error(404)
            return manifest[0] if url.endswith(".json") else manifest[1]
        if zip_error:
            raise zip_error
        return zipdata if zipdata is not None else github_zip(version)
    fetch.calls = calls
    return fetch


def _http_error(code):
    e = OSError(f"HTTP {code}")
    e.response = type("R", (), {"status_code": code})()
    return e


def no_pip(reqs):
    return True, ""


def stage(proj, version=NEW, **kw):
    kw.setdefault("pip_install", no_pip)
    kw.setdefault("fetch", fake_fetch(version))
    return updater.check_and_update(proj, OLD, **kw)


def mod_text(proj):
    return (proj / "aion_target" / "mod.py").read_text()


def assert_coherent(proj, version):
    """Installation complète et cohérente : tous les fichiers d'UNE seule version."""
    init = (proj / "aion_target" / "__init__.py").read_text()
    assert f'"{version}"' in init
    expect = "X = 'ancien'\n" if version == OLD else "X = 'nouveau'\n"
    assert mod_text(proj) == expect
    assert (proj / "aion_target" / "assets" / "x.json").exists() == (version != OLD)


# --- fonctions pures --------------------------------------------------------------------------

def test_parse_version():
    assert updater.parse_version('__version__ = "2026.10.12.1"') == (2026, 10, 12, 1)
    assert updater.parse_version("2026.10.13") > updater.parse_version("2026.10.12.9")
    assert updater.parse_version("<html>404</html>") is None


def test_requirements_parsing():
    r = updater.requirement_names("PyQt6>=6.6\nhttpx[http2]>=0.27\nnumpy\n# c\npytest\ntzdata  # tz\n")
    assert r == {"pyqt6": "6.6", "httpx": "0.27", "numpy": "", "tzdata": ""}


# --- mise à jour normale ----------------------------------------------------------------------

def test_normal_update_is_staged_then_switched_then_confirmed(tmp_path):
    proj = make_install(tmp_path)
    res = stage(proj)
    assert res.status == "staged" and res.version == NEW, res.message
    assert_coherent(proj, OLD)                               # rien touché pendant que l'appli tourne
    assert (proj / ".update-staged" / "aion_target" / "mod.py").read_text() == "X = 'nouveau'\n"
    assert updater.read_state(proj)["status"] == "ready"

    res = updater.apply_pending(proj)
    assert res.status == "updated" and res.updated, res.message
    assert_coherent(proj, NEW)
    assert "pytest" in (proj / "requirements.txt").read_text()          # fichier annexe
    assert (proj / "aion_target.prev" / "mod.py").read_text() == "X = 'ancien'\n"   # gardée
    assert not (proj / ".update-staged").exists()
    st = updater.read_state(proj)
    assert st["status"] == "trial" and st["version"] == NEW and st["previous"] == OLD

    assert updater.confirm_update(proj).status == "confirmed"
    assert updater.read_state(proj)["status"] == "ok"
    assert (proj / "aion_target.prev").exists()              # gardée jusqu'à la mise à jour suivante
    assert updater.confirm_update(proj) is None


def test_same_version_downloads_nothing(tmp_path):
    proj = make_install(tmp_path)
    f = fake_fetch(OLD)
    res = updater.check_and_update(proj, OLD, fetch=f)
    assert res.status == "up_to_date" and f.calls == [updater.VERSION_URL]


def test_already_staged_version_is_not_downloaded_again(tmp_path):
    proj = make_install(tmp_path)
    stage(proj)
    f = fake_fetch(NEW)
    assert stage(proj, fetch=f).status == "staged" and f.calls == [updater.VERSION_URL]


def test_progress_states_are_reported(tmp_path):
    proj = make_install(tmp_path)
    seen = []
    stage(proj, progress=lambda st, txt: seen.append(st))
    assert seen == ["downloading", "verifying"]


# --- réseau ---------------------------------------------------------------------------------

def test_no_network_keeps_current(tmp_path):
    proj = make_install(tmp_path)
    res = stage(proj, fetch=fake_fetch("x", fail=True))
    assert res.status == "offline"
    assert_coherent(proj, OLD)
    assert not (proj / ".update-staged").exists()


def test_interrupted_download_keeps_current(tmp_path):
    proj = make_install(tmp_path)
    res = stage(proj, fetch=fake_fetch(NEW, zip_error=TimeoutError("coupure")))
    assert res.status == "offline" and "interrompu" in res.message
    truncated = github_zip(NEW)[:400]
    res = stage(proj, fetch=fake_fetch(NEW, truncated))
    assert res.status == "error"
    assert_coherent(proj, OLD)
    assert not (proj / ".update-staged").exists() and not (proj / ".update-staged.tmp").exists()
    assert updater.apply_pending(proj).status == "up_to_date"     # rien à installer


# --- archives malveillantes ou abîmées ----------------------------------------------------------

def _corrupt_crc(data: bytes, needle: bytes) -> bytes:
    i = data.index(needle)
    return data[:i] + bytes([data[i] ^ 0xFF]) + data[i + 1:]


@pytest.mark.parametrize("label,data", [
    ("pas un zip", b"PK\x03\x04 ceci n'est pas une archive"),
    ("octets aleatoires", os.urandom(2000)),
    ("crc", _corrupt_crc(github_zip(NEW, mod="X = 'nouveau-unique'\n", compression=zipfile.ZIP_STORED),
                         b"nouveau-unique")),
])
def test_corrupt_archive_is_refused(tmp_path, label, data):
    proj = make_install(tmp_path)
    res = stage(proj, fetch=fake_fetch(NEW, data))
    assert res.status == "error", label
    assert_coherent(proj, OLD)
    assert not (proj / ".update-staged").exists()


@pytest.mark.parametrize("name", [
    "../../evil.txt", "AION-2-TARGET-main/../../evil.txt", "/etc/evil.txt", "C:/evil.txt",
    "AION-2-TARGET-main/aion_target/..\\..\\evil.py", "AION-2-TARGET-main/aion_target/CON.py",
    "AION-2-TARGET-main/aion_target/aux.json", "AION-2-TARGET-main/aion_target/bad:name.py",
    "AION-2-TARGET-main/aion_target/trailing.py ", "AION-2-TARGET-main/aion_target/nul\x00.py",
])
def test_dangerous_paths_are_refused(tmp_path, name):
    proj = make_install(tmp_path)
    buf = io.BytesIO(github_zip(NEW))
    with zipfile.ZipFile(buf, "a") as z:
        info = zipfile.ZipInfo("placeholder")
        info.filename = name                       # contourne la normalisation de zipfile
        z.writestr(info, "x")
    res = stage(proj, fetch=fake_fetch(NEW, buf.getvalue()))
    assert res.status == "error", res.message
    assert not (tmp_path / "evil.txt").exists() and not Path("/etc/evil.txt").exists()
    assert_coherent(proj, OLD)


def test_safe_member_path_uses_path_logic_not_text_prefix():
    with pytest.raises(ValueError):
        updater.safe_member_path("root/a/../../x")
    assert str(updater.safe_member_path("root/aion_target/a.py")) == "root/aion_target/a.py"


def test_symlink_in_archive_is_refused(tmp_path):
    proj = make_install(tmp_path)
    buf = io.BytesIO(github_zip(NEW))
    with zipfile.ZipFile(buf, "a") as z:
        info = zipfile.ZipInfo("AION-2-TARGET-main/aion_target/link.py")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(info, "/etc/passwd")
    res = stage(proj, fetch=fake_fetch(NEW, buf.getvalue()))
    assert res.status == "error" and "lien" in res.message


def test_windows_case_collision_is_refused(tmp_path):
    proj = make_install(tmp_path)
    evil = github_zip(NEW, extra={"aion_target/Mod.py": "X = 1\n"})
    res = stage(proj, fetch=fake_fetch(NEW, evil))
    assert res.status == "error" and "même nom" in res.message


def test_excessive_uncompressed_size_is_refused(tmp_path, monkeypatch):
    proj = make_install(tmp_path)
    monkeypatch.setattr(updater, "MAX_UNCOMPRESSED", 1000)
    res = stage(proj, fetch=fake_fetch(NEW, github_zip(NEW, extra={"aion_target/big.txt": "0" * 5000})))
    assert res.status == "error" and "volumineuse" in res.message


def test_several_roots_are_refused(tmp_path):
    proj = make_install(tmp_path)
    buf = io.BytesIO(github_zip(NEW))
    with zipfile.ZipFile(buf, "a") as z:
        z.writestr("autre-racine/aion_target/__init__.py", "x")
    assert stage(proj, fetch=fake_fetch(NEW, buf.getvalue())).status == "error"


def test_update_refuses_native_binaries(tmp_path):
    proj = make_install(tmp_path)
    evil = github_zip(NEW, extra={"aion_target/payload.exe": "MZ"})
    res = stage(proj, fetch=fake_fetch(NEW, evil))
    assert res.status == "error" and "refusé" in res.message
    assert not (proj / "aion_target" / "payload.exe").exists()


def test_inconsistent_archive_refused(tmp_path):
    proj = make_install(tmp_path)
    res = stage(proj, fetch=fake_fetch(NEW, github_zip("2026.10.11")))
    assert res.status == "error" and "incohérente" in res.message


def test_broken_code_is_refused(tmp_path):
    proj = make_install(tmp_path)
    res = stage(proj, fetch=fake_fetch(NEW, github_zip(NEW, mod="def oops(:\n")))
    assert res.status == "error"
    assert_coherent(proj, OLD)


def test_version_that_cannot_be_loaded_is_refused_before_switch(tmp_path):
    """Contrôle réel dans un processus séparé : module qui plante au chargement → refus."""
    proj = make_install(tmp_path)
    bad = github_zip(NEW, mod="import module_qui_n_existe_pas\n")
    res = stage(proj, fetch=fake_fetch(NEW, bad))
    assert res.status == "error" and "ne se charge pas" in res.message, res.message
    assert "module_qui_n_existe_pas" in res.message
    assert not (proj / ".update-staged").exists()


# --- signature ------------------------------------------------------------------------------

def _keypair():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    k = Ed25519PrivateKey.generate()                  # clé de TEST, créée à la volée
    pub = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return k, base64.b64encode(pub).decode()


def _manifest(key, version=NEW, files=None, commit="a" * 40):
    files = files if files is not None else package_files(version)
    m = {"version": version, "commit": commit,
         "files": {k: {"size": len(v.encode()), "sha256": hashlib.sha256(v.encode()).hexdigest()}
                   for k, v in files.items()}}
    raw = json.dumps(m).encode()
    return raw, base64.b64encode(key.sign(raw))


def test_signed_manifest_pins_commit_and_is_verified(tmp_path):
    proj = make_install(tmp_path)
    key, pub = _keypair()
    f = fake_fetch(NEW, manifest=_manifest(key))
    res = stage(proj, fetch=f, public_key_b64=pub)
    assert res.status == "staged" and res.details["signed"], res.message
    assert updater.COMMIT_ZIP_URL.format(commit="a" * 40) in f.calls      # archive du commit exact
    assert updater.ZIP_URL not in f.calls
    assert updater.read_state(proj)["signed_seen"] is True


def test_invalid_signature_is_refused(tmp_path):
    proj = make_install(tmp_path)
    key, _ = _keypair()
    _, other_pub = _keypair()
    res = stage(proj, fetch=fake_fetch(NEW, manifest=_manifest(key)), public_key_b64=other_pub)
    assert res.status == "error" and "SIGNATURE INVALIDE" in res.message
    assert not (proj / ".update-staged").exists()
    raw, sig = _manifest(key)
    tampered = raw.replace(b'"commit": "aaaa', b'"commit": "bbbb')
    res = stage(proj, fetch=fake_fetch(NEW, manifest=(tampered, sig)), public_key_b64=_keypair()[1])
    assert res.status == "error"
    assert_coherent(proj, OLD)


@pytest.mark.parametrize("change,needle", [
    ("missing", "fichier manquant"), ("extra", "non signé"), ("different", "empreinte différente")])
def test_archive_must_match_signed_manifest(tmp_path, change, needle):
    proj = make_install(tmp_path)
    key, pub = _keypair()
    files = package_files(NEW)
    if change == "missing":
        files["aion_target/disparu.py"] = "x = 1\n"
    elif change == "extra":
        del files["aion_target/assets/x.json"]
    else:
        files["aion_target/mod.py"] = "X = 'signé'\n"
    res = stage(proj, fetch=fake_fetch(NEW, manifest=_manifest(key, files=files)), public_key_b64=pub)
    assert res.status == "error" and needle in res.message, res.message
    assert_coherent(proj, OLD)


def test_once_signed_unsigned_versions_are_no_longer_accepted(tmp_path):
    proj = make_install(tmp_path)
    updater.write_state(proj, {"status": "ok", "signed_seen": True})
    res = stage(proj)                                   # pas de manifeste publié (404)
    assert res.status == "offline" and "pas encore signée" in res.message
    assert not (proj / ".update-staged").exists()


def test_signing_tool_manifest_matches_updater_checks(tmp_path, monkeypatch):
    """tools/sign_app.py produit un manifeste que l'updater accepte (clé de test)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import sign_app
    root = Path(__file__).resolve().parent.parent
    monkeypatch.setattr(sign_app, "tracked_files", lambda: sorted(updater.tree_digest(root, ("aion_target",
                                                                                            "requirements.txt"))))
    m = sign_app.build_manifest("c" * 40)
    key, pub = _keypair()
    raw = json.dumps(m, indent=1, sort_keys=True).encode()
    import aion_target
    got = updater.verify_app_manifest(raw, base64.b64encode(key.sign(raw)),
                                      updater.parse_version(aion_target.__version__), pub)
    assert got["commit"] == "c" * 40 and "aion_target/updater.py" in got["files"]


# --- bascule : échecs pendant le déploiement ------------------------------------------------------

def test_access_denied_on_folder_rename_changes_nothing(tmp_path, monkeypatch):
    """Windows : dossier encore utilisé → rien ne bouge, « redémarrage nécessaire », nouvel essai."""
    proj = make_install(tmp_path)
    stage(proj)
    real = os.replace

    def refuse_dirs(a, b):
        if os.path.isdir(a):
            raise PermissionError("[WinError 5] Accès refusé")
        return real(a, b)
    monkeypatch.setattr(updater.os, "replace", refuse_dirs)
    monkeypatch.setattr(updater.time, "sleep", lambda s: None)
    res = updater.apply_pending(proj)
    assert res.status == "pending" and "redémarrage nécessaire" in res.message
    assert_coherent(proj, OLD)
    assert updater.read_state(proj)["status"] == "ready"
    monkeypatch.setattr(updater.os, "replace", real)
    assert updater.apply_pending(proj).status == "updated"
    assert_coherent(proj, NEW)


def test_error_mid_deploy_restores_previous(tmp_path, monkeypatch):
    """Le 1er renommage réussit, le 2e échoue : retour immédiat à la version en service."""
    proj = make_install(tmp_path)
    stage(proj)
    real = os.replace

    def fail_second(a, b):
        if str(a).endswith(os.path.join(".update-staged", "aion_target")):
            raise PermissionError("fichier verrouillé")
        return real(a, b)
    monkeypatch.setattr(updater.os, "replace", fail_second)
    monkeypatch.setattr(updater.time, "sleep", lambda s: None)
    res = updater.apply_pending(proj)
    assert res.status == "pending"
    assert_coherent(proj, OLD)
    assert not (proj / "aion_target.prev").exists() or (proj / "aion_target.prev" / "mod.py").exists()
    monkeypatch.setattr(updater.os, "replace", real)
    assert updater.apply_pending(proj).status == "updated"
    assert_coherent(proj, NEW)


def test_locked_file_while_preparing_leaves_nothing_behind(tmp_path, monkeypatch):
    proj = make_install(tmp_path)

    def locked(*a, **k):
        raise PermissionError("fichier verrouillé par l'antivirus")
    monkeypatch.setattr(updater.shutil, "copytree", locked)
    res = stage(proj)
    assert res.status == "error"
    assert not (proj / ".update-staged").exists() and not (proj / ".update-staged.tmp").exists()
    assert_coherent(proj, OLD)


def test_altered_staged_copy_is_never_installed(tmp_path):
    proj = make_install(tmp_path)
    stage(proj)
    (proj / ".update-staged" / "aion_target" / "mod.py").write_text("X = 'modifié'\n")
    res = updater.apply_pending(proj)
    assert res.status == "error" and "altérée" in res.message
    assert_coherent(proj, OLD)
    assert not (proj / ".update-staged").exists()


def test_missing_file_in_staged_copy_is_never_installed(tmp_path):
    proj = make_install(tmp_path)
    stage(proj)
    (proj / ".update-staged" / "aion_target" / "assets" / "x.json").unlink()
    assert updater.apply_pending(proj).status == "error"
    assert_coherent(proj, OLD)


def test_interrupted_switch_is_repaired_at_next_start(tmp_path):
    """Coupure entre les deux renommages : le dossier en service manque → version précédente remise."""
    proj = make_install(tmp_path)
    stage(proj)
    st = updater.read_state(proj)
    updater.write_state(proj, {**st, "status": "switching"})
    os.replace(proj / "aion_target", proj / "aion_target.prev")
    assert updater.recover(proj) is None
    assert_coherent(proj, OLD)
    assert updater.read_state(proj)["status"] == "ready"
    assert updater.apply_pending(proj).status == "updated"
    assert_coherent(proj, NEW)


def test_interrupted_preparation_is_cleaned(tmp_path):
    proj = make_install(tmp_path)
    (proj / ".update-staged.tmp" / "aion_target").mkdir(parents=True)
    updater.recover(proj)
    assert not (proj / ".update-staged.tmp").exists()


# --- essai et retour arrière -----------------------------------------------------------------

def test_unconfirmed_version_is_rolled_back_after_two_starts(tmp_path):
    proj = make_install(tmp_path)
    stage(proj)
    updater.apply_pending(proj)                        # 1er démarrage (attempts = 1), plantage
    assert updater.recover(proj) is None               # 2e démarrage, plantage
    res = updater.recover(proj)                        # 3e : retour arrière
    assert res.status == "rolled_back" and res.updated, res.message
    assert_coherent(proj, OLD)
    st = updater.read_state(proj)
    assert st["status"] == "rolled_back" and st["bad_version"] == NEW
    assert (proj / "aion_target.bad" / "mod.py").read_text() == "X = 'nouveau'\n"
    # la version fautive n'est plus réinstallée automatiquement…
    res = stage(proj)
    assert res.status == "up_to_date" and "a échoué" in res.message
    # … sauf demande explicite
    assert stage(proj, manual=True).status == "staged"


def test_confirmed_version_is_never_rolled_back(tmp_path):
    proj = make_install(tmp_path)
    stage(proj)
    updater.apply_pending(proj)
    updater.confirm_update(proj)
    for _ in range(4):
        assert updater.recover(proj) is None
    assert_coherent(proj, NEW)


def test_rollback_without_previous_version_keeps_current(tmp_path):
    proj = make_install(tmp_path)
    updater.write_state(proj, {"status": "trial", "version": OLD, "attempts": 5})
    res = updater.recover(proj)
    assert res.status == "error"
    assert_coherent(proj, OLD)


def test_main_rolls_back_in_process_when_new_version_does_not_load(monkeypatch):
    import aion_target.__main__ as entry
    monkeypatch.setattr(sys, "argv", ["aion_target"])
    monkeypatch.setattr(updater, "startup_update", lambda: updater.UpdateResult("updated", "ok", NEW))
    monkeypatch.setattr(updater, "in_trial", lambda: True)
    calls = []
    monkeypatch.setattr(updater, "rollback",
                        lambda reason="": calls.append(reason) or updater.UpdateResult("rolled_back", "retour", OLD))

    class FakeCli:
        @staticmethod
        def main():
            return 0
    attempts = []

    def fake_import(name):
        attempts.append(name)
        if len(attempts) == 1:
            raise ImportError("nouvelle version cassée")
        return FakeCli
    saved = {k: v for k, v in sys.modules.items() if k.startswith("aion_target")}
    monkeypatch.setattr(entry.importlib, "import_module", fake_import)
    try:
        assert entry.main() == 0
    finally:
        sys.modules.update(saved)
    assert len(attempts) == 2 and calls and "cassée" in calls[0]
    assert os.environ.get("AION_TARGET_UPDATE_STATUS") == "rolled_back"


def test_main_reloads_modules_after_update(monkeypatch):
    """Après une bascule, __main__ purge les modules aion_target puis lance cli.main."""
    import aion_target.__main__ as entry
    monkeypatch.setattr(sys, "argv", ["aion_target"])
    monkeypatch.setattr(updater, "startup_update", lambda: updater.UpdateResult("updated", "ok", "9"))
    seen = {}

    class FakeCli:
        @staticmethod
        def main():
            seen["config_reloaded"] = "aion_target.config" not in before
            return 0
    before = {}

    def fake_import(name):
        before.update(dict.fromkeys(sys.modules))
        return FakeCli
    import aion_target.config  # noqa: F401  (chargé avant la mise à jour)
    saved = {k: v for k, v in sys.modules.items() if k.startswith("aion_target")}
    monkeypatch.setattr(entry.importlib, "import_module", fake_import)
    try:
        assert entry.main() == 0
        assert seen["config_reloaded"]
    finally:
        sys.modules.update(saved)


def test_windows_launcher_restores_previous_version(tmp_path):
    """Filet du lanceur Windows (nouveaux Setup) : paquet illisible → version précédente remise."""
    src = (Path(__file__).resolve().parent.parent / "tools" / "build_windows.py").read_text()
    boot = re.search(r"BOOTSTRAP = r'''(.*?)'''", src, re.S).group(1)
    func = boot[boot.index("def _restore_previous"):boot.index("\ntry:\n    try:")]
    ns = {"os": os, "sys": sys}
    exec(func, ns)
    proj = make_install(tmp_path)
    os.replace(proj / "aion_target", proj / "aion_target.prev")
    (proj / "aion_target").mkdir()                              # paquet cassé
    (proj / "update-state.json").write_text(json.dumps({"status": "trial", "version": NEW}))
    saved = {k: v for k, v in sys.modules.items() if k.startswith("aion_target")}
    try:
        assert ns["_restore_previous"](str(proj)) is True
    finally:
        sys.modules.update(saved)
    assert_coherent(proj, OLD)
    st = json.loads((proj / "update-state.json").read_text())
    assert st["status"] == "rolled_back" and st["bad_version"] == NEW


# --- démarrage complet ------------------------------------------------------------------------

@pytest.fixture
def startup_env(monkeypatch):
    from aion_target import engine_update
    monkeypatch.setattr(engine_update, "update_engine",
                        lambda *a, **k: updater.UpdateResult("up_to_date", "Moteur : à jour."))
    monkeypatch.setattr(updater, "_other_instance_running", lambda: False)


def test_startup_installs_and_starts_in_trial(tmp_path, startup_env):
    proj = make_install(tmp_path)
    res = updater.startup_update(proj, check=lambda p: stage(p))
    assert res.status == "updated" and res.updated, res.message
    assert_coherent(proj, NEW)
    assert os.environ["AION_TARGET_UPDATE_STATUS"] == "updated"
    res = updater.startup_update(proj, check=lambda p: pytest.fail("pas de réseau à l'essai"))
    assert res.status == "trial" and not res.updated


def test_startup_offline_starts_current_version(tmp_path, startup_env):
    proj = make_install(tmp_path)
    res = updater.startup_update(proj, check=lambda p: stage(p, fetch=fake_fetch("x", fail=True)))
    assert res.status == "offline" and not res.updated
    assert_coherent(proj, OLD)


def test_startup_with_other_instance_open_defers(tmp_path, startup_env, monkeypatch):
    proj = make_install(tmp_path)
    stage(proj)
    monkeypatch.setattr(updater, "_other_instance_running", lambda: True)
    res = updater.startup_update(proj, check=lambda p: pytest.fail("jamais"))
    assert res.status == "pending" and not res.updated
    assert_coherent(proj, OLD)


def test_startup_dev_checkout_is_never_updated(tmp_path, startup_env):
    proj = make_install(tmp_path)
    stage(proj)
    (proj / ".git").mkdir()
    res = updater.startup_update(proj, check=lambda p: pytest.fail("jamais"))
    assert res.status == "skipped"
    assert_coherent(proj, OLD)


def test_manual_staging_is_installed_even_if_auto_update_is_off(tmp_path, startup_env, monkeypatch):
    proj = make_install(tmp_path)
    stage(proj, manual=True)
    monkeypatch.setattr(updater, "_auto_update_enabled", lambda: False)
    res = updater.startup_update(proj, check=lambda p: pytest.fail("pas de vérification auto"))
    assert res.status == "updated"
    assert_coherent(proj, NEW)


# --- processus concurrents --------------------------------------------------------------------

def test_two_concurrent_updates_never_interleave(tmp_path):
    proj = make_install(tmp_path)
    gate = threading.Event()
    results = []

    def slow_fetch_factory():
        inner = fake_fetch(NEW)

        def fetch(url, timeout, limit):
            if url != updater.VERSION_URL and not url.endswith(("json", "sig")):
                gate.wait(2)
            return inner(url, timeout, limit)
        return fetch
    threads = [threading.Thread(target=lambda: results.append(stage(proj, fetch=slow_fetch_factory())))
               for _ in range(2)]
    for t in threads:
        t.start()
    import time
    time.sleep(0.3)
    gate.set()
    for t in threads:
        t.join(10)
    assert sorted(r.status for r in results) == ["busy", "staged"], [r.message for r in results]
    assert updater.apply_pending(proj).status == "updated"
    assert_coherent(proj, NEW)
    assert not (proj / updater.LOCK_FILE).exists()


def test_lock_held_by_live_process_blocks_and_dead_one_is_taken_over(tmp_path):
    proj = make_install(tmp_path)
    (proj / updater.LOCK_FILE).write_text(f"{os.getppid()} 0\n")      # processus vivant
    assert stage(proj).status == "busy"
    assert updater.apply_pending(proj).status == "up_to_date"
    (proj / updater.LOCK_FILE).write_text("999999 0\n")               # processus disparu
    assert stage(proj).status == "staged"


# --- relance ----------------------------------------------------------------------------------

def test_restart_impossible_keeps_app_open():
    def popen(*a, **k):
        raise OSError("exécutable introuvable")
    ok, msg = updater.restart_app(popen=popen)
    assert ok is False and "Relance impossible" in msg


def test_restart_command_waits_for_this_instance(monkeypatch):
    monkeypatch.delenv("AION_TARGET_EXE", raising=False)
    argv, env, opts = updater.restart_command(pid=4242)
    assert argv[-2:] == ["-m", "aion_target"] and env["AION_TARGET_WAIT_PID"] == "4242"
    assert "AION_TARGET_UPDATE_MSG" not in env


# --- compatibilité ----------------------------------------------------------------------------

def test_incompatible_engine_protocol_is_refused(tmp_path):
    proj = make_install(tmp_path)
    (proj / "aion_target" / "engine_client.py").write_text("PROTOCOL = 1\n")
    (proj / "bin").mkdir()
    exe = "aion-engine.exe" if sys.platform == "win32" else "aion-engine"
    (proj / "bin" / exe).write_bytes(b"\x7fELF")
    new = github_zip(NEW, extra={"aion_target/engine_client.py": "PROTOCOL = 2\n"})
    res = stage(proj, fetch=fake_fetch(NEW, new))
    assert res.status == "incompatible" and "moteur" in res.message
    assert_coherent(proj, OLD)
    same = github_zip(NEW, extra={"aion_target/engine_client.py": "PROTOCOL = 1\n"})
    assert stage(proj, fetch=fake_fetch(NEW, same)).status == "staged"


def test_new_dependency_installed_with_pip(tmp_path, monkeypatch):
    proj = make_install(tmp_path)
    installed = []
    state = {"missing": ["paquet-imaginaire"]}
    monkeypatch.setattr(updater, "missing_requirements", lambda text: list(state["missing"]))

    def pip(reqs):
        installed.extend(reqs)
        state["missing"] = []
        return True, ""
    res = stage(proj, pip_install=pip)
    assert res.status == "staged" and installed == ["paquet-imaginaire"]


def test_dependency_failure_keeps_current(tmp_path, monkeypatch):
    proj = make_install(tmp_path)
    monkeypatch.setattr(updater, "missing_requirements", lambda text: ["paquet-imaginaire"])
    res = stage(proj, pip_install=lambda r: (False, "pas de réseau"))
    assert res.status == "needs_setup"
    assert_coherent(proj, OLD)
    assert not (proj / ".update-staged").exists()


def test_update_refuses_strange_dependency(tmp_path, monkeypatch):
    proj = make_install(tmp_path)
    monkeypatch.setattr(updater, "missing_requirements", lambda text: ["--index-url=http://x"])
    assert stage(proj).status == "error"


def test_dev_checkout_is_never_updated(tmp_path):
    proj = make_install(tmp_path)
    (proj / ".git").mkdir()
    f = fake_fetch(NEW)
    res = updater.check_and_update(proj, OLD, fetch=f)
    assert res.status == "skipped" and f.calls == []


def test_real_requirements_are_satisfied_here():
    req = (Path(updater.__file__).parent.parent / "requirements.txt").read_text()
    assert updater.missing_requirements(req) == []


def test_real_package_passes_the_import_check():
    ok, detail = updater._import_check(Path(updater.__file__).resolve().parent.parent)
    assert ok, detail


def test_powershell_quoting():
    from aion_target.launcher import ps_quote
    assert ps_quote("C:\\Users\\O'Neil") == "'C:\\Users\\O''Neil'"
    assert ps_quote("x'; Remove-Item -Recurse C:\\; '") == "'x''; Remove-Item -Recurse C:\\; '''"


def test_every_app_file_can_be_delivered_by_auto_update():
    """Nouveaux modules / catalogues livrés depuis main sans réinstallation (.exe compris)."""
    pkg = Path(__file__).resolve().parent.parent / "aion_target"
    blocked = [p.relative_to(pkg).as_posix() for p in pkg.rglob("*")
               if p.is_file() and "__pycache__" not in p.parts and p.suffix.lower() not in updater.ALLOWED_SUFFIXES]
    assert blocked == []
    for p in pkg.rglob("*"):                         # noms valides pour l'extraction contrôlée
        if "__pycache__" not in p.parts:
            updater.safe_member_path("root/aion_target/" + p.relative_to(pkg).as_posix())


def test_no_file_by_file_replacement_anymore():
    assert not hasattr(updater, "_sync_files") and not hasattr(updater, "install_tree")


def test_stale_lock_from_a_dead_process_does_not_block_updates(tmp_path, monkeypatch):
    from aion_target.config import config_path
    lock = config_path().parent / "events.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    import socket
    lock.write_text(f"999999\nAion Target\n{socket.gethostname()}\n")    # instance morte
    assert updater._other_instance_running() is False
    assert updater.pid_alive(os.getpid()) and not updater.pid_alive(999999)
    monkeypatch.setenv("AION_TARGET_WAIT_PID", "999999")
    updater.wait_previous_instance(timeout=1)                             # ne bloque pas
    assert "AION_TARGET_WAIT_PID" not in os.environ


def test_missing_settings_folder_means_no_other_instance(tmp_path, monkeypatch):
    """Dossier de réglages absent (premier lancement, réglages effacés) : la bascule n'est pas
    bloquée à tort par un « déjà ouvert »."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "jamais-cree"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "jamais-cree"))
    assert updater._other_instance_running() is False
