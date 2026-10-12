"""Point d'entrée : python -m aion_target [--test-lookup NOM | --test-ocr | …]

Au lancement normal (sans option), la mise à jour automatique est tentée AVANT de charger
le reste de l'appli : si une version plus récente est installée, tous les modules
aion_target sont rechargés depuis le disque, donc l'appli démarre directement sur le
nouveau code (aucun redémarrage).
"""
import importlib
import sys


def main() -> int:
    if len(sys.argv) <= 1:                       # lancement normal uniquement
        try:
            from . import updater
            if updater.startup_update().updated:
                for name in [m for m in sys.modules if m == "aion_target" or m.startswith("aion_target.")]:
                    if name != "aion_target.__main__":
                        del sys.modules[name]
        except Exception as e:                   # la mise à jour ne doit JAMAIS empêcher le lancement
            import os
            os.environ["AION_TARGET_UPDATE_MSG"] = f"Mise à jour : erreur ({e})"
    return importlib.import_module("aion_target.cli").main()


if __name__ == "__main__":
    sys.exit(main())
