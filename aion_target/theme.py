"""Thème « AION 2 — Aether Analytics » de la FENÊTRE DE CONTRÔLE uniquement.

Deux palettes (clair par défaut, sombre), mêmes rôles, choisies par l'utilisateur (Clair / Sombre /
Comme le système, mémorisé dans la config). Les overlays affichés par-dessus le jeu (cadres GS/CP,
DPS, monnaie, familiers, carré de test, sélecteur de zone) ne l'utilisent PAS : ce sont des fenêtres
de premier niveau dessinées à la main (QPainter) avec leurs propres couleurs, inchangées.

Toutes les couleurs, polices et espacements de l'interface viennent d'ici.
"""
from __future__ import annotations

from pathlib import Path

# --- palettes ------------------------------------------------------------------------------
# PRIMARY : bleu principal (actions, sélection) · AETHER : cyan (indicateurs, progression).
# Texte ≥ 4,5:1 sur les cartes dans les deux modes (vérifié par tests/test_theme.py).
PALETTES = {
    "light": {
        "BG": "#EAF0F6", "SURFACE": "#FFFFFF", "SURFACE_ALT": "#F4F7FB", "HOVER": "#EEF3FA",
        "BORDER": "#D5E1EC", "BORDER_STRONG": "#BCCCDD", "SCROLL": "#C5D3E1", "SCROLL_HOVER": "#A8BBCF",
        "TITLE": "#1B3655", "TEXT": "#1B3655", "MUTED": "#607891", "DISABLED": "#9DB0C3",
        "MUTED_BG": "#556C84",          # texte secondaire posé directement sur le fond de page (AA)
        "PRIMARY": "#285EB2", "PRIMARY_HOVER": "#1F4C93", "AETHER": "#31AFC7", "AETHER_TEXT": "#167385",
        "ACCENT": "#285EB2", "ACCENT_DARK": "#285EB2", "ACCENT_SOFT": "#E4ECF8", "ON_ACCENT": "#FFFFFF",
        "GOLD": "#D2A657", "GOLD_TEXT": "#7E5D1C", "GOLD_SOFT": "#FBF4E6",
        "OK": "#19835A", "WARN": "#A35C00", "ERR": "#B42F2A", "DANGER_BORDER": "#EBC6C2",
        "DANGER_HOVER": "#FCF1F0", "TOOLTIP_BG": "#1B3655", "TOOLTIP_FG": "#FFFFFF",
        "NAV_ACTIVE": "#E4ECF8", "SHADOW": "#1B365514",
    },
    "dark": {
        "BG": "#0F1828", "SURFACE": "#16233A", "SURFACE_ALT": "#1B2B45", "HOVER": "#203351",
        "BORDER": "#283B57", "BORDER_STRONG": "#36507A", "SCROLL": "#34496A", "SCROLL_HOVER": "#4A6390",
        "TITLE": "#EAF1FB", "TEXT": "#CED9E8", "MUTED": "#98ACC4", "DISABLED": "#5C7090",
        "MUTED_BG": "#A3B6CC",
        "PRIMARY": "#6E9EEB", "PRIMARY_HOVER": "#8AB2F0", "AETHER": "#4CC8DF", "AETHER_TEXT": "#62D0E4",
        "ACCENT": "#6E9EEB", "ACCENT_DARK": "#8DB5F2", "ACCENT_SOFT": "#1F3558", "ON_ACCENT": "#0B1424",
        "GOLD": "#D3AC5E", "GOLD_TEXT": "#E2BD72", "GOLD_SOFT": "#2A2418",
        "OK": "#52C995", "WARN": "#E8B04E", "ERR": "#F27D72", "DANGER_BORDER": "#6B3534",
        "DANGER_HOVER": "#2E1D22", "TOOLTIP_BG": "#EAF1FB", "TOOLTIP_FG": "#0F1828",
        "NAV_ACTIVE": "#1F3558", "SHADOW": "#00000040",
    },
}
ON_LIGHT = "#13283F"     # texte posé sur une couleur claire choisie par l'utilisateur (pastilles)
MODE = "light"


def _apply(mode: str):
    """Rend la palette `mode` active (variables du module lues au moment de dessiner)."""
    global MODE, LEVEL_COLORS, IDLE
    MODE = mode if mode in PALETTES else "light"
    globals().update(PALETTES[MODE])
    IDLE = PALETTES[MODE]["MUTED"]
    LEVEL_COLORS = {"ok": OK, "warn": WARN, "err": ERR, "idle": IDLE, "info": ACCENT_DARK}  # noqa: F821


def system_mode() -> str:
    """Clair ou sombre selon le réglage du système (Qt ≥ 6.5), clair si inconnu."""
    try:
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QGuiApplication
        app = QGuiApplication.instance()
        if app and app.styleHints().colorScheme() == Qt.ColorScheme.Dark:
            return "dark"
    except Exception:
        pass
    return "light"


def resolve(choice: str) -> str:
    return system_mode() if choice == "system" else (choice if choice in PALETTES else "light")


def set_mode(choice: str) -> str:
    """Active « light », « dark » ou « system ». Renvoie le mode réellement appliqué."""
    _apply(resolve(choice))
    return MODE


_apply("light")

# Polices (Inter, JetBrains Mono et Cinzel sont fournies dans assets/, licence OFL) ; repli
# automatique sur une police du système si le chargement échoue.
UI_FONTS = ["Inter", "Segoe UI Variable Text", "Segoe UI", "Noto Sans", "Cantarell", "Ubuntu",
            "DejaVu Sans"]
MONO_FONTS = ["JetBrains Mono", "Cascadia Mono", "Consolas", "Noto Sans Mono", "DejaVu Sans Mono",
              "Liberation Mono"]
BRAND_FONT = "Cinzel"
ANIM_MS = 150            # micro-interactions : 120-180 ms, jamais en boucle


def dot(level: str, size: str = "") -> str:
    """Pastille d'état HTML (« ok », « warn », « err », « idle », « info »)."""
    style = f"color:{LEVEL_COLORS.get(level, IDLE)}" + (f";font-size:{size}" if size else "")
    return f"<span style='{style}'>●</span>"


def colored(text: str, level: str) -> str:
    return f"<span style='color:{LEVEL_COLORS.get(level, IDLE)}'>{text}</span>"


def ui_font(point_size: float | None = None):
    from PyQt6.QtGui import QFont, QFontDatabase
    families = set(QFontDatabase.families())
    fam = next((f for f in UI_FONTS if f in families), None)
    font = QFont(fam) if fam else QFont()
    if point_size:
        font.setPointSizeF(point_size)
    return font


def mono_font(point_size: float | None = None, bold: bool = True):
    """Police des statistiques chiffrées (monospace, chiffres alignés)."""
    from PyQt6.QtGui import QFont, QFontDatabase
    families = set(QFontDatabase.families())
    fam = next((f for f in MONO_FONTS if f in families), None)
    font = QFont(fam) if fam else QFont()
    if not fam:
        font.setStyleHint(QFont.StyleHint.Monospace)
    if point_size:
        font.setPointSizeF(point_size)
    font.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    return tabular(font)


def tabular(font):
    """Chiffres tabulaires (largeur fixe) : les valeurs ne « dansent » pas en se mettant à jour."""
    from PyQt6.QtGui import QFont
    f = QFont(font)
    if hasattr(QFont, "Tag") and hasattr(f, "setFeature"):         # Qt ≥ 6.7
        try:
            f.setFeature(QFont.Tag("tnum"), 1)
            f.setFeature(QFont.Tag("lnum"), 1)
        except Exception:
            pass
    return f


# --- petites images générées (cases cochées, flèches) : PNG, aucun module SVG requis -----------

def _icons_dir() -> Path:
    from .config import config_path
    d = config_path().with_name("theme-icons")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _make_icons(d: Path) -> dict[str, str]:
    d = d / MODE
    d.mkdir(parents=True, exist_ok=True)
    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPolygonF
    out = {}

    def save(name, size, draw):
        path = d / f"{name}.png"
        img = QImage(size * 2, size * 2, QImage.Format.Format_ARGB32)    # 2x : net en haute densité
        img.fill(Qt.GlobalColor.transparent)
        img.setDevicePixelRatio(2.0)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        draw(p)
        p.end()
        img.save(str(path))
        out[name] = path.as_posix()

    def check(p):
        pen = QPen(QColor(ON_ACCENT), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                   Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawPolyline(QPolygonF([QPointF(3.5, 8.5), QPointF(6.5, 11.5), QPointF(12.5, 4.5)]))

    def arrow(color):
        def draw(p):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(color))
            p.drawPolygon(QPolygonF([QPointF(3, 5), QPointF(11, 5), QPointF(7, 10)]))
        return draw

    def arrow_up(color):
        def draw(p):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(color))
            p.drawPolygon(QPolygonF([QPointF(3, 9), QPointF(11, 9), QPointF(7, 4)]))
        return draw
    save("check", 16, check)
    save("arrow", 14, arrow(MUTED))
    save("arrow_up", 14, arrow_up(MUTED))
    return out


def icons() -> dict[str, str]:
    try:
        return _make_icons(_icons_dir())
    except Exception:
        return {}


# --- feuille de style ----------------------------------------------------------------------

def stylesheet(ic: dict[str, str] | None = None) -> str:
    ic = ic or {}
    check = f"image: url({ic['check']});" if ic.get("check") else ""
    arrow = f"image: url({ic['arrow']}); width: 10px; height: 10px;" if ic.get("arrow") else ""
    up = f"image: url({ic['arrow_up']}); width: 9px; height: 9px;" if ic.get("arrow_up") else ""
    down = f"image: url({ic['arrow']}); width: 9px; height: 9px;" if ic.get("arrow") else ""
    return f"""
/* ================= Aether Analytics — fenêtre de contrôle ================= */
QWidget#root, QWidget#body, QWidget#events, QWidget#page {{ background: {BG}; color: {TEXT}; }}
QScrollArea {{ border: none; background: {BG}; }}
QScrollArea > QWidget > QWidget {{ background: {BG}; }}
QLabel {{ color: {TEXT}; background: transparent; }}
QLabel#title {{ color: {TITLE}; }}
QLabel#brandSub {{ color: {AETHER_TEXT}; font-size: 8pt; font-weight: 600; letter-spacing: 2.5px; }}
QLabel#status {{ color: {MUTED}; }}
QWidget#events QLabel#status {{ color: {MUTED_BG}; }}
QLabel#next {{ color: {GOLD_TEXT}; font-weight: 600; }}
QLabel#version {{ color: {MUTED}; font-size: 8pt; }}
QToolTip {{ background: {TOOLTIP_BG}; color: {TOOLTIP_FG}; border: none; padding: 6px 8px; }}

/* ---- en-tête ---- */
QFrame#header {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#accentBar {{ background: {AETHER}; border: none; border-radius: 2px; }}

/* ---- barre latérale ---- */
QFrame#sidebar {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QPushButton#nav {{
    background: transparent; color: {MUTED}; border: none; border-left: 3px solid transparent;
    border-radius: 8px; padding: 9px 10px; text-align: left; font-weight: 600;
}}
QPushButton#nav:hover {{ background: {HOVER}; color: {TITLE}; }}
QPushButton#nav:checked {{ background: {NAV_ACTIVE}; color: {PRIMARY}; border-left: 3px solid {AETHER}; }}
QLabel#navText {{ color: inherit; background: transparent; font-weight: 600; }}
QLabel#navSection {{ color: {MUTED}; font-size: 7.5pt; font-weight: 600; letter-spacing: 1.5px;
                     padding: 6px 10px 2px 12px; }}

/* ---- en-tête de page ---- */
QLabel#pageTitle {{ color: {TITLE}; font-size: 15pt; font-weight: 600; }}
QLabel#pageDesc {{ color: {MUTED_BG}; font-size: 9pt; }}

/* ---- cartes de section ---- */
QFrame#card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#card:hover {{ border-color: {BORDER_STRONG}; }}
QLabel#cardTitle {{ color: {TITLE}; font-size: 8.5pt; font-weight: 600; letter-spacing: 1.2px; }}
QLabel#cardDesc {{ color: {MUTED}; font-size: 8.5pt; }}
QToolButton#chevron {{
    background: transparent; border: none; color: {MUTED}; text-align: left; padding: 0;
    font-size: 8.5pt; font-weight: 600; letter-spacing: 1.2px;
}}
QToolButton#chevron:hover {{ color: {PRIMARY}; }}
QFrame#cardRule {{ background: {BORDER}; border: none; max-height: 1px; min-height: 1px; }}

/* ---- cartes de statistiques ---- */
QFrame#tile {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#tile:hover {{ border-color: {BORDER_STRONG}; }}
QFrame#tile[important="true"] {{ border-top: 3px solid {AETHER}; }}
QLabel#tileCaption {{ color: {MUTED}; font-size: 8pt; font-weight: 600; letter-spacing: 1.2px; }}
QLabel#tileValue {{ color: {TITLE}; }}
QLabel#tileUnit {{ color: {MUTED}; font-size: 9pt; font-weight: 600; }}
QLabel#tileSub {{ color: {MUTED}; font-size: 8.5pt; }}

/* ---- états, réglages, vides, progression ---- */
QFrame#chip {{ background: {SURFACE_ALT}; border: 1px solid {BORDER}; border-radius: 9px; }}
QLabel#chipText {{ color: {TEXT}; font-size: 8.5pt; }}
QLabel#settingTitle {{ color: {TITLE}; font-weight: 600; }}
QLabel#settingDesc {{ color: {MUTED}; font-size: 8.5pt; }}
QLabel#empty {{ color: {MUTED}; background: {SURFACE_ALT}; border: 1px dashed {BORDER_STRONG};
                border-radius: 10px; padding: 18px; }}
QLabel#progressText {{ color: {TITLE}; font-size: 8.5pt; }}

/* ---- onglets internes (historique DPS) ---- */
QTabWidget::pane {{ border: none; }}
QTabWidget::tab-bar {{ alignment: left; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{
    background: transparent; color: {MUTED}; padding: 7px 14px; margin: 0 2px 0 0;
    border: none; border-bottom: 2px solid transparent; font-weight: 600;
}}
QTabBar::tab:hover {{ color: {TITLE}; border-bottom: 2px solid {BORDER_STRONG}; }}
QTabBar::tab:selected {{ color: {PRIMARY}; border-bottom: 2px solid {AETHER}; }}

/* ---- boutons ---- */
QPushButton {{
    background: {SURFACE}; color: {TITLE}; border: 1px solid {BORDER_STRONG};
    border-radius: 8px; padding: 7px 14px; font-weight: 600;
}}
QPushButton:hover {{ border-color: {PRIMARY}; background: {HOVER}; }}
QPushButton:pressed {{ background: {ACCENT_SOFT}; }}
QPushButton:disabled {{ color: {DISABLED}; border-color: {BORDER}; background: {SURFACE_ALT}; }}
QPushButton:checked {{ background: {ACCENT_SOFT}; border-color: {PRIMARY}; color: {ACCENT_DARK}; }}
QPushButton#primary {{ background: {PRIMARY}; color: {ON_ACCENT}; border-color: {PRIMARY}; }}
QPushButton#primary:hover {{ background: {PRIMARY_HOVER}; border-color: {PRIMARY_HOVER}; }}
QPushButton#primary:disabled {{ background: {SURFACE_ALT}; color: {DISABLED}; border-color: {BORDER}; }}
QPushButton#danger {{ color: {ERR}; border-color: {DANGER_BORDER}; }}
QPushButton#danger:hover {{ background: {DANGER_HOVER}; border-color: {ERR}; }}
QPushButton#swatch {{ min-width: 54px; padding: 4px; border-radius: 6px; font-weight: 500; }}
QPushButton#chip {{ padding: 3px 10px; border-radius: 10px; font-size: 8.5pt; }}

/* ---- champs ---- */
QLineEdit, QComboBox, QFontComboBox, QSpinBox {{
    background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER_STRONG};
    border-radius: 7px; padding: 5px 8px; selection-background-color: {PRIMARY};
    selection-color: {ON_ACCENT};
}}
QLineEdit:hover, QComboBox:hover, QFontComboBox:hover, QSpinBox:hover {{ border-color: {MUTED}; }}
QLineEdit:focus, QComboBox:focus, QFontComboBox:focus, QSpinBox:focus {{ border-color: {PRIMARY}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{ color: {DISABLED}; background: {SURFACE_ALT}; }}
QComboBox::drop-down, QFontComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow, QFontComboBox::down-arrow {{ {arrow} }}
QComboBox QAbstractItemView {{
    background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER_STRONG};
    selection-background-color: {ACCENT_SOFT}; selection-color: {TITLE}; outline: none;
}}
QSpinBox::up-button, QSpinBox::down-button {{ border: none; width: 16px; background: transparent; }}
QSpinBox::up-arrow {{ {up} }}
QSpinBox::down-arrow {{ {down} }}

/* ---- cases à cocher ---- */
QCheckBox {{ color: {TEXT}; spacing: 9px; }}
QCheckBox::indicator, QTreeWidget::indicator {{
    width: 16px; height: 16px; border: 1px solid {BORDER_STRONG}; border-radius: 5px; background: {SURFACE};
}}
QCheckBox::indicator:hover, QTreeWidget::indicator:hover {{ border-color: {PRIMARY}; }}
QCheckBox::indicator:checked, QTreeWidget::indicator:checked {{
    background: {PRIMARY}; border-color: {PRIMARY}; {check}
}}
QCheckBox::indicator:disabled {{ background: {SURFACE_ALT}; border-color: {BORDER}; }}

/* ---- listes et tableaux ---- */
QTreeWidget, QListWidget, QTableWidget {{
    background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 10px;
    alternate-background-color: {SURFACE_ALT}; outline: none;
}}
QTreeWidget::item {{ padding: 4px 2px; border: none; }}
QTreeWidget::item:hover {{ background: {HOVER}; }}
QTreeWidget::item:selected {{ background: {ACCENT_SOFT}; color: {TITLE}; }}
QHeaderView::section {{
    background: {SURFACE_ALT}; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER};
    padding: 6px 7px; font-size: 8pt; font-weight: 600; letter-spacing: 1px;
}}

/* ---- barres ---- */
QProgressBar {{
    background: {SURFACE_ALT}; border: 1px solid {BORDER}; border-radius: 5px;
    color: {TITLE}; text-align: center; height: 10px; font-size: 8pt;
}}
QProgressBar::chunk {{ background: {AETHER}; border-radius: 4px; }}
QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {PRIMARY}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {SURFACE}; border: 2px solid {PRIMARY}; width: 12px; height: 12px;
    margin: -6px 0; border-radius: 8px;
}}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {SCROLL}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {SCROLL_HOVER}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {SCROLL}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ---- dialogues (questions, saisie, fichiers) ---- */
QDialog, QMessageBox, QInputDialog, QFileDialog {{ background: {SURFACE}; color: {TEXT}; }}
QMessageBox QLabel {{ color: {TEXT}; }}
"""


STYLESHEET = stylesheet()        # sans icônes (import sans application Qt) ; voir full_stylesheet()
_FULL: dict[str, str] = {}


def full_stylesheet() -> str:
    """Feuille de style complète du mode actif (avec icônes générées), mise en cache par mode."""
    if MODE not in _FULL:
        _FULL[MODE] = stylesheet(icons())
    return _FULL[MODE]
