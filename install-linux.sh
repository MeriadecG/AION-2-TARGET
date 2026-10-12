#!/usr/bin/env bash
# Aion Target : installation / mise à jour pour l'utilisateur courant (Fedora / Nobara, Debian / Ubuntu).
#
#   ./install-linux.sh            installe ou met à jour (réglages et mémoire des joueurs conservés)
#   ./install-linux.sh --run      … puis lance l'appli
#   ./install-linux.sh --no-system  sans installer les paquets système (dnf / apt)
#
# Installé dans ~/.local/share/aion-target (app + environnement Python), commande « aion-target »
# dans ~/.local/bin, raccourci dans le menu (catégorie Jeux) et sur le bureau.
# Réglages : ~/.config/aion-target (jamais touché ici). Désinstaller : ./uninstall-linux.sh
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${AION_TARGET_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/aion-target}"
APP="$DEST/app"
VENV="$DEST/venv"
BIN="$HOME/.local/bin"
case "$DEST" in
    */aion-target) ;;
    *) echo "Dossier d'installation refusé (doit se terminer par /aion-target) : $DEST" >&2; exit 1 ;;
esac
RUN=0
SYSTEM=1
for arg in "$@"; do
    case "$arg" in
        --run) RUN=1 ;;
        --no-system) SYSTEM=0 ;;
        -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "Option inconnue : $arg" >&2; exit 2 ;;
    esac
done

bold() { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '    \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31mErreur : %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] && die "lance ce script SANS sudo (il s'installe dans ton dossier personnel ; il demandera sudo lui-même pour les paquets système)."
[ -f "$SRC/aion_target/__main__.py" ] || die "dossier de l'appli introuvable à côté du script ($SRC)."

# --- 1. Python ------------------------------------------------------------------------------
bold "Python"
command -v python3 >/dev/null || die "python3 absent (Fedora : sudo dnf install python3)."
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || die "Python 3.10 ou plus requis."
PYVER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
ok "python3 $PYVER"

# --- 2. Paquets système (capture d'écran Wayland via le portail KDE, son, venv) -------------
if [ "$SYSTEM" -eq 1 ]; then
    bold "Paquets système"
    if command -v dnf >/dev/null; then
        PKGS=(python3-gobject gstreamer1-plugin-pipewire gstreamer1-plugins-base pipewire-utils)
        MISSING=()
        for p in "${PKGS[@]}"; do rpm -q "$p" >/dev/null 2>&1 || MISSING+=("$p"); done
        if [ "${#MISSING[@]}" -gt 0 ]; then
            echo "    À installer : ${MISSING[*]} (mot de passe sudo demandé)"
            sudo dnf install -y "${MISSING[@]}" || warn "installation dnf incomplète : la capture Wayland peut ne pas marcher."
        fi
        ok "dépendances dnf présentes"
    elif command -v apt-get >/dev/null; then
        PKGS=(python3-venv python3-gi gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 gstreamer1.0-pipewire pipewire-bin)
        MISSING=()
        for p in "${PKGS[@]}"; do dpkg -s "$p" >/dev/null 2>&1 || MISSING+=("$p"); done
        if [ "${#MISSING[@]}" -gt 0 ]; then
            echo "    À installer : ${MISSING[*]} (mot de passe sudo demandé)"
            sudo apt-get install -y "${MISSING[@]}" || warn "installation apt incomplète."
        fi
        ok "dépendances apt présentes"
    else
        warn "gestionnaire de paquets non reconnu : installe à la main PyGObject + GStreamer PipeWire."
    fi
fi

# --- 3. Copie de l'appli (remplacement propre, réglages ailleurs) -----------------------------
bold "Copie de l'appli dans $DEST"
mkdir -p "$DEST"
if [ "$SRC" != "$APP" ]; then
    rm -rf "$APP.new"
    mkdir -p "$APP.new"
    cp -a "$SRC/aion_target" "$SRC/requirements.txt" "$APP.new/"
    for f in README.md install-linux.sh uninstall-linux.sh; do
        [ -f "$SRC/$f" ] && cp -a "$SRC/$f" "$APP.new/"
    done
    find "$APP.new" -name '__pycache__' -prune -exec rm -rf {} +
    rm -rf "$APP.old"
    [ -d "$APP" ] && mv "$APP" "$APP.old"
    mv "$APP.new" "$APP"
    rm -rf "$APP.old"
fi
ok "appli copiée"

# --- 4. Environnement Python (recréé si la version de Python a changé : mise à jour Fedora) ---
bold "Environnement Python"
if [ -x "$VENV/bin/python" ] && [ "$(cat "$VENV/.aion-python" 2>/dev/null)" = "$PYVER" ] \
        && "$VENV/bin/python" -c 'import sys' 2>/dev/null; then
    ok "environnement existant réutilisé"
else
    rm -rf "$VENV"
    # --system-site-packages : PyGObject (gi) vient du système, il ne s'installe pas avec pip.
    python3 -m venv --system-site-packages "$VENV" || die "création du venv impossible."
    echo "$PYVER" > "$VENV/.aion-python"
    ok "environnement créé"
fi
REQ="$(mktemp)"
grep -v -E '^\s*(#|pytest)' "$APP/requirements.txt" > "$REQ"
echo "    Téléchargement des dépendances (PyQt6, OCR…) : 1 à 3 min la première fois"
"$VENV/bin/python" -m pip install -q --disable-pip-version-check --upgrade pip
"$VENV/bin/python" -m pip install -q --disable-pip-version-check -r "$REQ" || die "pip install a échoué (connexion ?)."
rm -f "$REQ"
ok "dépendances installées"

# --- 5. Vérification -------------------------------------------------------------------------
bold "Vérification"
(cd "$APP" && "$VENV/bin/python" - <<'PY') || die "l'appli ne s'importe pas (voir le message ci-dessus)."
import zoneinfo
from PyQt6 import QtWidgets  # noqa: F401
import httpx, rapidocr  # noqa: F401
import aion_target.events, aion_target.notify, aion_target.overlay  # noqa: F401
zoneinfo.ZoneInfo("Europe/Paris")
PY
ok "appli et dépendances OK"
if "$VENV/bin/python" -c 'import gi; gi.require_version("Gst", "1.0"); from gi.repository import Gst' 2>/dev/null; then
    ok "capture Wayland (PyGObject + GStreamer) OK"
else
    warn "PyGObject / GStreamer introuvable : la capture d'écran sous Wayland ne marchera pas."
fi
if command -v pw-play >/dev/null || command -v paplay >/dev/null; then
    ok "lecteur audio présent (sons des notifications)"
else
    warn "ni pw-play ni paplay : les sons de notification seront muets."
fi

# --- 6. Commande et raccourcis ---------------------------------------------------------------
bold "Raccourcis"
mkdir -p "$BIN"
cat > "$BIN/aion-target" <<EOF
#!/bin/sh
# Lanceur Aion Target (créé par install-linux.sh)
cd "$APP" && exec "$VENV/bin/python" -m aion_target "\$@"
EOF
chmod +x "$BIN/aion-target"
ok "commande : $BIN/aion-target"
case ":$PATH:" in *":$BIN:"*) ;; *) warn "$BIN n'est pas dans le PATH : utilise le menu, ou $BIN/aion-target" ;; esac
(cd "$APP" && "$VENV/bin/python" -m aion_target --install-launcher) | sed 's/^/    /'

bold "Terminé"
echo "    Lance « Aion Target » depuis le menu (Jeux) ou tape : aion-target"
echo "    Mise à jour : dézippe la nouvelle version et relance ./install-linux.sh"
echo "    Désinstaller : $APP/uninstall-linux.sh"
if [ "$RUN" -eq 1 ]; then
    nohup "$BIN/aion-target" >/dev/null 2>&1 &
    echo "    Aion Target démarre…"
fi
