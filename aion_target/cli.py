"""Ligne de commande et lancement de l'appli (appelé par __main__ après la mise à jour)."""
import argparse
import os
import sys

from .config import Config, config_path


def _prefer_xwayland():
    # Sous Wayland, une appli ne peut ni se positionner ni capturer l'écran librement.
    # En passant par XWayland (xcb), l'overlay se place où on veut et mss fonctionne,
    # le jeu tournant lui-même sous XWayland via Proton.
    if sys.platform.startswith("linux") and os.environ.get("WAYLAND_DISPLAY"):
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    # Qt en pixels réels, comme mss : sinon un écran à 125 %/150 % décale l'overlay.
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")


def main() -> int:
    ap = argparse.ArgumentParser(prog="aion_target")
    ap.add_argument("--test-lookup", metavar="NOM", help="interroge shugo.gg et affiche GS/CP")
    ap.add_argument("--test-ocr", action="store_true",
                    help="capture la zone, enregistre capture.png et affiche le texte lu")
    ap.add_argument("--select-zone", action="store_true",
                    help="choisir la zone de capture à la souris sur une capture d'écran")
    ap.add_argument("--test-import", action="store_true",
                    help="télécharge UNE page du classement shugo.gg et affiche la réponse exacte")
    ap.add_argument("--install-launcher", action="store_true",
                    help="créer le raccourci dans le menu (KDE / Démarrer)")
    args = ap.parse_args()
    cfg = Config.load()

    if args.test_import:
        import time
        import httpx
        from .importer import API_URL, explain_error, parse_page
        from .shugo import API_HEADERS, USER_AGENT
        params = {"region": "GLOBAL", "subRegion": cfg.region, "sort": "combatPower", "page": 1, "limit": 50}
        print(f"GET {API_URL} {params}")
        t0 = time.monotonic()
        try:
            with httpx.Client(http2=True, timeout=10, headers={"User-Agent": USER_AGENT}) as http:
                resp = http.get(API_URL, params=params, headers=API_HEADERS)
        except httpx.HTTPError as e:
            print(f"✗ réseau : {e!r} — {explain_error('réseau : ' + type(e).__name__)}")
            return 1
        print(f"HTTP {resp.status_code} en {(time.monotonic() - t0) * 1000:.0f} ms, "
              f"{resp.http_version}, type {resp.headers.get('content-type')}")
        if resp.status_code != 200:
            print("✗", explain_error(f"HTTP {resp.status_code}"))
            print("Début de la réponse :", resp.text[:300].replace("\n", " "))
            return 1
        items, more, total = parse_page(resp.json(), cfg.region)
        print(f"✓ {len(items)} joueurs reçus sur {total} (suite : {more}). "
              f"Ex. : {items[0][0].name} CP {items[0][0].combat_power}" if items else "✗ page vide")
        return 0

    if args.install_launcher:
        from .launcher import install
        print(install())
        return 0

    if args.select_zone:
        _prefer_xwayland()
        from PyQt6.QtWidgets import QApplication
        from .capture import make_grabber
        from .zone_picker import pick_zone
        app = QApplication([])  # noqa: F841
        grabber = make_grabber(cfg)
        if pick_zone(cfg, grabber):
            print(f"Zone enregistrée : {cfg.capture}")
            return 0
        print("Annulé, zone inchangée.")
        return 1

    if args.test_lookup:
        from .shugo import ShugoClient
        import time
        t0 = time.monotonic()
        info = ShugoClient(cfg.servers, region=cfg.region, fanout=cfg.lookup_fanout).lookup(args.test_lookup)
        print(f"Recherche : {(time.monotonic() - t0) * 1000:.0f} ms")
        print(info if info else "Introuvable sur les serveurs configurés (ou erreur réseau).")
        return 0 if info else 1

    if args.test_ocr:
        from PIL import Image
        from .name_filter import clean, is_plausible_player
        from .capture import make_grabber
        from .ocr import NameReader
        grabber = make_grabber(cfg)
        print(f"Capture : {grabber.name}")
        img = grabber.grab(*cfg.capture)
        Image.fromarray(img).save("capture.png")
        raw = NameReader(cfg.ocr_min_confidence).read(img)
        name = clean(raw)
        print(f"Zone {cfg.capture} → capture.png (luminosité {img.mean():.0f}/255)")
        print(f"OCR brut : {raw!r}  →  nettoyé : {name!r}  →  joueur plausible : {is_plausible_player(name)}")
        return 0

    _prefer_xwayland()
    _log_to_file_if_no_terminal()
    from .overlay import run
    print(f"Config : {config_path()}")
    return run(cfg)


def _log_to_file_if_no_terminal():
    """Lancé depuis le menu, il n'y a pas de terminal : le journal va dans un fichier."""
    if sys.stdout is not None and sys.stdout.isatty():
        return
    log = config_path().with_name("aion-target.log")
    log.parent.mkdir(parents=True, exist_ok=True)
    f = open(log, "w", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = f
