"""Portail Wayland : découpe de la zone SANS décoder tout l'écran (faux GStreamer)."""
import numpy as np

from aion_target.capture import PortalGrabber, pixel_rect, rows_to_rgb


class Struct:
    def __init__(self, w, h, fmt):
        self.v = {"width": w, "height": h, "format": fmt}

    def get_value(self, k):
        return self.v[k]


class Buf:
    pts = None

    def __init__(self, data):
        self.data, self.extracted = data, 0

    def get_size(self):
        return len(self.data)

    def extract_dup(self, off, size):
        self.extracted += size
        return self.data[off:off + size]


class Sample:
    def __init__(self, w, h, fmt, data):
        self.s, self.b = Struct(w, h, fmt), Buf(data)

    def get_caps(self):
        caps = self.s
        return type("C", (), {"get_structure": lambda _self, i: caps})()

    def get_buffer(self):
        return self.b


class Sink:
    def __init__(self):
        self.queue, self.timeouts = [], []

    def emit(self, _sig, timeout_ns):
        self.timeouts.append(timeout_ns)
        return self.queue.pop(0) if self.queue else None


def bgrx_frame(w, h, pad=8):
    """Image BGRx où R = x % 256, G = y % 256, B = 7, lignes alignées (+pad octets)."""
    rows = []
    for y in range(h):
        px = bytearray()
        for x in range(w):
            px += bytes((7, y % 256, x % 256, 0))
        rows.append(bytes(px) + b"\0" * pad)
    return b"".join(rows)


def grabber(sink):
    g = PortalGrabber.__new__(PortalGrabber)
    g.sink, g.origin, g.logical_size = sink, (0, 0), None
    g._sample, g._cur, g._crop_memo = None, (None, "RGB", 0, 0, 0, 0), None
    g.frames_received, g.last_frame_at, g._frame_ts = 0, 0.0, 0.0
    g.pipeline = None
    return g


def test_rows_to_rgb_formats():
    data = bgrx_frame(10, 4)
    out = rows_to_rgb(data, "BGRx", 10 * 4 + 8, 2, 5, 4)
    assert out.shape == (4, 3, 3)
    assert out[1, 0].tolist() == [2, 1, 7] and out[3, 2].tolist() == [4, 3, 7]
    rgb = bytes(np.arange(2 * 3 * 3, dtype=np.uint8))
    assert rows_to_rgb(rgb, "RGB", 9, 0, 3, 2).tolist() == np.arange(18, dtype=np.uint8).reshape(2, 3, 3).tolist()


def test_pixel_rect_scaling_and_bounds():
    assert pixel_rect(3840, 2160, (1920, 0), (1920, 1080), 2000, 100, 100, 20) == (160, 200, 360, 240)
    try:
        pixel_rect(100, 100, (0, 0), None, 500, 500, 10, 10)
        assert False
    except ValueError:
        pass


def test_grab_copies_only_the_zone_and_never_waits_when_a_frame_exists():
    w, h = 400, 300
    sink = Sink()
    sample = Sample(w, h, "BGRx", bgrx_frame(w, h))
    sink.queue.append(sample)
    g = grabber(sink)
    img = g.grab(100, 50, 30, 10)
    assert img.shape == (10, 30, 3) and img[0, 0].tolist() == [100, 50, 7]
    full = len(sample.b.data)
    assert sample.b.extracted <= 10 * (w * 4 + 8) < full / 20      # ~1/30 de l'écran
    again = g.grab(100, 50, 30, 10)                                   # pas de nouvelle image
    assert again is img                                               # découpe mémorisée
    assert sink.timeouts[0] == 50_000_000 and sink.timeouts[1] == 0    # jamais d'attente ensuite
    assert g.frame_age_ms() is not None and g.frame_age_ms() < 1000
