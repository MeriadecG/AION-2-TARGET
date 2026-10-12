"""Mise à jour automatique : version, téléchargement simulé, vérifications, remplacement."""
import io
import zipfile
from pathlib import Path

import pytest

from aion_target import updater


def make_install(tmp: Path, version="2026.10.12.1") -> Path:
    proj = tmp / "app"
    pkg = proj / "aion_target"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(f'__version__ = "{version}"\n')
    (pkg / "mod.py").write_text("X = 'ancien'\n")
    (proj / "requirements.txt").write_text("PyQt6>=6.6\n")
    return proj


def github_zip(version: str, mod="X = 'nouveau'\n", req="PyQt6>=6.6\npytest\n", extra=None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        root = "AION-2-TARGET-main/"
        z.writestr(root + "aion_target/__init__.py", f'__version__ = "{version}"\n')
        z.writestr(root + "aion_target/mod.py", mod)
        z.writestr(root + "aion_target/assets/x.json", "{}")
        z.writestr(root + "requirements.txt", req)
        for k, v in (extra or {}).items():
            z.writestr(root + k, v)
    return buf.getvalue()


def fake_fetch(version: str, zipdata: bytes | None = None, fail=False):
    calls = []

    def fetch(url, timeout, limit):
        calls.append(url)
        if fail:
            raise OSError("hors ligne")
        if url == updater.VERSION_URL:
            return f'"""doc"""\n__version__ = "{version}"\n'.encode()
        return zipdata if zipdata is not None else github_zip(version)
    fetch.calls = calls
    return fetch


def test_parse_version():
    assert updater.parse_version('__version__ = "2026.10.12.1"') == (2026, 10, 12, 1)
    assert updater.parse_version("2026.10.13") > updater.parse_version("2026.10.12.9")
    assert updater.parse_version("<html>404</html>") is None


def test_requirements_parsing():
    r = updater.requirement_names("PyQt6>=6.6\nhttpx[http2]>=0.27\nnumpy\n# c\npytest\ntzdata  # tz\n")
    assert r == {"pyqt6": "6.6", "httpx": "0.27", "numpy": "", "tzdata": ""}


def test_newer_version_is_installed(tmp_path):
    proj = make_install(tmp_path)
    res = updater.check_and_update(proj, "2026.10.12.1", fetch=fake_fetch("2026.10.13"),
                                   pip_install=lambda r: (True, ""))
    assert res.updated and res.version == "2026.10.13", res.message
    assert (proj / "aion_target" / "mod.py").read_text() == "X = 'nouveau'\n"
    assert (proj / "aion_target" / "assets" / "x.json").exists()
    assert "pytest" in (proj / "requirements.txt").read_text()     # fichier annexe mis à jour
    assert not (proj / "aion_target.old").exists() and not (proj / "aion_target.new").exists()


def test_same_version_downloads_nothing(tmp_path):
    proj = make_install(tmp_path)
    f = fake_fetch("2026.10.12.1")
    res = updater.check_and_update(proj, "2026.10.12.1", fetch=f)
    assert res.status == "up_to_date" and f.calls == [updater.VERSION_URL]


def test_offline_keeps_current(tmp_path):
    proj = make_install(tmp_path)
    res = updater.check_and_update(proj, "2026.10.12.1", fetch=fake_fetch("x", fail=True))
    assert res.status == "offline"
    assert (proj / "aion_target" / "mod.py").read_text() == "X = 'ancien'\n"


def test_broken_code_is_refused(tmp_path):
    proj = make_install(tmp_path)
    bad = github_zip("2026.10.13", mod="def oops(:\n")
    res = updater.check_and_update(proj, "2026.10.12.1", fetch=fake_fetch("2026.10.13", bad))
    assert res.status == "error"
    assert (proj / "aion_target" / "mod.py").read_text() == "X = 'ancien'\n"


def test_inconsistent_archive_refused(tmp_path):
    proj = make_install(tmp_path)
    res = updater.check_and_update(proj, "2026.10.12.1",
                                   fetch=fake_fetch("2026.10.13", github_zip("2026.10.11")))
    assert res.status == "error" and "incohérente" in res.message


def test_zip_slip_refused(tmp_path):
    proj = make_install(tmp_path)
    evil = github_zip("2026.10.13", extra={"../../evil.txt": "x"})
    res = updater.check_and_update(proj, "2026.10.12.1", fetch=fake_fetch("2026.10.13", evil))
    assert res.status == "error"
    assert not (tmp_path / "evil.txt").exists()


def test_new_dependency_installed_with_pip(tmp_path, monkeypatch):
    proj = make_install(tmp_path)
    installed = []
    state = {"missing": ["paquet-imaginaire"]}
    monkeypatch.setattr(updater, "missing_requirements", lambda text: list(state["missing"]))

    def pip(reqs):
        installed.extend(reqs)
        state["missing"] = []
        return True, ""
    res = updater.check_and_update(proj, "2026.10.12.1", fetch=fake_fetch("2026.10.13"), pip_install=pip)
    assert res.updated and installed == ["paquet-imaginaire"]


def test_dependency_failure_keeps_current(tmp_path, monkeypatch):
    proj = make_install(tmp_path)
    monkeypatch.setattr(updater, "missing_requirements", lambda text: ["paquet-imaginaire"])
    res = updater.check_and_update(proj, "2026.10.12.1", fetch=fake_fetch("2026.10.13"),
                                   pip_install=lambda r: (False, "pas de réseau"))
    assert res.status == "needs_setup"
    assert (proj / "aion_target" / "mod.py").read_text() == "X = 'ancien'\n"


def test_dev_checkout_is_never_updated(tmp_path):
    proj = make_install(tmp_path)
    (proj / ".git").mkdir()
    f = fake_fetch("2026.10.13")
    res = updater.check_and_update(proj, "2026.10.12.1", fetch=f)
    assert res.status == "skipped" and f.calls == []


def test_real_requirements_are_satisfied_here():
    req = (Path(updater.__file__).parent.parent / "requirements.txt").read_text()
    assert updater.missing_requirements(req) == []


def test_main_reloads_modules_after_update(monkeypatch):
    """Après une mise à jour, __main__ purge les modules aion_target puis lance cli.main."""
    import sys
    import aion_target.__main__ as entry
    monkeypatch.setattr(sys, "argv", ["aion_target"])
    monkeypatch.setattr(updater, "startup_update",
                        lambda: updater.UpdateResult("updated", "ok", "9"))
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


def test_update_refuses_native_binaries(tmp_path):
    proj = make_install(tmp_path)
    evil = github_zip("2026.10.13", extra={"aion_target/payload.exe": "MZ"})
    res = updater.check_and_update(proj, "2026.10.12.1", fetch=fake_fetch("2026.10.13", evil))
    assert res.status == "error" and "refusé" in res.message
    assert not (proj / "aion_target" / "payload.exe").exists()


def test_update_refuses_strange_dependency(tmp_path, monkeypatch):
    proj = make_install(tmp_path)
    monkeypatch.setattr(updater, "missing_requirements", lambda text: ["--index-url=http://x"])
    res = updater.check_and_update(proj, "2026.10.12.1", fetch=fake_fetch("2026.10.13"),
                                   pip_install=lambda r: (True, ""))
    assert res.status == "error"


def test_powershell_quoting():
    from aion_target.launcher import ps_quote
    assert ps_quote("C:\\Users\\O'Neil") == "'C:\\Users\\O''Neil'"
    assert ps_quote("x'; Remove-Item -Recurse C:\; '") == "'x''; Remove-Item -Recurse C:\; '''"


def test_every_app_file_can_be_delivered_by_auto_update():
    """Nouveaux modules / catalogues livrés depuis main sans réinstallation (.exe compris)."""
    from pathlib import Path
    from aion_target.updater import ALLOWED_SUFFIXES
    pkg = Path(__file__).resolve().parent.parent / "aion_target"
    blocked = [p.relative_to(pkg).as_posix() for p in pkg.rglob("*")
               if p.is_file() and "__pycache__" not in p.parts and p.suffix.lower() not in ALLOWED_SUFFIXES]
    assert blocked == []
