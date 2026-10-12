import numpy as np
import pytest

from aion_target.capture import choose_backend, crop_global, decode_rgb


def test_choose_backend(monkeypatch):
    assert choose_backend("mss") == "mss"
    assert choose_backend("portal") == "portal"
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    assert choose_backend("auto") == "portal"
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert choose_backend("auto") == "mss"


def test_decode_rgb_with_row_padding():
    w, h = 3, 2                      # 9 octets utiles par ligne, alignés sur 12
    rows = [bytes(range(i * 9, i * 9 + 9)) + b"\0\0\0" for i in range(h)]
    img = decode_rgb(b"".join(rows), w, h)
    assert img.shape == (2, 3, 3)
    assert img[1, 0].tolist() == [9, 10, 11]


def _frame(w=1920, h=1080):
    f = np.zeros((h, w, 3), np.uint8)
    f[24:59, 802:1093] = 255          # zone du nom, en coordonnées locales à l'écran
    return f


def test_crop_on_secondary_monitor():
    # Ta config : écran principal DP-2 à (1920, 1080), zone [2722, 1104, 291, 35]
    out = crop_global(_frame(), (1920, 1080), (1920, 1080), 2722, 1104, 291, 35)
    assert out.shape == (35, 291, 3) and out.min() == 255


def test_crop_with_hidpi_scaling():
    # Écran à 150 % : image 2880x1620 pour une taille logique 1920x1080
    frame = np.zeros((1620, 2880, 3), np.uint8)
    out = crop_global(frame, (0, 0), (1920, 1080), 100, 100, 200, 40)
    assert out.shape == (60, 300, 3)


def test_crop_outside_shared_screen_raises():
    with pytest.raises(ValueError):
        crop_global(_frame(), (1920, 1080), (1920, 1080), 100, 100, 50, 20)
