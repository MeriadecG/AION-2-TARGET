"""Thème « AION 2 Tactical Analytics » de la FENÊTRE DE CONTRÔLE uniquement.

Clair, froid, technique. Les overlays affichés par-dessus le jeu (cadres GS/CP, DPS, monnaie,
familiers, carré de test, sélecteur de zone) ne l'utilisent PAS : ils sont des fenêtres de premier
niveau dessinées à la main (QPainter) avec leurs propres couleurs, inchangées.

Toutes les couleurs de l'interface viennent d'ici (aucune valeur codée en dur ailleurs).
"""
from __future__ import annotations

from pathlib import Path

# --- palette ------------------------------------------------------------------------------
BG = "#ECF2F7"          # fond général gris glacier
SURFACE = "#FFFFFF"     # cartes, champs
SURFACE_ALT = "#F5F8FB"  # lignes alternées, en-têtes de tableau
BORDER = "#D3DFE9"
BORDER_STRONG = "#B9CBDA"
TITLE = "#163B5D"       # titres bleu acier, texte principal
TEXT = "#1E3550"
MUTED = "#637D95"       # texte secondaire
ACCENT = "#3C9FC9"      # cyan : interaction, sélection
ACCENT_DARK = "#2A7FA6"
ACCENT_SOFT = "#E3F2F9"
GOLD = "#DBA954"        # or discret : éléments importants (traits, repères)
GOLD_TEXT = "#8A6420"   # or lisible sur fond blanc (texte)
GOLD_SOFT = "#FBF4E6"

# États fonctionnels (contraste ≥ 4,5:1 sur blanc pour le texte)
OK = "#17804F"
WARN = "#A85F00"
ERR = "#B8322B"
IDLE = MUTED
LEVEL_COLORS = {"ok": OK, "warn": WARN, "err": ERR, "idle": IDLE, "info": ACCENT_DARK}

# Polices : la première disponible (lisible, chiffres nets) ; titre de marque en Cinzel (fourni).
UI_FONTS = ["Inter", "Segoe UI Variable Text", "Segoe UI", "Noto Sans", "Cantarell", "Ubuntu",
            "DejaVu Sans"]
BRAND_FONT = "Cinzel"


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
        pen = QPen(QColor("#FFFFFF"), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
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
/* ---- fond et texte ---- */
QWidget#root, QWidget#body, QWidget#events {{ background: {BG}; color: {TEXT}; }}
QScrollArea {{ border: none; background: {BG}; }}
QScrollArea > QWidget > QWidget {{ background: {BG}; }}
QLabel {{ color: {TEXT}; background: transparent; }}
QLabel#title {{ color: {TITLE}; }}
QLabel#brandSub {{ color: {MUTED}; font-size: 8pt; letter-spacing: 2px; }}
QLabel#section {{ color: {MUTED}; font-size: 8pt; font-weight: 600; letter-spacing: 1.5px;
                  padding: 4px 0 0 2px; }}
QLabel#status {{ color: {MUTED}; }}
QLabel#next {{ color: {GOLD_TEXT}; font-weight: 600; }}
QLabel#version {{ color: {MUTED}; font-size: 8pt; }}
QToolTip {{ background: {TITLE}; color: #FFFFFF; border: none; padding: 5px 7px; }}

/* ---- cartes ---- */
QFrame#card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; }}
QFrame#header {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#accentBar {{ background: {GOLD}; border: none; border-radius: 2px; }}

/* ---- tuiles de statistiques ---- */
QFrame#tile {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; }}
QFrame#tile[important="true"] {{ border-top: 3px solid {GOLD}; }}
QLabel#tileCaption {{ color: {MUTED}; font-size: 7.5pt; font-weight: 600; letter-spacing: 1.2px; }}
QLabel#tileValue {{ color: {TITLE}; font-size: 15pt; font-weight: 600; }}
QLabel#tileSub {{ color: {MUTED}; font-size: 8pt; }}

/* ---- puces d'état ---- */
QFrame#chip {{ background: {SURFACE_ALT}; border: 1px solid {BORDER}; border-radius: 9px; }}
QLabel#chipText {{ color: {TEXT}; font-size: 8pt; }}

/* ---- navigation (onglets) ---- */
QTabWidget::pane {{ border: none; }}
QTabWidget::tab-bar {{ alignment: left; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{
    background: transparent; color: {MUTED}; padding: 7px 14px; margin: 0 2px 0 0;
    border: none; border-bottom: 2px solid transparent; font-weight: 600;
}}
QTabBar::tab:hover {{ color: {TITLE}; border-bottom: 2px solid {BORDER_STRONG}; }}
QTabBar::tab:selected {{ color: {TITLE}; border-bottom: 2px solid {ACCENT}; }}

/* ---- boutons ---- */
QPushButton {{
    background: {SURFACE}; color: {TITLE}; border: 1px solid {BORDER_STRONG};
    border-radius: 7px; padding: 6px 12px; font-weight: 600;
}}
QPushButton:hover {{ border-color: {ACCENT}; background: #F7FBFD; }}
QPushButton:pressed {{ background: {ACCENT_SOFT}; }}
QPushButton:disabled {{ color: #9FB2C3; border-color: {BORDER}; background: {SURFACE_ALT}; }}
QPushButton:checked {{ background: {ACCENT_SOFT}; border-color: {ACCENT}; color: {ACCENT_DARK}; }}
QPushButton#primary {{ background: {ACCENT}; color: #FFFFFF; border-color: {ACCENT}; }}
QPushButton#primary:hover {{ background: {ACCENT_DARK}; border-color: {ACCENT_DARK}; }}
QPushButton#danger {{ color: {ERR}; border-color: #E9C3BF; }}
QPushButton#danger:hover {{ background: #FCF1F0; border-color: {ERR}; }}
QPushButton#swatch {{ min-width: 54px; padding: 4px; border-radius: 6px; font-weight: 500; }}
QPushButton#chip {{ padding: 2px 9px; border-radius: 10px; font-size: 8pt; }}

/* ---- champs ---- */
QLineEdit, QComboBox, QFontComboBox, QSpinBox {{
    background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER_STRONG};
    border-radius: 6px; padding: 4px 7px; selection-background-color: {ACCENT};
    selection-color: #FFFFFF;
}}
QLineEdit:focus, QComboBox:focus, QFontComboBox:focus, QSpinBox:focus {{ border-color: {ACCENT}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{ color: #9FB2C3; background: {SURFACE_ALT}; }}
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
QCheckBox {{ color: {TEXT}; spacing: 8px; }}
QCheckBox::indicator, QTreeWidget::indicator {{
    width: 15px; height: 15px; border: 1px solid #9DB3C6; border-radius: 4px; background: {SURFACE};
}}
QCheckBox::indicator:hover, QTreeWidget::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked, QTreeWidget::indicator:checked {{
    background: {ACCENT}; border-color: {ACCENT}; {check}
}}
QCheckBox::indicator:disabled {{ background: {SURFACE_ALT}; border-color: {BORDER}; }}

/* ---- listes et tableaux ---- */
QTreeWidget, QListWidget, QTableWidget {{
    background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 8px;
    alternate-background-color: {SURFACE_ALT}; outline: none;
}}
QTreeWidget::item {{ padding: 3px 2px; border: none; }}
QTreeWidget::item:hover {{ background: #EEF6FB; }}
QTreeWidget::item:selected {{ background: {ACCENT_SOFT}; color: {TITLE}; }}
QHeaderView::section {{
    background: {SURFACE_ALT}; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER};
    padding: 5px 6px; font-size: 7.5pt; font-weight: 600; letter-spacing: 1px;
}}

/* ---- barres ---- */
QProgressBar {{
    background: {SURFACE_ALT}; border: 1px solid {BORDER}; border-radius: 5px;
    color: {TITLE}; text-align: center; height: 14px; font-size: 8pt;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {SURFACE}; border: 2px solid {ACCENT}; width: 12px; height: 12px;
    margin: -6px 0; border-radius: 8px;
}}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #C3D2DF; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #A6BBCD; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #C3D2DF; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ---- dialogues (questions, saisie, fichiers) ---- */
QDialog, QMessageBox, QInputDialog, QFileDialog {{ background: {SURFACE}; color: {TEXT}; }}
QMessageBox QLabel {{ color: {TEXT}; }}
"""


STYLESHEET = stylesheet()        # sans icônes (import sans application Qt) ; voir full_stylesheet()
_FULL: str | None = None


def full_stylesheet() -> str:
    """Feuille de style complète (avec icônes générées) — à appeler une fois l'application créée."""
    global _FULL
    if _FULL is None:
        _FULL = stylesheet(icons())
    return _FULL
