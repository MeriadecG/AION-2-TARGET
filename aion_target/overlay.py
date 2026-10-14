"""Overlay : 4 cadres (lecture, GS, CP, détails) + boucle de lecture en arrière-plan."""
from __future__ import annotations

import os
import sys
import time
import traceback
from collections import deque

from PyQt6.QtCore import QObject, QPoint, QRect, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

from .config import Config, config_path
from .style import GREEN, RED, frame_contents, register_bundled_fonts, sample_info, threat
from .tracker import TargetTracker

HANDLE = 12
ROLE_LABELS = {"zone": "Lecture du nom", "gs": "GS", "cp": "CP", "details": "Classe · Nv · Serveur",
               "rank": "TOP"}
ROLE_COLORS = {"zone": QColor(0, 170, 255), "gs": QColor(242, 193, 78),
               "cp": QColor(111, 227, 245), "details": QColor(200, 200, 200),
               "rank": QColor(232, 166, 255)}
MY_CP_REFRESH = 600  # secondes


# Derniers messages gardés en mémoire : bouton « Copier le rapport de vitesse ».
LOG_RING: deque[str] = deque(maxlen=600)


def log(msg: str):
    now = time.time()
    line = time.strftime("%H:%M:%S", time.localtime(now)) + f".{int(now % 1 * 1000):03d} {msg}"
    LOG_RING.append(line)
    print(line, flush=True)


REPORT_KEYS = ("OCR :", "Cible", "Affiché", "Perf", "ERREUR", "base shugo", "fiche shugo", "Prêt.",
               "Capture", "Import", "Retard de capture", "Le carré de test", "Écran partagé")


def speed_report(max_lines: int = 80) -> str:
    """Résumé à copier-coller : lignes utiles au diagnostic de vitesse, les plus récentes."""
    import platform
    lines = [l for l in LOG_RING if any(k in l for k in REPORT_KEYS)][-max_lines:]
    from .metrics import STATS, extra_lines
    head = f"Aion Target — rapport de vitesse ({platform.system()} {platform.release()}, " \
           f"{os.cpu_count()} cœurs)"
    pct = STATS.report() + extra_lines()
    body = [*pct, *lines] if (pct or lines) else ["(aucune lecture encore : cible des joueurs en jeu)"]
    return "\n".join([head, *body])


class PerfStats:
    """Mesures réelles, écrites dans le journal toutes les 10 s (diagnostic de vitesse)."""

    def __init__(self, every: float = 10.0):
        self.every = every
        self._frames0 = 0
        self.reset(time.monotonic())

    def reset(self, now: float):
        self.t0, self.loops, self.ocr_ms, self.fast, self.ages = now, 0, [], 0, []

    def add(self, ocr_ms: float, fast: bool, frame_age_ms: float | None):
        self.loops += 1
        self.ocr_ms.append(ocr_ms)
        self.fast += fast
        if frame_age_ms is not None:
            self.ages.append(frame_age_ms)

    def report(self, grabber, now: float) -> str | None:
        if now - self.t0 < self.every or not self.loops:
            return None
        span = now - self.t0
        frames = getattr(grabber, "frames_received", None)
        cap = ""
        if frames is not None:
            fps = (frames - self._frames0) / span
            self._frames0 = frames
            cap = f"capture KDE {fps:.0f} img/s · "
        ocr = sorted(self.ocr_ms)
        age = f" · âge image lue moy {sum(self.ages) / len(self.ages):.0f} ms max {max(self.ages):.0f} ms" \
            if self.ages else ""
        msg = (f"Perf : {cap}{self.loops / span:.1f} lectures/s · OCR médiane {ocr[len(ocr) // 2]:.0f} ms "
               f"max {ocr[-1]:.0f} ms · lecture seule {100 * self.fast / self.loops:.0f} %{age}")
        self.reset(now)
        return msg


# --- boucle de lecture ---------------------------------------------------------------

class Worker(QObject):
    """Capture → OCR → nom confirmé → affichage (cache instantané, puis score frais)."""
    status = pyqtSignal(str, object)   # (nom, CharacterInfo | None | "loading" | "notfound" | "error")
    timing = pyqtSignal(str, float)    # (nom, secondes entre la 1re lecture et l'affichage)
    my_cp = pyqtSignal(object)         # CP de ton personnage (int | None)
    import_progress = pyqtSignal(int, int, bool)   # (importés, total, terminé)

    def __init__(self, cfg: Config, grabber=None):
        super().__init__()
        self.cfg = cfg
        self.grabber = grabber   # portail : créé dans le thread principal ; mss : créé ici
        self.running = True
        self.paused = False
        self.lookup = None
        self.tracker = TargetTracker(cfg.stable_reads, cfg.blacklist)
        self._first_seen: dict[str, float] = {}
        self._timed: set[str] = set()
        self._me_checked = 0.0
        self._import_thread = None
        self._import_checked = 0.0
        self._full_memo: tuple[str, str, str] | None = None
        self._change_at = 0.0
        # Génération de cible : chaque changement de cible l'incrémente. Un résultat réseau
        # n'est affiché que s'il concerne la cible ET le serveur courants (pas d'homonyme,
        # pas de réponse tardive d'une cible précédente).
        self.generation = 0
        self._hint: str | None = None
        self._shown_gen: set[tuple[int, str]] = set()
        self._skipped_seen = 0
        self.paint_meta: tuple | None = None   # (nom, génération, début, étape, émis à)
        self.npcs = None                   # NpcIndex, chargé dans le thread de lecture
        self._last_npc: str | None = None

    def run(self):
        try:
            self._loop()
        except Exception:
            log("ERREUR fatale dans la boucle :\n" + traceback.format_exc())
            self.status.emit("Erreur : voir le journal", "error")

    def _build_lookup(self):
        from .cache import PlayerCache
        from .lookup import PlayerLookup
        from .shugo import ShugoClient

        def learned(slug):
            log(f"Nouveau serveur appris : {slug}")
            if slug not in self.cfg.servers:
                self.cfg.servers.append(slug)
                self.cfg.save()

        client = ShugoClient(self.cfg.servers, region=self.cfg.region,
                             fanout=self.cfg.lookup_fanout, on_new_server=learned)
        client.warmup()
        cache = PlayerCache(config_path().with_name("players.db"),
                            max_bytes=self.cfg.cache_max_mb * 1024 * 1024)
        self._client = client
        return PlayerLookup(client, cache, on_result=self._on_result, log=log)

    def _learn_server(self, slug: str):
        # Passe par le client : il prévient _build_lookup → journal + config, une seule fois.
        self.lookup.client._learn(slug)

    def maybe_import(self, force: bool = False):
        """Import du classement shugo.gg en arrière-plan (au plus toutes les 24 h)."""
        import threading
        from .importer import LeaderboardImporter
        if not self.lookup or (self._import_thread and self._import_thread.is_alive()):
            return
        limit = None if self.cfg.import_max_players < 0 else self.cfg.import_max_players
        if force:
            self.cfg.import_state["last_full"] = 0
        imp = LeaderboardImporter(
            self._client.http, self.lookup.cache, self.cfg.import_state, region=self.cfg.region,
            max_players=limit, on_new_server=self._learn_server,
            on_progress=lambda done, total: (self.cfg.save(), self.import_progress.emit(done, total, False)),
            should_stop=lambda: not self.running)
        if not imp.due():
            return

        def work():
            from .importer import explain_error
            log("Import du classement shugo.gg…")
            self.import_progress.emit(0, self.cfg.import_state.get("total", 0), False)
            try:
                n = imp.run()
            except Exception:
                # Plus jamais d'échec silencieux : trace complète + message dans la fenêtre.
                n = 0
                self.cfg.import_state["last_error"] = "erreur interne (voir le journal)"
                log("ERREUR pendant l'import :\n" + traceback.format_exc())
            self.cfg.save()
            err = self.cfg.import_state.get("last_error")
            if err:
                log(f"Import arrêté après {n} joueurs : {err} — {explain_error(err)}")
            else:
                log(f"Import terminé : {n} joueurs reçus")
            if n:
                self.lookup.rebuild_names()
                log(f"Dictionnaire des noms : {len(self.lookup.names)} joueurs")
            self.import_progress.emit(len(self.lookup.cache), self.cfg.import_state.get("total", 0), True)
        self._import_thread = threading.Thread(target=work, name="import", daemon=True)
        self._import_thread.start()

    def refresh_me(self):
        """Ton personnage a changé dans la fenêtre : recalculer la pastille."""
        self._me_checked = 0.0

    def _check_me(self):
        me = self.cfg.my_character.strip()
        if not me or not self.lookup or time.monotonic() - self._me_checked < MY_CP_REFRESH:
            return
        self._me_checked = time.monotonic()
        cached = self.lookup.cached(me)
        if cached:
            self.my_cp.emit(cached.combat_power)
        if not self.lookup.prefetch(me) and not cached:
            self.my_cp.emit(None)

    def _on_result(self, name: str, info):
        """Appelé depuis un thread réseau quand un résultat frais arrive."""
        if name.casefold() == self.cfg.my_character.strip().casefold() and not self.lookup.ambiguous(name):
            self.my_cp.emit(info.combat_power if info else None)
        current, hint = self.tracker.current, self._hint
        if not (current and current.casefold() == name.casefold()):
            return                                   # réponse tardive d'une autre cible : ignorée
        if info is not None and hint and info.server != hint:
            return                                   # homonyme d'un autre serveur : ignoré
        amb = self.lookup.ambiguous(current, hint)
        if amb:
            from .style import Ambiguous
            self.status.emit(current, Ambiguous(amb))
            return
        self._show(current, info or self.lookup.cached(name, hint), fresh=info is not None)

    def _is_npc(self, ocr_name: str, raw: str, fuzzy: bool = False) -> bool:
        npcs = self.npcs
        if not npcs:
            return False
        names = self.lookup.names
        npc = npcs.match(ocr_name, raw, fuzzy=fuzzy, is_player=lambda c: c in names)
        if npc and npc != self._last_npc:
            log(f"Cible PNJ ignorée : {npc} ← lu « {raw or ocr_name} »")
        self._last_npc = npc
        return bool(npc)

    def _resolve_name(self, reader, img, ocr_name: str) -> tuple[str, str]:
        """Lecture OCR → (nom à utiliser, comment il a été trouvé).

        1. dictionnaire des vrais noms (niveau collé, lettre mal lue…) ;
        2. sinon, s'il y avait du texte : relecture COMPLÈTE de la même image (sépare le
           niveau du nom), puis dictionnaire — une seule fois par texte lu différent ;
        3. sinon le nom lu tel quel (joueur absent de la mémoire → recherche réseau).
        """
        raw = reader.last_raw
        if not (ocr_name or raw.strip()):
            return "", ""
        # Cible PNJ (« Vakron », « 54Vakron », « 54 Vakron lis ») : rien à chercher ni afficher.
        if self._is_npc(ocr_name, raw):
            return "", ""
        hit = self.lookup.resolve(ocr_name, raw)
        if hit:
            return hit, ("" if hit == ocr_name else "corrigé")
        if self._is_npc(ocr_name, raw, fuzzy=True):          # « Vakrom »
            return "", ""
        memo = self._full_memo
        if memo and memo[0] == raw:
            return memo[1], memo[2]
        tag = reader.last_tag
        from .metrics import STATS
        t_full = time.monotonic()
        full = reader.read_full(img)
        STATS.add("full_reread", (time.monotonic() - t_full) * 1000)
        hit = self.lookup.resolve(full, reader.last_raw)
        result = (hit, "relecture complète") if hit else (ocr_name or full, "")
        reader.last_tag = reader.last_tag or tag
        self._full_memo = (raw, *result)
        return result

    def _show(self, name: str, info, fresh: bool = False, from_cache: bool = False):
        from .metrics import STATS
        if not info:
            self.status.emit(name, "notfound")
            return
        now = time.monotonic()
        start = self._first_seen.get(name, now)
        since_change = now - start
        gen = self.generation
        stage = None
        if (gen, "first") not in self._shown_gen:
            self._shown_gen.add((gen, "first"))
            stage = "first_cache" if from_cache else "first_net"
            STATS.add(stage, since_change * 1000)
        if fresh and (gen, "fresh") not in self._shown_gen:
            self._shown_gen.add((gen, "fresh"))
            stage = stage or "fresh"
            STATS.add("fresh", since_change * 1000)
        # Pour la mesure du rendu RÉEL (l'overlay la complète une fois les cadres peints).
        self.paint_meta = (name, gen, start, stage, now)
        self.status.emit(name, info)
        if name not in self._timed:
            self._timed.add(name)
            log(f"Affiché {name} : {since_change * 1000:.0f} ms après le 1er texte lu"
                + (" (mémoire)" if from_cache else ""))
            self.timing.emit(name, since_change)

    def _loop(self):
        from .capture import MssGrabber
        from .name_filter import is_plausible_player, server_from_tag
        from .ocr import NameReader
        log("Démarrage : chargement de l'OCR…")
        grabber = self.grabber or MssGrabber()   # mss doit être créé dans ce thread
        reader = NameReader(self.cfg.ocr_min_confidence)
        self.lookup = self._build_lookup()
        from .npc import NpcIndex
        self.npcs = NpcIndex.load()
        log(f"Prêt. Capture {grabber.name}, zone {self.cfg.capture}, "
            f"lecture toutes les {self.cfg.poll_interval_ms} ms, "
            f"dictionnaire de {len(self.lookup.names)} noms")
        last_name = None
        hint = None
        perf = PerfStats()
        errors, seen_errors = 0, set()

        while self.running:
            t0 = time.monotonic()
            try:
                self._check_me()
                report = perf.report(grabber, t0)
                if report:
                    log(report)
                if t0 - self._import_checked > 600:      # au lancement, puis toutes les 10 min
                    self._import_checked = t0
                    self.maybe_import()
                if not self.paused:
                    age_ms = None
                    try:
                        from .metrics import STATS
                        t_cap = time.monotonic()
                        img = grabber.grab(*self.cfg.capture)
                        STATS.add("capture", (time.monotonic() - t_cap) * 1000)
                        frame_age = getattr(grabber, "frame_age_ms", None)
                        if frame_age is not None:
                            age_ms = frame_age()               # âge réel (horodatage de l'image)
                        elif getattr(grabber, "last_frame_at", 0):
                            age_ms = (time.monotonic() - grabber.last_frame_at) * 1000
                        if age_ms is not None:
                            STATS.add("frame_age", age_ms)
                        t_ocr = time.monotonic()
                        skipped_before = getattr(reader, "skipped", 0)
                        ocr_name = reader.read(img)
                        ocr_ms = (time.monotonic() - t_ocr) * 1000
                        perf.add(ocr_ms, reader.last_mode == "lecture seule", age_ms)
                        if getattr(reader, "skipped", 0) == skipped_before:      # image vraiment relue
                            STATS.add("ocr", ocr_ms)
                        t_res = time.monotonic()
                        name, how = self._resolve_name(reader, img, ocr_name)
                        if name:
                            STATS.add("resolve", (time.monotonic() - t_res) * 1000)
                        # Abysse : « Nom [Isr] » → serveur connu d'avance, requête directe.
                        hint = server_from_tag(reader.last_tag, self.cfg.servers)
                    except Exception as e:  # zone hors écran, écran changé...
                        log(f"Erreur capture/OCR : {e}")
                        self.status.emit(f"Erreur capture : {e}", "error")
                        name, how = "", ""
                        time.sleep(1)
                    if name != last_name:
                        tag = f" [{reader.last_tag} → {hint or '?'}]" if reader.last_tag else ""
                        age = f", image âgée de {age_ms:.0f} ms" if age_ms is not None else ""
                        fix = f" ← lu « {reader.last_raw} », {how}" if how else ""
                        log(f"OCR : {name!r}{tag}{fix} ({(time.monotonic() - t0) * 1000:.0f} ms, "
                            f"{reader.last_mode or '—'}{age})")
                        if name and not last_name:
                            self._change_at = t0          # début d'une nouvelle cible (mesure)
                        if name:
                            self.generation += 1          # nouvelle cible : nouvelle génération
                        last_name = name
                        if name:
                            # Début de la mesure : l'image où le nom est apparu (son âge déduit).
                            self._first_seen[name] = t0 - (age_ms or 0) / 1000
                            self._timed.discard(name)
                            # Recherche ANTICIPÉE : dès la 1re lecture, sans attendre la confirmation.
                            if is_plausible_player(name):
                                self.lookup.prefetch(name, hint)

                    # Joueur connu (nom du dictionnaire) : validé dès la 1re lecture.
                    # Nom réel connu (dictionnaire, personnage observé, mémoire) : validé à la
                    # 1re lecture. Sinon, deux lectures concordantes.
                    known = bool(how) or (bool(name) and (
                        name in self.lookup.names or name in getattr(self.lookup, "observed", ())
                        or self.lookup.cached(name, hint) is not None))
                    target = self.tracker.feed(name, trusted=known)
                    if target:
                        from .metrics import STATS
                        STATS.add("confirm", (time.monotonic() - self._first_seen.get(target, t0)) * 1000)
                        log(f"Cible {target} validée en {(time.monotonic() - self._first_seen.get(target, t0)) * 1000:.0f} ms"
                            + (" (connue)" if known else ""))
                    if target is not None:
                        self._hint = hint if target else None
                    if target == "":
                        self.status.emit("", None)
                    elif target:
                        from .metrics import STATS
                        t_ram = time.monotonic()
                        amb = self.lookup.ambiguous(target, hint)
                        cached = None if amb else self.lookup.cached(target, hint)   # index RAM
                        STATS.add("ram", (time.monotonic() - t_ram) * 1000)
                        if amb:
                            from .style import Ambiguous
                            log(f"Cible {target} : homonymes ({', '.join(amb)}), serveur inconnu → "
                                "aucun score attribué")
                            self.status.emit(target, Ambiguous(amb))
                        elif cached:
                            self._show(target, cached, from_cache=True)
                        elif self.lookup.known_missing(target):
                            self.status.emit(target, "notfound")
                        else:
                            self.status.emit(target, "loading")
                        if not amb:
                            self.lookup.prefetch(target, hint)    # rafraîchissement APRÈS l'affichage
            except Exception:
                # Une erreur ne doit JAMAIS arrêter la lecture : on la note et on continue.
                errors += 1
                tb = traceback.format_exc()
                key = tb.strip().splitlines()[-1]
                if key not in seen_errors:          # trace complète la 1re fois seulement
                    seen_errors.add(key)
                    log("ERREUR dans la boucle (la lecture continue) :\n" + tb)
                self.status.emit(f"Erreur : {key[:120]}", "error")
                time.sleep(0.5)

            # Image inchangée (lecture sautée, quelques µs) : on revérifie plus souvent, pour voir
            # un changement de cible au plus tôt ; sinon cadence normale (OCR coûteux).
            skipped = getattr(reader, "skipped", 0)
            idle = skipped != self._skipped_seen
            self._skipped_seen = skipped
            interval = self.cfg.poll_idle_ms if idle else self.cfg.poll_interval_ms
            delay = interval / 1000 - (time.monotonic() - t0)
            if delay > 0:
                time.sleep(delay)


# --- cadres d'affichage ---------------------------------------------------------------

def overlay_flags(platform: str, edit: bool, above_fullscreen: bool = True) -> Qt.WindowType:
    """Options de fenêtre d'un cadre.

    En jeu (Linux) : type INFOBULLE. KDE range le jeu plein écran / sans bordure actif dans une
    couche au-dessus des fenêtres « toujours devant » ; les infobulles (et fenêtres hors
    gestionnaire) sont dans une couche encore au-dessus. Le type « Tool » ne suffit pas.
    Attention : Tool et ToolTip sont des TYPES exclusifs (ne jamais les combiner par |).
    En réglage : fenêtre « Tool » normale, pour pouvoir la saisir à la souris.
    """
    W = Qt.WindowType
    common = W.FramelessWindowHint | W.WindowStaysOnTopHint
    if edit:
        return common | W.Tool
    flags = common | W.WindowTransparentForInput            # clics traversants
    if platform.startswith("linux") and above_fullscreen:
        return flags | W.ToolTip | W.X11BypassWindowManagerHint
    return flags | W.Tool


class FrameWindow(QWidget):
    """Un cadre : transparent et traversé par les clics en jeu, éditable en mode réglage."""

    def __init__(self, overlay: "Overlay", role: str):
        super().__init__()
        self.overlay, self.role = overlay, role
        self.cfg = overlay.cfg
        self._drag: QPoint | None = None
        self._resizing = False
        self.setWindowTitle(f"Aion Target - {ROLE_LABELS[role]}")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

    def rect_cfg(self) -> list[int]:
        return self.cfg.capture if self.role == "zone" else self.cfg.frames()[self.role]

    def apply(self, edit: bool):
        self.setWindowFlags(overlay_flags(sys.platform, edit, self.cfg.overlay_above_fullscreen))
        x, y, w, h = self.rect_cfg()
        self.setGeometry(x, y, max(w, 20), max(h, 10))
        # La zone de lecture n'existe à l'écran QUE pendant le réglage : en jeu, aucune
        # fenêtre ne doit la recouvrir (sinon l'OCR lirait notre propre affichage).
        if self.role == "zone" and not edit:
            self.hide()
        else:
            self.show()
        self.update()

    def save_geometry(self):
        g = self.geometry()
        self.cfg.set_frame(self.role, [g.x(), g.y(), g.width(), g.height()])

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        r = self.rect()
        edit = self.overlay.edit_mode
        if edit:
            col = ROLE_COLORS[self.role]
            fill = QColor(col)
            fill.setAlpha(70 if self.role == "zone" else 35)
            p.fillRect(r, fill)
            p.setPen(QPen(col, 2, Qt.PenStyle.DashLine))
            p.drawRect(r.adjusted(1, 1, -1, -1))
            p.fillRect(r.right() - HANDLE, r.bottom() - HANDLE, HANDLE, HANDLE, col)
            if self.role == "zone":
                p.setPen(QColor(255, 255, 255))
                p.setFont(QFont("Sans Serif", 8))
                p.drawText(r.adjusted(4, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, ROLE_LABELS["zone"])
                return
        segments = self.overlay.contents().get(self.role, [])
        dot = self.overlay.dot() if self.role == "cp" else None
        draw_segments(p, r, segments, self.cfg.font_family, dot)

    # -- souris (mode réglage) --
    def mousePressEvent(self, e):
        if not self.overlay.edit_mode:
            return
        pos = e.position().toPoint()
        r = self.rect()
        self._resizing = pos.x() >= r.right() - HANDLE - 2 and pos.y() >= r.bottom() - HANDLE - 2
        self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if not self.overlay.edit_mode or self._drag is None:
            return
        if self._resizing:
            pos = e.position().toPoint()
            self.resize(max(30, pos.x()), max(10, pos.y()))
        else:
            self.move(e.globalPosition().toPoint() - self._drag)
        self.update()

    def mouseReleaseEvent(self, _):
        self._drag, self._resizing = None, False


def fit_font(family: str, segments, rect: QRect) -> QFont:
    """Police dont la taille suit la HAUTEUR du cadre, réduite si le texte déborde en largeur."""
    font = QFont(family)
    font.setWeight(QFont.Weight.Bold)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    px = max(6, int(rect.height() * 0.78))
    text = "".join(t for t, _ in segments) + "  ●"
    while px > 6:
        font.setPixelSize(px)
        if QFontMetrics(font).horizontalAdvance(text) <= rect.width() - 4:
            break
        px -= 1
    font.setPixelSize(px)
    return font


def draw_segments(p: QPainter, rect: QRect, segments, family: str, dot: str | None = None):
    """Texte vectoriel avec contour noir (lisible sur tout décor), + pastille éventuelle."""
    if not segments:
        return
    font = fit_font(family, segments, rect)
    fm = QFontMetrics(font)
    x = rect.x() + 2
    baseline = rect.y() + (rect.height() + fm.ascent() - fm.descent()) / 2
    outline = QPen(QColor(0, 0, 0, 230), max(2.0, font.pixelSize() / 6),
                   Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    for text, color in segments:
        path = QPainterPath()
        path.addText(x, baseline, font, text)
        p.setPen(outline)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawPath(path)
        x += fm.horizontalAdvance(text)
    if dot:
        d = max(6, int(font.pixelSize() * 0.55))
        cy = baseline - fm.ascent() / 2 + fm.descent() / 2
        p.setPen(QPen(QColor(0, 0, 0, 230), 1.5))
        p.setBrush(QColor(RED if dot == "stronger" else GREEN))
        p.drawEllipse(int(x + d * 0.6), int(cy - d / 2), d, d)


class Overlay(QObject):
    """Gère les 4 cadres. Interface utilisée par la fenêtre de contrôle."""
    edit_changed = pyqtSignal(bool)

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.edit_mode = False
        self._state: tuple[str, object] = ("", None)
        self.my_cp: int | None = None
        self._preview_until = 0.0
        self.windows = {role: FrameWindow(self, role) for role in ("zone", "gs", "cp", "details", "rank")}
        self._apply_all()

        # Filet de sécurité : rester au premier plan ; fin de l'aperçu de style.
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

    def _tick(self):
        if self._preview_until and time.monotonic() > self._preview_until:
            self._preview_until = 0.0
            self.repaint_all()
        if not self.edit_mode:
            for role, w in self.windows.items():
                if role != "zone" and w.isVisible():
                    w.raise_()

    def _apply_all(self):
        for w in self.windows.values():
            w.apply(self.edit_mode)

    def repaint_all(self):
        for w in self.windows.values():
            w.update()

    # -- contenu --
    def showing_sample(self) -> bool:
        return self.edit_mode or bool(self._preview_until)

    def contents(self):
        info = sample_info() if self.showing_sample() else self._state[1]
        return frame_contents(info, self.cfg, self.my_cp)

    def dot(self) -> str | None:
        from .shugo import CharacterInfo
        info = sample_info() if self.showing_sample() else self._state[1]
        if not isinstance(info, CharacterInfo):
            return None
        if self.showing_sample() and not self.my_cp:
            return "stronger"   # montre où la pastille apparaîtra
        return threat(info.combat_power, self.my_cp)

    def current_info(self):
        from .shugo import CharacterInfo
        info = self._state[1]
        return info if isinstance(info, CharacterInfo) else None

    def show_status(self, name: str, info):
        self._state = (name, info)
        self._preview_until = 0.0
        # Peinture IMMÉDIATE (pas de mise en file) : rien ne s'intercale entre le score et
        # l'écran (écritures d'historique, autres signaux…).
        for role, w in self.windows.items():
            if role != "zone" and w.isVisible():
                w.repaint()
        self._measure_paint(name)

    timing_source = None      # Worker : fournit paint_meta (mesure du rendu réel)
    _painted: set = set()

    def _measure_paint(self, name: str):
        meta = getattr(self.timing_source, "paint_meta", None)
        if not meta or meta[0] != name:
            return
        from .metrics import STATS
        _, gen, start, stage, emitted = meta
        now = time.monotonic()
        STATS.add("deliver", (now - emitted) * 1000)       # émis par la lecture → peint
        if stage and (gen, stage) not in self._painted:
            self._painted = {k for k in self._painted if k[0] >= gen - 50} | {(gen, stage)}
            STATS.add("paint_" + stage, (now - start) * 1000)

    def set_my_cp(self, cp):
        self.my_cp = cp
        self.repaint_all()

    # -- réglage --
    def set_edit(self, on: bool):
        if on != self.edit_mode:
            self.toggle_edit()

    def toggle_edit(self):
        if self.edit_mode:
            # Fin du réglage : enregistrer (la zone d'abord, pour le contrôle de chevauchement).
            for role in ("zone", "gs", "cp", "details", "rank"):
                self.windows[role].save_geometry()
            self.cfg.save()
        self.edit_mode = not self.edit_mode
        self._apply_all()
        self.edit_changed.emit(self.edit_mode)

    def restyle(self, preview: bool = True):
        if preview and not self._state[0]:
            self._preview_until = time.monotonic() + 4
        self.repaint_all()

    def test_display(self, seconds: float = 5):
        """Affiche l'exemple dans tous les cadres, même avec une cible (diagnostic)."""
        self._preview_until = time.monotonic() + seconds
        self._apply_all()
        for role, w in self.windows.items():
            if role != "zone":
                w.raise_()
        self.repaint_all()

    def zone_changed(self):
        self._apply_all()


# --- application ---------------------------------------------------------------------

def app_icon() -> QIcon:
    from .style import ASSETS
    png = ASSETS / "icon.png"
    if png.exists():
        return QIcon(str(png))
    pm = QPixmap(32, 32)
    pm.fill(QColor(201, 162, 39))
    return QIcon(pm)


def run(cfg: Config) -> int:
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Aion Target")
    app.setDesktopFileName("aion-target")   # associe la fenêtre au raccourci du menu
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(False)
    register_bundled_fonts()

    grabber = None
    from .capture import choose_backend, make_grabber
    if choose_backend(cfg.capture_backend) == "portal":
        log("Wayland : demande de partage d'écran à KDE (choisis l'écran du jeu)…")
        grabber = make_grabber(cfg)
        log(f"Écran partagé : origine {grabber.origin}, taille {grabber.logical_size}")

    overlay = Overlay(cfg)
    worker = Worker(cfg, grabber)
    overlay.timing_source = worker
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.status.connect(overlay.show_status)
    worker.my_cp.connect(overlay.set_my_cp)
    thread.start()

    events = None
    currency = None
    dps_tab = None
    hub = None
    msg = os.environ.get("AION_TARGET_UPDATE_MSG")
    if msg:
        log(msg)

    def quit_app():
        if events is not None:
            events.stop()
        if currency is not None:
            currency.worker.stop()
        if dps_tab is not None:
            dps_tab.shutdown()
        if hub is not None:
            hub.shutdown()
        worker.running = False
        thread.quit()
        thread.wait(2000)
        if grabber:
            grabber.close()
        app.quit()

    from .control import ControlWindow
    control = ControlWindow(cfg, overlay, worker, grabber, quit_app)
    control.show()

    tray = QSystemTrayIcon(app_icon())
    menu = QMenu()
    menu.addAction("Afficher la fenêtre", control.show_and_raise)
    menu.addAction("Quitter", quit_app)
    tray.setContextMenu(menu)
    tray.activated.connect(lambda reason: control.show_and_raise()
                           if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
    tray.setToolTip("Aion Target")
    tray.show()

    # Fichier de réglages endommagé, restauré ou corrigé : l'utilisateur est prévenu (journal,
    # fenêtre de contrôle, notification), jamais de remise à zéro silencieuse.
    def storage_notices():
        from . import storage
        notices = storage.pop_notices()
        for n in notices:
            log(n)
        if notices:
            control.show_storage_notice(" ".join(notices))
            try:
                tray.showMessage("Aion Target — réglages", " ".join(notices),
                                 QSystemTrayIcon.MessageIcon.Warning, 10_000)
            except Exception:
                pass
    QTimer.singleShot(3000, storage_notices)
    control._tray = tray

    # Module Événements : indépendant de la détection GS/CP et du jeu (tourne même jeu fermé).
    try:
        from .notify import build_notifier
        events = build_notifier(cfg.region, lambda: tray, log, config_path().parent)
        events.setParent(app)
        events.start()
        control.set_events(events)
    except Exception as e:
        log(f"Événements : module indisponible ({e})")

    # Monnaie calculée (onglet Farm) : thread de lecture séparé, 1 capture toutes les 2 s.
    try:
        from .currency import CurrencyWorker
        from .currency_ui import CurrencyOverlay
        cworker = CurrencyWorker(cfg, grabber, log)
        currency = CurrencyOverlay(cfg, cworker)
        if cfg.currency_enabled:
            cworker.start()
        control.set_currency(currency)
        raise_timer = QTimer(app)
        raise_timer.timeout.connect(currency.raise_value)
        raise_timer.start(1000)
    except Exception as e:
        log(f"Monnaie : module indisponible ({e})")

    # Historiques + module DPS (moteur réseau passif, désactivé par défaut).
    try:
        from .history import History
        from .dps_ui import DpsTab
        from .prefetch import Prefetcher
        history = History(config_path().with_name("history.db"), cfg.history_max_fights,
                          cfg.history_max_encounters)
        prefetcher = Prefetcher(lambda: worker.lookup, cfg.region, cfg.servers, log=log)
        from .metrics import REPORTERS
        REPORTERS.append(prefetcher.report)
        REPORTERS.append(lambda: worker.lookup and worker.lookup.report())

        def on_players(players):
            if cfg.dps_prefetch:
                prefetcher.observe(players)
            if cfg.dps_record_observed:
                from .prefetch import server_slug_from_name
                for p in players:
                    slug = server_slug_from_name(cfg.region, p.get("server"), cfg.servers)
                    history.record_encounter(p.get("name", ""), slug, source="reseau",
                                             confidence="exact" if slug else "ambigu")

        def on_party(members):
            from .prefetch import server_slug_from_name
            from .shugo import CharacterInfo
            for m in members:
                slug = server_slug_from_name(cfg.region, m.get("server"), cfg.servers)
                history.record_encounter(m.get("name", ""), slug, source="groupe",
                                         confidence="exact" if slug else "ambigu",
                                         gs=m.get("gs") or None, cp=m.get("cp") or None,
                                         data_at=time.time(), char_id=m.get("dbid"))
                # Le jeu envoie le GS/CP du groupe : donnée fraîche, serveur exact → mémoire Target.
                if slug and worker.lookup and m.get("cp"):
                    worker.lookup.cache.put(CharacterInfo(m["name"], slug, m.get("gs") or None,
                                                          m.get("cp"), m.get("level") or None))
            if cfg.dps_prefetch:          # après l'écriture : le groupe est « récent » localement
                prefetcher.observe(members, source="groupe")

        # UN seul moteur réseau, partagé (DPS, observation Target, familiers).
        from .engine_hub import EngineHub
        hub = EngineHub(cfg, log, parent=app)
        hub.players.connect(on_players)
        hub.party.connect(on_party)
        dps_tab = DpsTab(cfg, history, control._card, log, hub)
        control.set_dps(dps_tab)
        from .observe_ui import ObservationSection
        control.set_observation(ObservationSection(cfg, hub, prefetcher, control._card, log))
        app._aion_hub = hub
        from .engine_hub import STATE_LABELS

        def engine_chip():
            st = hub.state()
            level = {"active": "ok", "searching": "warn", "starting": "warn", "off": "idle"}.get(st, "err")
            modes = ", ".join(sorted(hub.modes)) or "aucun module"
            short = {"active": "actif", "searching": "recherche", "starting": "démarrage", "off": "arrêté",
                     "missing_engine": "absent", "missing_library": "Npcap", "no_permission": "permission",
                     "stopped": "arrêté", "error": "erreur"}.get(st, st)
            return level, short, f"Moteur réseau partagé : {STATE_LABELS.get(st, st)} ({modes})"
        control.register_status("engine", "Moteur", engine_chip)
        from . import diag

        def engine_section(_names):
            st = hub.last_status
            return [f"Modes demandés : {', '.join(sorted(hub.modes)) or 'aucun'} · état : {hub.state()}",
                    f"Port du jeu repéré : {'oui' if st.get('game_port') else 'non'} · fonctions du moteur : "
                    f"{', '.join(st.get('features') or []) or 'inconnues (ancien moteur ?)'}",
                    f"Dernière erreur : {st.get('error') or st.get('stopped') or '—'}"]
        diag.register("Moteur réseau partagé", engine_section)


        # Familiers : catalogue, progression, comptage des morts (moteur partagé).
        try:
            from .pets import Catalog, KillCounter, PetStore
            from .pets_ui import PetsTab
            store = PetStore(config_path().with_name("pets.json"))
            catalog = Catalog.load(store.data.get("custom"))
            counter = KillCounter(catalog, store, config_path().with_name("pets_kills.jsonl"))
            pets_tab = PetsTab(cfg, catalog, store, counter, hub, control._card, log)
            control.set_pets(pets_tab)
            app._aion_pets = pets_tab

            def pets_chip():
                n = len(store.tracked())
                if store.data.get("auto"):
                    ok = hub.state() == "active"
                    return ("ok" if ok else "warn"), f"{n} · auto", f"{n} familier(s) suivi(s), comptage auto"
                return ("ok" if n else "idle"), f"{n} suivi{'s' if n > 1 else ''}", "Familiers suivis sur l'overlay"
            control.register_status("pets", "Familiers", pets_chip)

            def pets_section(_names):
                c = counter.counts
                lines = [f"Comptage automatique : {'activé' if store.data.get('auto') else 'désactivé'} · "
                         f"morts comptées {c['counted']}, ignorées {c['ignored']}, incertaines "
                         f"{c['uncertain']}, doublons écartés {c['duplicate']}",
                         f"Paliers : {'/'.join(map(str, store.levels(catalog)))} ({store.data.get('edition')})"]
                for pid in store.tracked():
                    p, pet = store.get(pid), catalog.by_id.get(pid)
                    lines.append(f"Suivi : {pet.name if pet else pid} — âmes {p['souls']}, morts {p['kills']}")
                lines.append("Dernières décisions :")
                for e in list(counter.journal)[-30:]:
                    lines.append(f"  {time.strftime('%d/%m %H:%M:%S', time.localtime(e.get('t') or 0))} "
                                 f"{e.get('monstre')} → {e.get('decision')} ({e.get('raison')})")
                return lines
            diag.register("Familiers", pets_section)
            pets_timer = QTimer(app)
            pets_timer.timeout.connect(lambda: pets_tab.overlay.isVisible() and pets_tab.overlay.raise_())
            pets_timer.start(1500)
        except Exception as e:
            log(f"Familiers : module indisponible ({e})")
        pf_timer = QTimer(app)
        pf_timer.timeout.connect(prefetcher.tick)
        pf_timer.start(500)

        def on_target(name, info):
            from .shugo import CharacterInfo
            from .style import Ambiguous
            if isinstance(info, CharacterInfo):
                _, age = worker.lookup.cache.get(name, info.server) if worker.lookup else (None, None)
                history.record_encounter(name, info.server, source="ocr",
                                         confidence="exact" if worker._hint else "unique",
                                         gs=info.gear_score, cp=info.combat_power,
                                         data_at=time.time() - age if age is not None else None)
            elif isinstance(info, Ambiguous):
                history.record_encounter(name, None, source="ocr", confidence="ambigu")
        worker.status.connect(on_target)
    except Exception as e:
        log(f"DPS / historique : module indisponible ({e})")

    _install_hotkeys(cfg, overlay, quit_app)

    # Mise à jour à l'essai : confirmée après quelques secondes de fonctionnement normal, ou à une
    # fermeture normale. Sans confirmation (plantage), le lancement suivant revient en arrière.
    def confirm_update():
        try:
            from . import updater
            res = updater.confirm_update()
            if res:
                log(res.message)
                if control is not None and hasattr(control, "update_label"):
                    control.update_label.setText(res.message)
        except Exception as e:
            log(f"Mise à jour : confirmation impossible ({e})")
    if os.environ.get("AION_TARGET_UPDATE_STATUS") in ("updated", "trial"):
        QTimer.singleShot(_confirm_delay_ms(), confirm_update)
        app.aboutToQuit.connect(confirm_update)
    return app.exec()


def _confirm_delay_ms() -> int:
    from .updater import CONFIRM_AFTER_MS
    return CONFIRM_AFTER_MS


class _HotkeyBridge(QObject):
    edit = pyqtSignal()
    quit = pyqtSignal()


def _install_hotkeys(cfg: Config, overlay: Overlay, quit_app):
    """Raccourcis globaux via pynput (Windows ; souvent inactifs sous Wayland). Optionnel."""
    if not cfg.global_hotkeys:
        return
    try:
        from pynput import keyboard
    except ImportError:
        return

    def to_pynput(combo: str) -> str:
        parts = [s.strip().lower() for s in combo.split("+")]
        return "+".join(f"<{s}>" if len(s) > 1 else s for s in parts)

    bridge = _HotkeyBridge()
    bridge.edit.connect(overlay.toggle_edit)
    bridge.quit.connect(quit_app)
    overlay._hotkey_bridge = bridge

    try:
        hk = keyboard.GlobalHotKeys({
            to_pynput(cfg.hotkey_edit): bridge.edit.emit,
            to_pynput(cfg.hotkey_quit): bridge.quit.emit,
        })
        hk.daemon = True
        hk.start()
    except Exception as e:
        log(f"Raccourcis globaux indisponibles ({e}) : utilise la fenêtre de contrôle.")
