"""Création du raccourci de lancement (menu KDE / menu Démarrer)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
ASSETS = Path(__file__).resolve().parent / "assets"


def desktop_entry(python: str, project_dir: Path, icon: Path) -> str:
    """Contenu du fichier .desktop (Linux)."""
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Name=Aion Target",
        "Comment=Gear score et combat power de ta cible dans Aion 2",
        f'Exec="{python}" -m aion_target',
        f"Path={project_dir}",
        f"Icon={icon}",
        "Terminal=false",
        "Categories=Game;Utility;",
        "StartupWMClass=aion-target",
        "",
    ])


def install() -> str:
    """Installe le raccourci et renvoie un message pour l'utilisateur."""
    if sys.platform == "win32":
        return _install_windows()
    return _install_linux()


def _install_linux() -> str:
    apps = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "applications"
    apps.mkdir(parents=True, exist_ok=True)
    target = apps / "aion-target.desktop"
    target.write_text(desktop_entry(sys.executable, PROJECT_DIR, ASSETS / "icon.png"), encoding="utf-8")
    target.chmod(0o755)
    try:  # rafraîchit le menu ; facultatif (KDE détecte aussi le fichier tout seul)
        subprocess.run(["update-desktop-database", str(apps)], capture_output=True, check=False)
    except FileNotFoundError:
        pass

    msg = [f"Raccourci créé : {target}", "→ menu KDE, catégorie Jeux (ou cherche « Aion Target »)."]
    desktop = _xdg_desktop_dir()
    if desktop and desktop.is_dir():
        copy = desktop / "aion-target.desktop"
        copy.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
        copy.chmod(0o755)
        msg.append(f"Copie sur le bureau : {copy} (au 1er clic, KDE peut demander de l'autoriser).")
    return "\n".join(msg)


def _xdg_desktop_dir() -> Path | None:
    try:
        out = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, check=False)
        path = out.stdout.strip()
        if path and path != str(Path.home()):
            return Path(path)
    except FileNotFoundError:
        pass
    return None


def _install_windows() -> str:
    # Version portable : « Aion Target.exe » (variable posée par le lanceur).
    portable_exe = os.environ.get("AION_TARGET_EXE")
    if portable_exe:
        exe, args, wd = Path(portable_exe), "", Path(portable_exe).parent
    else:
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        exe = pythonw if pythonw.exists() else Path(sys.executable)
        args, wd = "-m aion_target", PROJECT_DIR
    start_menu = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs"
    desktop = Path(os.environ.get("USERPROFILE", Path.home())) / "Desktop"
    created = []
    for folder in (start_menu, desktop):
        if not folder.is_dir():
            continue
        lnk = folder / "Aion Target.lnk"
        ps = (
            "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
            "$s.TargetPath='{exe}';$s.Arguments='{args}';"
            "$s.WorkingDirectory='{wd}';$s.IconLocation='{ico}';$s.Save()"
        ).format(lnk=lnk, exe=exe, args=args, wd=wd, ico=ASSETS / "icon.ico")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        created.append(str(lnk))
    return "Raccourci créé : " + " · ".join(created)
