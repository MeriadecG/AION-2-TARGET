"""Point d'entrée : python -m aion_target [--test-lookup NOM | --test-ocr | …]

Au lancement normal (sans option), AVANT de charger le reste de l'appli :
  - une version préparée (« prête à installer ») est installée par renommage de dossiers ;
  - les modules aion_target sont alors rechargés depuis le disque (nouvelle version) ;
  - si la nouvelle version ne se charge pas, retour IMMÉDIAT à la précédente (même processus).
Voir updater.py.
"""
import importlib
import os
import sys


def _purge_modules():
    for name in [m for m in sys.modules if m == "aion_target" or m.startswith("aion_target.")]:
        if name != "aion_target.__main__":
            del sys.modules[name]


def main() -> int:
    updater = None
    if len(sys.argv) <= 1:                       # lancement normal uniquement
        try:
            from . import updater
            if updater.startup_update().updated:
                _purge_modules()
        except Exception as e:                   # la mise à jour ne doit JAMAIS empêcher le lancement
            os.environ["AION_TARGET_UPDATE_MSG"] = f"Mise à jour : erreur ({e})"
    try:
        cli = importlib.import_module("aion_target.cli")
    except Exception as e:
        # Nouvelle version à l'essai qui ne se charge pas : retour à la précédente, puis on relance
        # l'import (aucune fenêtre n'a encore été créée).
        if updater is None or not updater.in_trial():
            raise
        res = updater.rollback(reason=f"erreur au chargement : {type(e).__name__}: {e}")
        os.environ["AION_TARGET_UPDATE_MSG"] = res.message
        os.environ["AION_TARGET_UPDATE_STATUS"] = res.status
        if res.status != "rolled_back":
            raise
        _purge_modules()
        cli = importlib.import_module("aion_target.cli")
    try:
        return cli.main()
    except Exception as e:
        if updater is not None and updater.in_trial():   # le prochain lancement reprend l'ancienne
            updater.rollback(reason=f"erreur au démarrage : {type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    sys.exit(main())
