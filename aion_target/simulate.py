"""Banc d'essai reproductible du chemin « changement de cible → cadres peints ».

Utilise les VRAIS composants (Worker, TargetTracker, PlayerLookup, PlayerCache/index RAM,
dictionnaire, filtre PNJ, Overlay Qt et sa peinture) ; seuls la capture d'écran, l'OCR et le
réseau sont simulés avec des délais réglables. Les chiffres obtenus mesurent donc le CODE de
l'application sur cette machine, pas le jeu : capture réelle, OCR réel et latence de shugo.gg
dépendent du PC et de la connexion (voir le rapport de vitesse en jeu).

Lancement : python -m aion_target.simulate   (ou tools/bench_target.py)
"""
from __future__ import annotations

import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


@dataclass
class Scene:
    name: str            # ce que l'OCR « lit » (nom seul) ; "" = pas de cible
    raw: str = ""        # ligne brute
    tag: str | None = None


@dataclass
class World:
    """Ce que « sait » le réseau simulé : nom → [(profil, horodatage)]."""
    remote: dict = field(default_factory=dict)
    quick_ms: float = 150.0
    full_ms: float = 900.0
    fail: bool = False
    calls: list = field(default_factory=list)


class FakeClient:
    def __init__(self, world: World):
        from concurrent.futures import ThreadPoolExecutor
        self.world = world
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="sim-net")

    def rate_limited(self):
        return False

    def quick_search_all(self, name):
        from .shugo import NetworkError
        self.world.calls.append(("quick", name, time.monotonic()))
        time.sleep(self.world.quick_ms / 1000)
        if self.world.fail:
            raise NetworkError("simulé")
        return list(self.world.remote.get(name.casefold(), []))

    def lookup(self, name, hint=None):
        from .shugo import AmbiguousName, NetworkError
        self.world.calls.append(("full", name, time.monotonic()))
        time.sleep(self.world.full_ms / 1000)
        if self.world.fail:
            raise NetworkError("simulé")
        found = [i for i, _ in self.world.remote.get(name.casefold(), [])]
        if hint:
            found = [i for i in found if i.server == hint]
        if len(found) > 1:
            raise AmbiguousName(name, sorted(i.server for i in found))
        return found[0] if found else None


class FakeGrabber:
    name = "simulation"

    def __init__(self):
        self.scene = Scene("")
        self.images: dict[str, np.ndarray] = {}
        self.changed_at = time.monotonic()

    def show(self, scene: Scene):
        self.scene, self.changed_at = scene, time.monotonic()

    def grab(self, *a):
        key = self.scene.raw or self.scene.name
        img = self.images.get(key)
        if img is None:                               # une image distincte par cible
            img = np.full((35, 300, 3), (len(self.images) * 37) % 256, np.uint8)
            img[0, 0] = len(self.images) % 256
            self.images[key] = img
        return img

    def frame_age_ms(self):
        return 0.0


def make_reader(grabber: FakeGrabber, ocr_ms: float, full_ms: float):
    from .ocr import frames_similar

    class Reader:
        last_tag, last_mode = None, "lecture seule"

        def __init__(self, *a):
            self.last_raw, self._last_img, self._last = "", None, ""
            self.skipped = 0

        def read(self, img):
            if frames_similar(img, self._last_img):
                self.skipped += 1
                return self._last
            time.sleep(ocr_ms / 1000)
            sc = grabber.scene
            self._last_img, self._last = img, sc.name
            self.last_raw, self.last_tag, self.last_mode = sc.raw or sc.name, sc.tag, "lecture seule"
            return sc.name

        def read_full(self, img):
            time.sleep(full_ms / 1000)
            self.last_mode = "relecture complète"
            return grabber.scene.name
    return Reader


@dataclass
class Result:
    label: str
    expected: str | None          # nom attendu à l'écran (None : aucun score attendu)
    paint_ms: float | None        # changement → 1re peinture du bon score
    first_kind: str = ""
    wrong: list = field(default_factory=list)   # affichages erronés pendant la scène


def run_scenario(scenes: list[tuple[str, Scene, str | None, float]], cache_rows=(), world: World | None = None,
                 ocr_ms: float = 20.0, full_ms: float = 45.0, cfg=None, observed=()) -> list[Result]:
    """scenes : (étiquette, scène, nom attendu ou None, durée en s). Renvoie une mesure par scène."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import QThread
    from PyQt6.QtWidgets import QApplication

    from . import ocr
    from .cache import PlayerCache
    from .config import Config
    from .lookup import PlayerLookup
    from .overlay import Overlay, Worker
    from .shugo import CharacterInfo

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="aion-sim-"))
    cfg = cfg or Config(capture=[860, 40, 240, 32])
    cfg.save = lambda *a: None
    cfg.my_character = ""
    world = world or World()
    grab = FakeGrabber()
    orig_reader = ocr.NameReader
    ocr.NameReader = make_reader(grab, ocr_ms, full_ms)
    overlay = Overlay(cfg)
    w = Worker(cfg, grab)
    overlay.timing_source = w
    w.maybe_import = lambda *a, **k: None
    cache = PlayerCache(tmp / "players.db")
    for info, age_s in cache_rows:
        cache.put_many([(info, time.time() - age_s)])

    def build():
        lk = PlayerLookup(FakeClient(world), cache, on_result=w._on_result)
        for n in observed:
            lk.observe_name(n)
        return lk
    w._build_lookup = build

    # Instant RÉEL de peinture : le cadre CP exécute son paintEvent (quel que soit le moment où
    # Qt le déclenche : tout de suite ou plus tard dans la boucle d'événements).
    from . import overlay as ov
    paints: list[float] = []
    orig_paint = ov.FrameWindow.paintEvent

    def paint_event(self, e):
        orig_paint(self, e)
        if self.role == "cp":
            paints.append(time.monotonic())
    ov.FrameWindow.paintEvent = paint_event

    calls: list[tuple[float, str, object, str]] = []
    orig_show = overlay.show_status

    def show(name, info):
        t = time.monotonic()
        orig_show(name, info)
        meta = getattr(w, "paint_meta", None)
        stage = meta[3] if meta and meta[0] == name and meta[3] else ""
        calls.append((t, name, info, stage))

    def painted_since(i0):
        out = []
        for t, name, info, stage in calls[i0:]:
            tp = next((p for p in paints if p >= t), None)
            if tp is not None:
                out.append((tp, name, info, stage))
        return out
    w.status.connect(show)

    th = QThread()
    w.moveToThread(th)
    th.started.connect(w.run)
    th.start()
    try:
        t_end = time.monotonic() + 10
        while w.lookup is None and time.monotonic() < t_end:   # chargement (index, dictionnaire)
            app.processEvents()
            time.sleep(0.01)
        results = []
        for label, scene, expected, dur in scenes:
            grab.show(scene)
            t0 = grab.changed_at
            start = len(calls)
            end = t0 + dur
            while time.monotonic() < end:
                app.processEvents()
                time.sleep(0.002)
            res = Result(label, expected, None)
            for t, name, info, stage in painted_since(start):
                if not isinstance(info, CharacterInfo):
                    continue
                dt = (t - t0) * 1000
                if expected and name == expected:
                    if res.paint_ms is None:
                        res.paint_ms = dt
                        res.first_kind = "mémoire" if stage == "first_cache" else (
                            "réseau" if stage == "first_net" else "déjà affiché")
                # Erreur = score d'un AUTRE personnage peint une fois la nouvelle cible connue :
                # après le bon score, ou plus de 250 ms après le changement quand aucun score
                # n'est attendu (avant, c'est encore l'ancienne cible à l'écran : pas une erreur).
                elif (expected and res.paint_ms is not None) or (expected is None and dt > 250):
                    res.wrong.append((round(dt), name, info.server))
            results.append(res)
        return results
    finally:
        w.running = False
        th.quit()
        th.wait(3000)
        ocr.NameReader = orig_reader
        ov.FrameWindow.paintEvent = orig_paint
        for win in overlay.windows.values():
            win.close()


def default_scenario(repeats: int = 30):
    """Scénario type « Abysse » : allers-retours entre joueurs connus, inconnus, homonymes, PNJ."""
    from .shugo import CharacterInfo
    known = [CharacterInfo(f"Joueur{i}", "eu-siel", 3000 + i, 150000 + i, 45, "Gladiator") for i in range(40)]
    rows = [(k, 3 * 86400) for k in known]                       # connus, score vieux de 3 jours
    homonyms = [(CharacterInfo("Krix", "eu-siel", 1, 100), 3600), (CharacterInfo("Krix", "eu-zikel", 2, 200), 3600)]
    world = World()
    world.remote["inconnu"] = [(CharacterInfo("Inconnu", "eu-siel", 2500, 120000, 45), time.time())]
    scenes = []
    for r in range(repeats):
        a, b = known[r % 40].name, known[(r + 7) % 40].name
        scenes += [(f"A {a}", Scene(a, f"45 {a}"), a, 0.35),
                   (f"B {b}", Scene(b, f"45 {b}"), b, 0.35),
                   (f"A {a} (retour)", Scene(a, f"45 {a}"), a, 0.35)]
    scenes += [("vide", Scene(""), None, 0.4),
               ("homonymes", Scene("Krix", "45 Krix"), None, 0.6),
               ("PNJ", Scene("Vakron", "54 Vakron"), None, 0.5),
               ("inconnu (réseau)", Scene("Inconnu", "45 Inconnu"), "Inconnu", 1.6)]
    return scenes, rows + homonyms, world


def summarize(results: list[Result]) -> list[str]:
    from .metrics import STATS, percentile
    lines = []
    groups: dict[str, list[float]] = {}
    for r in results:
        if r.paint_ms is not None:
            groups.setdefault(r.first_kind or "?", []).append(r.paint_ms)
    for g, vals in groups.items():
        v = sorted(vals)
        lines.append(f"{g:<16} n={len(v):<3} changement → cadres peints : p50 {percentile(v, 50):6.0f} ms · "
                     f"p95 {percentile(v, 95):6.0f} ms · p99 {percentile(v, 99):6.0f} ms · max {v[-1]:6.0f} ms")
    missing = [r.label for r in results if r.expected and r.paint_ms is None]
    wrong = [(r.label, r.wrong) for r in results if r.wrong]
    lines.append(f"scènes sans le bon score : {missing or 'aucune'}")
    lines.append(f"affichages erronés : {wrong or 'aucun'}")
    lines += STATS.report()
    return lines


if __name__ == "__main__":
    import sys
    reps = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    scenes, rows, world = default_scenario(reps)
    res = run_scenario(scenes, rows, world)
    print("\n".join(summarize(res)))
