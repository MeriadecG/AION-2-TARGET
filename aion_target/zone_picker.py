"""Sélection de la zone de capture à la souris sur une capture plein écran."""
from __future__ import annotations


def map_selection(sel: tuple[int, int, int, int], shown: tuple[int, int, int, int],
                  img_size: tuple[int, int], origin: tuple[int, int]) -> list[int]:
    """Convertit un rectangle dessiné sur l'image affichée en coordonnées écran réelles.

    sel     : (x, y, w, h) sélection dans le widget
    shown   : (x, y, w, h) où l'image est dessinée dans le widget (elle peut être réduite)
    img_size: (largeur, hauteur) de la capture en pixels réels
    origin  : coin haut-gauche du bureau virtuel (peut être négatif en multi-écran)
    """
    sx, sy, sw, sh = sel
    dx, dy, dw, dh = shown
    iw, ih = img_size
    kx, ky = iw / dw, ih / dh
    # Bornage à l'image affichée.
    x0 = min(max(sx, dx), dx + dw) - dx
    y0 = min(max(sy, dy), dy + dh) - dy
    x1 = min(max(sx + sw, dx), dx + dw) - dx
    y1 = min(max(sy + sh, dy), dy + dh) - dy
    return [origin[0] + round(x0 * kx), origin[1] + round(y0 * ky),
            max(1, round((x1 - x0) * kx)), max(1, round((y1 - y0) * ky))]


def to_widget(rect, shown, img_size, origin) -> list[int]:
    """Inverse de map_selection : rectangle écran → rectangle dans l'image affichée.

    Un cadre hors de l'écran capturé est ramené dedans (pour rester attrapable).
    """
    x, y, w, h = rect
    dx, dy, dw, dh = shown
    iw, ih = img_size
    kx, ky = dw / iw, dh / ih
    ww, wh = max(8, round(w * kx)), max(6, round(h * ky))
    wx = dx + round((x - origin[0]) * kx)
    wy = dy + round((y - origin[1]) * ky)
    wx = min(max(wx, dx), dx + dw - ww)
    wy = min(max(wy, dy), dy + dh - wh)
    return [wx, wy, ww, wh]


ROLES = ("zone", "gs", "cp", "details", "rank")


def open_picker(cfg, grabber, on_done=None):
    """Éditeur sur capture : 4 cadres (lecture, GS, CP, détails) à placer et redimensionner.

    Non bloquant ; on_done(bool enregistré) est appelé à la fermeture.
    """
    import numpy as np
    from PyQt6.QtCore import QPoint, QRect, Qt
    from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
    from PyQt6.QtWidgets import QApplication, QWidget

    from .overlay import HANDLE, ROLE_COLORS, ROLE_LABELS, draw_segments
    from .style import frame_contents, sample_info

    app = QApplication.instance()
    shot, origin = grabber.full()   # mss : bureau complet ; portail : l'écran partagé
    shot = np.ascontiguousarray(shot)
    black = shot.mean() < 1
    ih, iw = shot.shape[:2]
    pixmap = QPixmap.fromImage(QImage(shot.data, iw, ih, 3 * iw, QImage.Format.Format_RGB888).copy())
    sample = frame_contents(sample_info(), cfg)

    class Editor(QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Aion Target - placement des cadres")
            self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            self.saved = False
            self.rects: dict[str, QRect] = {}
            self.active: str | None = None
            self.mode = None            # "move" | "resize"
            self.grab_offset = QPoint()
            avail = app.primaryScreen().availableGeometry()
            self.resize(min(iw, avail.width() - 40), min(ih + 40, avail.height() - 40))

        def image_rect(self) -> QRect:
            area = self.rect().adjusted(0, 40, 0, 0)
            scaled = pixmap.size().scaled(area.size(), Qt.AspectRatioMode.KeepAspectRatio)
            return QRect(area.x() + (area.width() - scaled.width()) // 2, area.y(),
                         scaled.width(), scaled.height())

        def shown(self):
            r = self.image_rect()
            return (r.x(), r.y(), r.width(), r.height())

        def ensure_rects(self):
            if self.rects:
                return
            sources = {"zone": cfg.capture, **cfg.frames()}
            for role in ROLES:
                self.rects[role] = QRect(*to_widget(sources[role], self.shown(), (iw, ih), origin))

        def resizeEvent(self, e):
            # Garder les cadres au même endroit de l'image quand la fenêtre change de taille.
            if self.rects and e.oldSize().isValid() and e.oldSize().width() > 0:
                old = self._last_shown
                for role, r in self.rects.items():
                    real = map_selection((r.x(), r.y(), r.width(), r.height()), old, (iw, ih), origin)
                    self.rects[role] = QRect(*to_widget(real, self.shown(), (iw, ih), origin))
            self._last_shown = self.shown()
            super().resizeEvent(e)

        def paintEvent(self, _):
            self.ensure_rects()
            self._last_shown = self.shown()
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.fillRect(self.rect(), QColor(21, 23, 28))
            p.drawPixmap(self.image_rect(), pixmap)
            p.setPen(QColor(255, 120, 120) if black else QColor(235, 235, 235))
            p.setFont(QFont("Sans Serif", 10))
            msg = ("Capture entièrement noire : la zone ne pourra pas être lue (voir README)" if black
                   else "Glisse les cadres · coin = taille · BLEU : nom à lire (ligne « 45 Nom ») · "
                        "Entrée = enregistrer · Échap = annuler")
            p.drawText(QRect(12, 0, self.width() - 24, 40), Qt.AlignmentFlag.AlignVCenter, msg)
            for role in ROLES:
                r, col = self.rects[role], ROLE_COLORS[role]
                fill = QColor(col)
                fill.setAlpha(60 if role == "zone" else 30)
                p.fillRect(r, fill)
                p.setPen(QPen(col, 2 if role == self.active else 1.5, Qt.PenStyle.DashLine))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(r)
                p.fillRect(r.right() - HANDLE + 1, r.bottom() - HANDLE + 1, HANDLE, HANDLE, col)
                if role == "zone":
                    p.setPen(col)
                    p.setFont(QFont("Sans Serif", 8, QFont.Weight.Bold))
                    p.drawText(r.x(), r.y() - 4, ROLE_LABELS[role])
                else:
                    draw_segments(p, r, sample[role] or [(ROLE_LABELS[role], col.name())],
                                  cfg.font_family, "stronger" if role == "cp" else None)

        def role_at(self, pos):
            for role in reversed(ROLES):           # le cadre dessiné au-dessus d'abord
                r = self.rects[role]
                if r.adjusted(-3, -3, 3, 3).contains(pos):
                    return role
            return None

        def mousePressEvent(self, e):
            pos = e.position().toPoint()
            self.active = self.role_at(pos)
            if self.active:
                r = self.rects[self.active]
                near_corner = pos.x() >= r.right() - HANDLE - 2 and pos.y() >= r.bottom() - HANDLE - 2
                self.mode = "resize" if near_corner else "move"
                self.grab_offset = pos - r.topLeft()
            self.update()

        def mouseMoveEvent(self, e):
            if not self.active:
                return
            pos = e.position().toPoint()
            bounds = self.image_rect()
            r = QRect(self.rects[self.active])
            if self.mode == "resize":
                r.setRight(max(r.left() + 12, min(pos.x(), bounds.right())))
                r.setBottom(max(r.top() + 6, min(pos.y(), bounds.bottom())))
            else:
                r.moveTopLeft(pos - self.grab_offset)
                r.moveLeft(min(max(r.left(), bounds.left()), bounds.right() - r.width()))
                r.moveTop(min(max(r.top(), bounds.top()), bounds.bottom() - r.height()))
            self.rects[self.active] = r
            self.update()

        def mouseReleaseEvent(self, _):
            self.mode = None

        def keyPressEvent(self, e):
            if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                for role in ROLES:                   # la zone d'abord (contrôle de chevauchement)
                    r = self.rects[role]
                    cfg.set_frame(role, map_selection((r.x(), r.y(), r.width(), r.height()),
                                                      self.shown(), (iw, ih), origin))
                cfg.save()
                self.saved = True
                self.close()
            elif e.key() == Qt.Key.Key_Escape:
                self.close()

        def closeEvent(self, e):
            if on_done:
                on_done(self.saved)
            super().closeEvent(e)

    w = Editor()
    w.showMaximized()
    w.activateWindow()
    return w


def pick_zone(cfg, grabber) -> bool:
    """Version bloquante (ligne de commande --select-zone)."""
    from PyQt6.QtWidgets import QApplication
    from .style import register_bundled_fonts
    app = QApplication.instance() or QApplication([])
    register_bundled_fonts()
    result = {"ok": False}

    def done(ok):
        result["ok"] = ok
        app.quit()
    _w = open_picker(cfg, grabber, done)  # noqa: F841 (garder une référence)
    app.exec()
    return result["ok"]
