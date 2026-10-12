"""OCR du cadre de cible (RapidOCR : latin, CJK, cyrillique), réglé pour la vitesse."""
from __future__ import annotations

import numpy as np

from .capture import MssGrabber as ScreenGrabber  # noqa: F401  (compatibilité)

# Réglages mesurés sur une vraie capture du cadre de cible :
#   défaut (détection redimensionnée à ≥ 736 px) : ~1 750 ms
#   détection plafonnée à 480 px, sans classifieur : ~140 ms, même résultat.
ENGINE_PARAMS = {
    "Global.use_cls": False,          # le texte du jeu n'est jamais retourné
    "Det.limit_type": "max",
    "Det.limit_side_len": 480,
    # Le jeu occupe déjà le processeur : 2 threads suffisent pour un modèle de 20 ms, et
    # évitent que des threads en attente active ralentissent le jeu ET la lecture.
    "EngineConfig.onnxruntime.intra_op_num_threads": 2,
    "EngineConfig.onnxruntime.inter_op_num_threads": 1,
}


def _no_spinning():
    """onnxruntime « tourne à vide » entre deux calculs pour gagner quelques µs : sous la
    charge d'un jeu, c'est contre-productif. Désactivé (réglage de session officiel)."""
    try:
        from rapidocr.inference_engine.onnxruntime.main import OrtInferSession
    except Exception:
        return
    orig = OrtInferSession._init_sess_opts
    if getattr(orig, "_aion_patched", False):
        return

    def patched(cfg):
        opt = orig(cfg)
        for key in ("session.intra_op.allow_spinning", "session.inter_op.allow_spinning"):
            try:
                opt.add_session_config_entry(key, "0")
            except Exception:
                pass
        return opt
    patched._aion_patched = True
    OrtInferSession._init_sess_opts = staticmethod(patched)


# Au-delà de cette hauteur, la zone peut contenir plusieurs lignes (légion, titre, nom) :
# il faut alors la détection pour trouver la ligne du nom.
SINGLE_LINE_MAX_H = 48


def upscale_factor(img: np.ndarray) -> int:
    """Une zone très basse (une seule ligne) est lue plus fiablement agrandie 2x."""
    # Mesuré : à 2x, une ligne de 35 px fait perdre l'espace « 45 Corgoth » ; on n'agrandit
    # donc que les zones vraiment minuscules.
    return 2 if img.shape[0] < 24 else 1


def frames_similar(a: np.ndarray | None, b: np.ndarray | None, tol: float = 3.0) -> bool:
    """Vrai si deux captures sont quasi identiques (écart moyen < tol sur 255).

    Permet de sauter l'OCR tant que le cadre de cible ne change pas.
    Comparaison sur une version sous-échantillonnée : quelques microsecondes.
    """
    if a is None or b is None or a.shape != b.shape:
        return False
    sa = a[::4, ::4].astype(np.int16)
    sb = b[::4, ::4].astype(np.int16)
    return float(np.abs(sa - sb).mean()) < tol


class NameReader:
    def __init__(self, min_confidence: float = 0.6):
        from rapidocr import RapidOCR
        _no_spinning()
        self._engine = RapidOCR(params=ENGINE_PARAMS)
        self.min_confidence = min_confidence
        self._last_img: np.ndarray | None = None
        self._last_name = ""
        self.skipped = 0  # nb d'images sautées (identiques à la précédente)
        self.last_tag: str | None = None  # balise serveur Abysse (« Isr »), si lue
        self.last_mode = ""                # « lecture seule » ou « détection » (journal de perf)
        self.last_raw = ""                 # texte brut lu (diagnostic)
        self._fallback_memo: tuple[str, str, str | None] | None = None

    def read(self, img: np.ndarray) -> str:
        """Renvoie le nom de la cible (texte qui suit le niveau), ou ''."""
        if frames_similar(img, self._last_img):
            self.skipped += 1
            return self._last_name
        self._last_img = img
        self._last_name = self._ocr(img)
        return self._last_name

    def _ocr(self, img: np.ndarray) -> str:
        from .name_filter import parse_target
        k = upscale_factor(img)
        if k > 1:
            img = img.repeat(k, axis=0).repeat(k, axis=1)
        h, w = img.shape[:2]
        if h <= SINGLE_LINE_MAX_H * k:
            # Zone d'UNE ligne (« 45 Nom  2 m ») : inutile de chercher où est le texte,
            # on le lit directement. Mesuré : 19 ms au lieu de 72 ms, même résultat.
            # Pas de texte (aucune cible) → rien : pas de repli coûteux sur la détection.
            r = self._engine(img, use_det=False, use_cls=False)
            txts, scores = list(getattr(r, "txts", None) or ()), list(getattr(r, "scores", None) or ())
            self.last_mode = "lecture seule"
            self.last_tag = None
            self.last_raw = txts[0] if txts else ""
            if not (txts and txts[0].strip() and scores[0] >= self.min_confidence):
                return ""
            full = [[0, 0], [w / k, 0], [w / k, h / k], [0, h / k]]
            name, self.last_tag = parse_target([(txts[0], float(scores[0]), full)])
            if name:
                return name
            # Filet de sécurité : du texte mais pas de nom (icône collée, artefact, PNJ…).
            # La détection complète tranche — une seule fois par texte lu différent.
            if self._fallback_memo and self._fallback_memo[0] == self.last_raw:
                _, name, self.last_tag = self._fallback_memo
                self.last_mode = "lecture seule (mémo)"
                return name
            raw = self.last_raw
            name = self._detect(img, k)
            self._fallback_memo = (raw, name, self.last_tag)
            self.last_raw = raw
            self.last_mode = "lecture seule → détection"
            return name
        return self._detect(img, k)

    def read_full(self, img: np.ndarray) -> str:
        """Relecture COMPLÈTE de la même image (détection : sépare « 45 » du nom).
        Utilisée quand la lecture rapide donne un nom inconnu, au lieu d'attendre que
        l'image change (elle peut rester identique longtemps si personne ne bouge)."""
        k = upscale_factor(img)
        if k > 1:
            img = img.repeat(k, axis=0).repeat(k, axis=1)
        name = self._detect(img, k)
        self.last_mode = "relecture complète"
        return name

    def _detect(self, img: np.ndarray, k: int) -> str:
        from .name_filter import parse_target
        self.last_mode = "détection"
        result = self._engine(img, use_cls=False)
        self.last_raw = " | ".join(getattr(result, "txts", None) or ())
        txts = getattr(result, "txts", None) or ()
        scores = getattr(result, "scores", None) or ()
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            boxes = [[[0, 0]] * 4] * len(txts)
        lines = [(t, float(s), (np.asarray(b) / k).tolist())
                 for t, s, b in zip(txts, scores, boxes) if s >= self.min_confidence]
        name, self.last_tag = parse_target(lines)
        return name
