"""Composants visuels réutilisables de la fenêtre de contrôle (jamais utilisés par les overlays)."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from . import theme


class StatTile(QFrame):
    """Tuile de statistique : LÉGENDE · valeur (chiffres tabulaires) · précision.
    N'affiche que des valeurs réelles : « — » tant qu'il n'y a pas de donnée."""

    def __init__(self, caption: str, important: bool = False, tooltip: str = ""):
        super().__init__()
        self.setObjectName("tile")
        self.setProperty("important", "true" if important else "false")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(11, 8, 11, 8)
        lay.setSpacing(1)
        self.caption = QLabel(caption.upper())
        self.caption.setObjectName("tileCaption")
        self.value = QLabel("—")
        self.value.setObjectName("tileValue")
        self.value.setFont(theme.tabular(self.value.font()))
        self.value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.sub = QLabel("")
        self.sub.setObjectName("tileSub")
        self.sub.setFont(theme.tabular(self.sub.font()))
        for w in (self.caption, self.value, self.sub):
            lay.addWidget(w)
        if tooltip:
            self.setToolTip(tooltip)

    def set(self, value: str | None, sub: str | None = None, level: str | None = None):
        self.value.setText(value if value not in (None, "") else "—")
        color = theme.LEVEL_COLORS.get(level) if level else None
        self.value.setStyleSheet(f"color:{color};" if color else "")
        self.sub.setText(sub or "")


def tile_row(*tiles: StatTile) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    for t in tiles:
        lay.addWidget(t, 1)
    return w


class StatusChip(QFrame):
    """Puce d'état d'un module : pastille colorée + nom + état."""

    def __init__(self, label: str):
        super().__init__()
        self.setObjectName("chip")
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.label = label
        lay = QHBoxLayout(self)
        lay.setContentsMargins(9, 3, 10, 3)
        lay.setSpacing(5)
        self.text = QLabel()
        self.text.setObjectName("chipText")
        self.text.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.text)
        self.level = "idle"
        self.set_state("idle", "—")

    def set_state(self, level: str, state: str, tooltip: str = ""):
        self.level = level
        self.text.setText(f"{theme.dot(level)}&nbsp;<b>{self.label}</b>&nbsp;"
                          f"<span style='color:{theme.MUTED}'>{state}</span>")
        self.setToolTip(tooltip or f"{self.label} : {state}")
