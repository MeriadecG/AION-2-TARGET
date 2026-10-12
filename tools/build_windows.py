"""Construit la version Windows PORTABLE d'Aion Target depuis Linux (ou Windows).

Résultat : un dossier « Aion Target/ » (et son zip) contenant
  Aion Target.exe              lanceur sans console (double-clic)
  Aion Target - diagnostic.exe même chose avec une console (messages visibles)
  python/                      Python 3.12 Windows autonome + dépendances
  app/aion_target/             l'application
Rien à installer chez l'utilisateur.

Usage (une seule commande, télécharge tout ce qu'il faut, ~250 Mo) :
  python tools/build_windows.py
→ dist/AionTarget-Setup.exe   installateur (si makensis est présent : sudo dnf install mingw32-nsis)
→ dist/AionTarget-Windows.zip version portable (dézipper et lancer)

L'installateur (NSIS) installe pour l'utilisateur courant, sans droits administrateur, dans
%LOCALAPPDATA%/Programs/Aion Target, crée les raccourcis menu Démarrer + bureau et un
désinstalleur visible dans « Applications installées ». Réinstaller = mettre à jour (réglages
et mémoire des joueurs conservés dans %APPDATA%/aion-target).

Le lanceur .exe est celui de distlib (utilisé par pip pour ses commandes sous Windows) :
il lit la ligne « #! » ajoutée à sa fin, résout le chemin RELATIF à son propre dossier
(python\\pythonw.exe) et exécute l'archive zip qui suit, dont le __main__.py démarre l'app.
"""
from __future__ import annotations

import argparse
import io
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BOOTSTRAP = r'''
import os, sys, traceback

exe = os.path.abspath(sys.argv[0])
here = os.path.dirname(exe)
os.environ["AION_TARGET_EXE"] = os.path.join(here, "Aion Target.exe")
sys.path.insert(0, os.path.join(here, "app"))
sys.argv[0] = "aion_target"

try:
    from aion_target.__main__ import main
    sys.exit(main())
except SystemExit:
    raise
except BaseException:
    # Sans console (pythonw), une erreur serait invisible : on l'écrit et on l'affiche.
    log_dir = os.path.join(os.environ.get("APPDATA", here), "aion-target")
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, "crash.log")
    with open(path, "w", encoding="utf-8") as f:
        traceback.print_exc(file=f)
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            None, "Aion Target n'a pas pu démarrer.\n\nDétails : " + path, "Aion Target", 0x10)
    except Exception:
        pass
    raise
'''


def make_launcher(stub: bytes, interpreter: str) -> bytes:
    """Lanceur distlib + ligne #! (chemin relatif) + archive zip contenant __main__.py."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("__main__.py", BOOTSTRAP)
    return stub + f"#!{interpreter}\n".encode() + buf.getvalue()   # format exact de distlib


def find_distlib_launchers() -> Path:
    try:
        import distlib
        d = Path(distlib.__file__).parent
        if (d / "w64.exe").exists():
            return d
    except ImportError:
        pass
    for cand in Path("/tmp").rglob("distlib/w64.exe"):
        return cand.parent
    raise SystemExit("Lanceurs distlib introuvables : pip download distlib, puis dézipper le wheel.")


PYTHON_URL = ("https://github.com/astral-sh/python-build-standalone/releases/download/20250612/"
              "cpython-3.12.11+20250612-x86_64-pc-windows-msvc-install_only_stripped.tar.gz")
PIP_WIN = ["--platform", "win_amd64", "--python-version", "3.12", "--implementation", "cp",
           "--only-binary=:all:"]
# Seuls QtCore / QtGui / QtWidgets sont utilisés : le reste de Qt est retiré (−200 Mo).
QT_KEEP_DLLS = {"Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll", "Qt6Svg.dll",
                "opengl32sw.dll", "d3dcompiler_47.dll"}
QT_KEEP_PYD = {"QtCore.pyd", "QtGui.pyd", "QtWidgets.pyd", "QtSvg.pyd"}
QT_KEEP_PLUGINS = {"platforms", "styles", "imageformats", "iconengines"}
# Plugins qui dépendent de DLL retirées (vérifié avec objdump -p) :
QT_DROP_PLUGINS = {"imageformats/qpdf.dll"}


def download(url: str, dest: Path) -> Path:
    if not dest.exists():
        print(f"Téléchargement {url.rsplit('/', 1)[-1]}…")
        urllib.request.urlretrieve(url, dest)
    return dest


def pip(*args):
    subprocess.run([sys.executable, "-m", "pip", *args, "-q", "--disable-pip-version-check"],
                   check=True)


def prepare_site(work: Path) -> Path:
    """Dépendances en version Windows (installables depuis Linux), puis allègement."""
    site = work / "site"
    if site.exists():
        shutil.rmtree(site)
    reqs = [l.strip() for l in (ROOT / "requirements.txt").read_text().splitlines()
            if l.strip() and not l.startswith(("#", "pytest"))]
    print("Dépendances Windows…")
    pip("install", "--target", str(site), *PIP_WIN, *reqs)
    # Les dépendances « seulement sous Windows » ne sont pas résolues depuis Linux :
    pip("install", "--target", str(site), *PIP_WIN, "--no-deps", "colorama")
    trim_site(site)
    return site


def trim_site(site: Path):
    for name in ("Xlib", "bin"):
        shutil.rmtree(site / name, ignore_errors=True)
    for d in site.glob("python_xlib-*"):
        shutil.rmtree(d)
    qt = site / "PyQt6" / "Qt6"
    for dll in (qt / "bin").glob("*.dll"):
        if dll.name not in QT_KEEP_DLLS and not dll.name.startswith(("msvc", "vcruntime", "concrt")):
            dll.unlink()
    for exe in (qt / "bin").glob("*.exe"):
        exe.unlink()
    for sub in ("qml", "qsci"):
        shutil.rmtree(qt / sub, ignore_errors=True)
    for f in (qt / "translations").glob("*"):
        if not f.name.startswith(("qtbase_fr", "qtbase_en")):
            f.unlink()
    for d in (qt / "plugins").iterdir():
        if d.name not in QT_KEEP_PLUGINS:
            shutil.rmtree(d)
    for rel in QT_DROP_PLUGINS:
        (qt / "plugins" / rel).unlink(missing_ok=True)
    for pyd in (site / "PyQt6").glob("*.pyd"):
        if pyd.name not in QT_KEEP_PYD and not pyd.name.startswith("sip."):
            pyd.unlink()
    for f in (site / "cv2").glob("opencv_videoio_ffmpeg*.dll"):
        f.unlink()                                   # vidéo : inutile pour l'OCR
    for d in list(site.rglob("__pycache__")):
        shutil.rmtree(d, ignore_errors=True)


def get_launchers(work: Path) -> Path:
    dl = work / "distlib"
    if not (dl / "distlib" / "w64.exe").exists():
        pip("download", "--no-deps", "-d", str(work), "distlib")
        wheel = next(work.glob("distlib-*.whl"))
        with zipfile.ZipFile(wheel) as z:
            z.extractall(dl)
    return dl / "distlib"


def build(python_tgz: Path, site: Path, out: Path, launchers: Path | None = None) -> Path:
    target = out / "Aion Target"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    with tarfile.open(python_tgz) as t:
        t.extractall(target, filter="data")          # → target/python/...
    site_dest = target / "python" / "Lib" / "site-packages"
    shutil.copytree(site, site_dest, dirs_exist_ok=True)

    shutil.copytree(ROOT / "aion_target", target / "app" / "aion_target",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    launchers = launchers or find_distlib_launchers()
    (target / "Aion Target.exe").write_bytes(
        make_launcher((launchers / "w64.exe").read_bytes(), r"python\pythonw.exe"))
    (target / "Aion Target - diagnostic.exe").write_bytes(
        make_launcher((launchers / "t64.exe").read_bytes(), r"python\python.exe"))
    # Secours si le .exe était bloqué par un antivirus : même chose en script.
    (target / "Lancer (secours).bat").write_text(
        '@echo off\r\ncd /d "%~dp0"\r\nstart "" "python\\pythonw.exe" "Aion Target.exe"\r\n',
        encoding="ascii")
    shutil.copy(ROOT / "LISEZMOI-WINDOWS.txt", target / "LISEZMOI.txt")

    archive = shutil.make_archive(str(out / "AionTarget-Windows"), "zip", out, "Aion Target")
    return Path(archive)


NSIS = r"""
Unicode true
!include "MUI2.nsh"
!include "FileFunc.nsh"

!define APP "Aion Target"
!define VERSION "{version}"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\AionTarget"

Name "${{APP}}"
OutFile "{outfile}"
InstallDir "$LOCALAPPDATA\Programs\${{APP}}"
InstallDirRegKey HKCU "Software\${{APP}}" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
BrandingText "${{APP}} ${{VERSION}}"

VIProductVersion "{vi_version}"
VIAddVersionKey /LANG=1036 "ProductName" "${{APP}}"
VIAddVersionKey /LANG=1036 "FileDescription" "Installation d'${{APP}}"
VIAddVersionKey /LANG=1036 "FileVersion" "${{VERSION}}"
VIAddVersionKey /LANG=1036 "ProductVersion" "${{VERSION}}"
VIAddVersionKey /LANG=1036 "LegalCopyright" "Usage personnel"

!define MUI_ICON "{icon}"
!define MUI_UNICON "{icon}"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TEXT "Aion Target affiche le gear score et le combat power de ta cible dans AION 2, et prévient avant les événements.$\r$\n$\r$\nInstallation pour ton compte Windows uniquement : aucun droit administrateur n'est demandé.$\r$\n$\r$\nSi Aion Target est ouvert, ferme-le avant de continuer (clic droit sur l'icône près de l'horloge > Quitter)."
!define MUI_FINISHPAGE_RUN "$INSTDIR\Aion Target.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Lancer Aion Target"
!define MUI_FINISHPAGE_SHOWREADME "$INSTDIR\LISEZMOI.txt"
!define MUI_FINISHPAGE_SHOWREADME_TEXT "Lire le mode d'emploi"
!define MUI_FINISHPAGE_SHOWREADME_NOTCHECKED

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "French"

Section "Aion Target"
    SetOutPath "$INSTDIR"
    ; Mise à jour propre : l'ancienne version de l'appli et de Python est remplacée.
    RMDir /r "$INSTDIR\app"
    RMDir /r "$INSTDIR\python"
    File /r "{source}"

    CreateShortcut "$SMPROGRAMS\${{APP}}.lnk" "$INSTDIR\Aion Target.exe" "" "$INSTDIR\app\aion_target\assets\icon.ico"
    CreateShortcut "$DESKTOP\${{APP}}.lnk" "$INSTDIR\Aion Target.exe" "" "$INSTDIR\app\aion_target\assets\icon.ico"

    WriteUninstaller "$INSTDIR\Desinstaller.exe"
    WriteRegStr HKCU "Software\${{APP}}" "InstallDir" "$INSTDIR"
    WriteRegStr HKCU "${{UNINST_KEY}}" "DisplayName" "${{APP}}"
    WriteRegStr HKCU "${{UNINST_KEY}}" "DisplayVersion" "${{VERSION}}"
    WriteRegStr HKCU "${{UNINST_KEY}}" "Publisher" "Aion Target"
    WriteRegStr HKCU "${{UNINST_KEY}}" "DisplayIcon" "$INSTDIR\app\aion_target\assets\icon.ico"
    WriteRegStr HKCU "${{UNINST_KEY}}" "InstallLocation" "$INSTDIR"
    WriteRegStr HKCU "${{UNINST_KEY}}" "UninstallString" '"$INSTDIR\Desinstaller.exe"'
    WriteRegDWORD HKCU "${{UNINST_KEY}}" "NoModify" 1
    WriteRegDWORD HKCU "${{UNINST_KEY}}" "NoRepair" 1
    ${{GetSize}} "$INSTDIR" "/S=0K" $0 $1 $2
    WriteRegDWORD HKCU "${{UNINST_KEY}}" "EstimatedSize" $0
SectionEnd

Section "Uninstall"
    Delete "$SMPROGRAMS\${{APP}}.lnk"
    Delete "$DESKTOP\${{APP}}.lnk"
    ; Uniquement ce que l'installateur a posé (jamais de suppression récursive du dossier choisi).
    RMDir /r "$INSTDIR\app"
    RMDir /r "$INSTDIR\python"
    Delete "$INSTDIR\Aion Target.exe"
    Delete "$INSTDIR\Aion Target - diagnostic.exe"
    Delete "$INSTDIR\Lancer (secours).bat"
    Delete "$INSTDIR\LISEZMOI.txt"
    Delete "$INSTDIR\Desinstaller.exe"
    RMDir "$INSTDIR"
    DeleteRegKey HKCU "${{UNINST_KEY}}"
    DeleteRegKey HKCU "Software\${{APP}}"
    MessageBox MB_YESNO|MB_ICONQUESTION "Supprimer aussi tes réglages et la mémoire des joueurs ?$\r$\n($APPDATA\aion-target)" /SD IDNO IDNO keep
        RMDir /r "$APPDATA\aion-target"
    keep:
SectionEnd
"""


def build_setup(target: Path, out: Path, work: Path) -> Path | None:
    """Installateur Windows avec NSIS (makensis : paquet mingw32-nsis sous Fedora, nsis sous Debian)."""
    makensis = shutil.which("makensis")
    if not makensis:
        print("makensis introuvable : installateur non créé (Fedora : sudo dnf install mingw32-nsis).")
        return None
    sys.path.insert(0, str(ROOT))
    from aion_target import __version__
    parts = [int(p) for p in __version__.split(".")[:3]] + [0, 0, 0]
    setup = out / "AionTarget-Setup.exe"
    nsi = work / "installer.nsi"
    nsi.write_text(NSIS.format(version=__version__, vi_version=".".join(map(str, parts[:4])),
                               outfile=setup, icon=ROOT / "aion_target" / "assets" / "icon.ico",
                               source=target / "*"), encoding="utf-8")
    print("Installateur (NSIS)…")
    subprocess.run([makensis, "-V2", "-INPUTCHARSET", "UTF8", str(nsi)], check=True)
    return setup


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Construit dist/AionTarget-Windows.zip")
    ap.add_argument("--out", default=ROOT / "dist", type=Path)
    ap.add_argument("--work", default=ROOT / "build" / "windows", type=Path,
                    help="dossier de travail (téléchargements gardés pour les fois suivantes)")
    ap.add_argument("--python", type=Path, help="archive Python Windows déjà téléchargée")
    ap.add_argument("--site", type=Path, help="dépendances Windows déjà préparées")
    ap.add_argument("--launchers", type=Path)
    ap.add_argument("--no-setup", action="store_true", help="ne pas créer AionTarget-Setup.exe")
    a = ap.parse_args()
    a.work.mkdir(parents=True, exist_ok=True)
    py = a.python or download(PYTHON_URL, a.work / "python-windows.tar.gz")
    site = a.site or prepare_site(a.work)
    launchers = a.launchers or get_launchers(a.work)
    print("Assemblage…")
    zip_path = build(py, site, a.out, launchers)
    setup = None if a.no_setup else build_setup(a.out / "Aion Target", a.out, a.work)
    print(f"\nTerminé :\n  {zip_path}  ({zip_path.stat().st_size / 1e6:.0f} Mo, version portable)")
    if setup:
        print(f"  {setup}  ({setup.stat().st_size / 1e6:.0f} Mo, installateur)")
