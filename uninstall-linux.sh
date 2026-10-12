#!/usr/bin/env bash
# Aion Target : désinstallation (installation faite par install-linux.sh).
#
#   ./uninstall-linux.sh           retire l'appli, la commande et les raccourcis
#   ./uninstall-linux.sh --purge   … et aussi réglages, journal et mémoire des joueurs
#
# Les paquets système (PyGObject, GStreamer…) sont laissés en place : d'autres logiciels s'en servent.
set -euo pipefail

# Tout est dans une fonction : bash la lit en entier avant de supprimer son propre dossier.
main() {
    DEST="${AION_TARGET_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/aion-target}"
    APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
    CONF="${XDG_CONFIG_HOME:-$HOME/.config}/aion-target"
    case "$DEST" in
        */aion-target) ;;
        *) echo "Dossier refusé (doit se terminer par /aion-target) : $DEST" >&2; exit 1 ;;
    esac
    PURGE=0
    [ "${1:-}" = "--purge" ] && PURGE=1

    pkill -f "aion-target/venv/bin/python -m aion_target" 2>/dev/null && echo "Aion Target arrêté." || true

    rm -rf "$DEST"
    rm -f "$HOME/.local/bin/aion-target" "$APPS/aion-target.desktop"
    DESKTOP="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
    [ -n "$DESKTOP" ] && [ "$DESKTOP" != "$HOME" ] && rm -f "$DESKTOP/aion-target.desktop"
    command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" 2>/dev/null || true
    echo "Aion Target désinstallé."

    if [ "$PURGE" -eq 1 ]; then
        rm -rf "$CONF"
        echo "Réglages et mémoire des joueurs supprimés ($CONF)."
    else
        echo "Réglages et mémoire des joueurs conservés : $CONF (--purge pour les supprimer)."
    fi
}

main "$@"
