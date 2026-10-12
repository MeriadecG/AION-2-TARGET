"""Capture d'écran : mss (Windows / X11) ou portail xdg-desktop-portal + PipeWire (Wayland).

Sous Wayland, mss ne voit qu'un écran noir : seul le portail ScreenCast (celui d'OBS,
Discord...) donne accès à l'image. KDE demande l'autorisation une fois, puis on garde
un « restore token » dans la config pour ne plus redemander.
"""
from __future__ import annotations

import os
import secrets
import sys
import time

import numpy as np


# --- fonctions pures (testées) ----------------------------------------------------

def is_wayland() -> bool:
    return sys.platform.startswith("linux") and (
        os.environ.get("XDG_SESSION_TYPE") == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY")))


def choose_backend(setting: str) -> str:
    if setting in ("mss", "portal"):
        return setting
    return "portal" if is_wayland() else "mss"


def decode_rgb(data: bytes, width: int, height: int) -> np.ndarray:
    """Buffer RGB GStreamer -> tableau (h, w, 3). Les lignes peuvent être alignées (stride)."""
    arr = np.frombuffer(data, dtype=np.uint8)
    stride = len(arr) // height
    return arr[: stride * height].reshape(height, stride)[:, : width * 3].reshape(height, width, 3).copy()


# Formats acceptés en sortie de GStreamer : ceux que PipeWire fournit nativement (BGRx le plus
# souvent). videoconvert devient alors un simple passe-plat : AUCUNE conversion de tout l'écran.
NATIVE_FORMATS = ("BGRx", "BGRA", "RGBx", "RGBA", "xRGB", "ARGB", "xBGR", "ABGR", "RGB", "BGR")
_CHANNELS = {  # format → (octets par pixel, indices R, G, B)
    "BGRx": (4, 2, 1, 0), "BGRA": (4, 2, 1, 0), "RGBx": (4, 0, 1, 2), "RGBA": (4, 0, 1, 2),
    "xRGB": (4, 1, 2, 3), "ARGB": (4, 1, 2, 3), "xBGR": (4, 3, 2, 1), "ABGR": (4, 3, 2, 1),
    "RGB": (3, 0, 1, 2), "BGR": (3, 2, 1, 0),
}


def pixel_rect(frame_w: int, frame_h: int, origin: tuple[int, int], logical_size: tuple[int, int] | None,
               x: int, y: int, w: int, h: int) -> tuple[int, int, int, int]:
    """Zone en coordonnées globales du bureau → (x0, y0, x1, y1) en pixels de l'image d'un écran."""
    kx = frame_w / logical_size[0] if logical_size else 1.0
    ky = frame_h / logical_size[1] if logical_size else 1.0
    x0 = max(0, round((x - origin[0]) * kx))
    y0 = max(0, round((y - origin[1]) * ky))
    x1 = min(frame_w, round((x - origin[0] + w) * kx))
    y1 = min(frame_h, round((y - origin[1] + h) * ky))
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f"zone {[x, y, w, h]} hors de l'écran partagé "
                         f"(origine {origin}, taille {logical_size or (frame_w, frame_h)})")
    return x0, y0, x1, y1


def rows_to_rgb(rows: bytes, fmt: str, stride: int, x0: int, x1: int, nrows: int) -> np.ndarray:
    """Lignes brutes d'une image (format natif, lignes alignées) → découpe RGB (h, w, 3).
    Ne touche QUE les octets de la zone : quelques Ko au lieu de tout l'écran."""
    bpp, r, g, b = _CHANNELS[fmt]
    arr = np.frombuffer(rows, dtype=np.uint8)[: stride * nrows].reshape(nrows, stride)
    px = arr[:, x0 * bpp: x1 * bpp].reshape(nrows, x1 - x0, bpp)
    return px[:, :, [r, g, b]].copy()


def crop_global(frame: np.ndarray, origin: tuple[int, int], logical_size: tuple[int, int] | None,
                x: int, y: int, w: int, h: int) -> np.ndarray:
    """Découpe dans l'image d'UN écran une zone donnée en coordonnées globales du bureau.

    origin       : position de cet écran dans le bureau (coordonnées logiques)
    logical_size : taille logique de l'écran ; si l'image est plus grande (mise à l'échelle
                   125 %, 150 %...), les coordonnées sont converties en pixels réels.
    """
    fh, fw = frame.shape[:2]
    kx = fw / logical_size[0] if logical_size else 1.0
    ky = fh / logical_size[1] if logical_size else 1.0
    x0 = round((x - origin[0]) * kx)
    y0 = round((y - origin[1]) * ky)
    x1 = round((x - origin[0] + w) * kx)
    y1 = round((y - origin[1] + h) * ky)
    x0, x1 = max(0, x0), min(fw, x1)
    y0, y1 = max(0, y0), min(fh, y1)
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f"zone {[x, y, w, h]} hors de l'écran partagé "
                         f"(origine {origin}, taille {logical_size or (fw, fh)})")
    return frame[y0:y1, x0:x1].copy()


# --- mss ---------------------------------------------------------------------------

class MssGrabber:
    name = "mss"

    def __init__(self):
        import mss
        self._sct = getattr(mss, "MSS", mss.mss)()

    def grab(self, x: int, y: int, w: int, h: int) -> np.ndarray:
        shot = self._sct.grab({"left": x, "top": y, "width": w, "height": h})
        return np.asarray(shot)[:, :, :3][:, :, ::-1].copy()

    def full(self) -> tuple[np.ndarray, tuple[int, int]]:
        mon = self._sct.monitors[0]
        shot = self._sct.grab(mon)
        return np.asarray(shot)[:, :, :3][:, :, ::-1].copy(), (mon["left"], mon["top"])

    def close(self):
        pass


# --- portail Wayland ---------------------------------------------------------------

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SCREENCAST = "org.freedesktop.portal.ScreenCast"


class PortalError(RuntimeError):
    pass


class PortalGrabber:
    """Partage d'un écran via le portail ScreenCast, image lue par GStreamer (pipewiresrc)."""
    name = "portal"

    def __init__(self, restore_token: str | None = None, on_token=None):
        try:
            import gi
            gi.require_version("Gst", "1.0")
            from gi.repository import Gio, GLib, Gst
        except (ImportError, ValueError) as e:
            raise PortalError(
                "PyGObject/GStreamer introuvable. Installe : sudo dnf install python3-gobject "
                "gstreamer1-plugin-pipewire gstreamer1-plugins-base, et autorise le venv à les voir "
                "(voir README)") from e
        self.Gio, self.GLib, self.Gst = Gio, GLib, Gst
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._sender = self.bus.get_unique_name()[1:].replace(".", "_")
        self._sample = None               # dernière image reçue (non décodée)
        self._cur: tuple = (None, "RGB", 0, 0, 0, 0)   # (image, format, l, h, pas, n°)
        self._crop_memo: tuple | None = None   # (n° d'image, zone) → découpe déjà faite
        self.frames_received = 0          # images NOUVELLES reçues de PipeWire (perf)
        self.last_frame_at = 0.0          # horloge monotone de la dernière image reçue
        self._frame_ts = 0.0              # horloge monotone de la CAPTURE de cette image

        session = self._request("CreateSession", "(a{sv})", lambda opts: (opts,),
                                {"session_handle_token": GLib.Variant("s", self._token())})["session_handle"]
        self.session = session

        opts = {
            "types": GLib.Variant("u", 1),          # écrans (pas fenêtres)
            "multiple": GLib.Variant("b", False),
            "persist_mode": GLib.Variant("u", 2),   # mémoriser le choix
        }
        if self._cursor_modes() & 1:
            opts["cursor_mode"] = GLib.Variant("u", 1)  # curseur masqué
        if restore_token:
            opts["restore_token"] = GLib.Variant("s", restore_token)
        self._request("SelectSources", "(oa{sv})", lambda o: (session, o), opts)

        res = self._request("Start", "(osa{sv})", lambda o: (session, "", o), {})
        streams = res.get("streams") or []
        if not streams:
            raise PortalError("aucun écran partagé")
        node_id, props = streams[0]
        self.origin = tuple(props.get("position", (0, 0)))
        size = props.get("size")
        self.logical_size = tuple(size) if size else None
        if on_token and res.get("restore_token"):
            on_token(res["restore_token"])

        fd = self._open_remote()
        Gst.init(None)
        self.pipeline = Gst.parse_launch(
            f"pipewiresrc fd={fd} path={node_id} do-timestamp=true keepalive-time=500 "
            "! videoconvert ! video/x-raw,format=(string){" + ",".join(NATIVE_FORMATS) + "} "
            "! appsink name=sink max-buffers=1 drop=true sync=false")
        self.sink = self.pipeline.get_by_name("sink")
        self.pipeline.set_state(Gst.State.PLAYING)
        for _ in range(25):  # attendre la première image (jusqu'à ~5 s)
            if self._pull(200):
                break

    # -- D-Bus helpers --
    def _token(self) -> str:
        return "aiontarget_" + secrets.token_hex(6)

    def _cursor_modes(self) -> int:
        try:
            v = self.bus.call_sync(PORTAL_BUS, PORTAL_PATH, "org.freedesktop.DBus.Properties", "Get",
                                   self.GLib.Variant("(ss)", (SCREENCAST, "AvailableCursorModes")),
                                   None, self.Gio.DBusCallFlags.NONE, -1, None)
            return int(v.unpack()[0])
        except Exception:
            return 0

    def _request(self, method: str, sig: str, build_args, options: dict) -> dict:
        """Appelle une méthode du portail et attend son signal Response (boucle GLib locale)."""
        GLib, Gio = self.GLib, self.Gio
        token = self._token()
        options = dict(options, handle_token=GLib.Variant("s", token))
        path = f"/org/freedesktop/portal/desktop/request/{self._sender}/{token}"
        loop = GLib.MainLoop()
        out: dict = {}

        def on_response(_conn, _sender, _path, _iface, _signal, params):
            code, results = params.unpack()
            out["code"], out["results"] = code, results
            loop.quit()

        # Abonnement AVANT l'appel, sinon la réponse peut arriver avant qu'on écoute.
        sub = self.bus.signal_subscribe(None, "org.freedesktop.portal.Request", "Response",
                                        path, None, Gio.DBusSignalFlags.NONE, on_response)
        try:
            self.bus.call_sync(PORTAL_BUS, PORTAL_PATH, SCREENCAST, method,
                               GLib.Variant(sig, build_args(options)), None,
                               Gio.DBusCallFlags.NONE, -1, None)
            GLib.timeout_add_seconds(120, loop.quit)  # l'utilisateur a 2 min pour répondre
            loop.run()
        finally:
            self.bus.signal_unsubscribe(sub)
        if out.get("code") != 0:
            raise PortalError(f"{method} refusé ou annulé (code {out.get('code', 'délai dépassé')})")
        return out["results"]

    def _open_remote(self) -> int:
        GLib, Gio = self.GLib, self.Gio
        result, fds = self.bus.call_with_unix_fd_list_sync(
            PORTAL_BUS, PORTAL_PATH, SCREENCAST, "OpenPipeWireRemote",
            GLib.Variant("(oa{sv})", (self.session, {})), GLib.VariantType("(h)"),
            Gio.DBusCallFlags.NONE, -1, None, None)
        return fds.get(result.unpack()[0])

    # -- images --
    def _pull(self, timeout_ms: int = 200) -> bool:
        """Récupère la dernière image en attente (sans la décoder). Vrai si une image existe.
        PipeWire n'envoie une image que si l'écran change : on garde la dernière."""
        sample = self.sink.emit("try-pull-sample", timeout_ms * 1_000_000)
        if sample is not None:
            s = sample.get_caps().get_structure(0)
            w, h = s.get_value("width"), s.get_value("height")
            fmt = s.get_value("format") or "RGB"
            buf = sample.get_buffer()
            now = time.monotonic()
            if fmt not in _CHANNELS or not h:
                return self._sample is not None
            if self.last_frame_at:
                from .metrics import STATS
                # Écart entre deux images NOUVELLES lues (borné par notre cadence de lecture).
                STATS.add("new_frame_gap", (now - self.last_frame_at) * 1000)
            self.frames_received += 1
            # Un seul tuple, remplacé d'un coup : la lecture Target et celle de la monnaie
            # (deux threads) voient toujours une image cohérente.
            self._cur = (sample, fmt, w, h, buf.get_size() // h, self.frames_received)
            self._sample = sample
            self.last_frame_at = now
            self._frame_ts = now - self._queue_delay(buf)
        return self._sample is not None

    def _queue_delay(self, buf) -> float:
        """Temps (s) entre l'horodatage de capture de l'image et maintenant (0 si inconnu)."""
        try:
            clock = self.pipeline.get_clock()
            pts = buf.pts
            if clock is None or pts in (None, self.Gst.CLOCK_TIME_NONE):
                return 0.0
            running = clock.get_time() - self.pipeline.get_base_time()
            return max(0.0, min(1.0, (running - pts) / 1e9))
        except Exception:
            return 0.0

    def frame_age_ms(self) -> float | None:
        """Âge RÉEL de l'image servie (depuis sa capture), en ms."""
        return (time.monotonic() - self._frame_ts) * 1000 if self._frame_ts else None

    @property
    def last_frame(self) -> np.ndarray | None:
        """Image complète décodée (placement des cadres seulement : coûteux)."""
        if self._sample is None:
            return None
        sample, fmt, w, h, stride, _ = self._cur
        rows = sample.get_buffer().extract_dup(0, stride * h)
        return rows_to_rgb(rows, fmt, stride, 0, w, h)

    def full(self) -> tuple[np.ndarray, tuple[int, int]]:
        for _ in range(25):  # jusqu'à ~5 s pour la première image
            if self._pull(200):
                break
        frame = self.last_frame
        if frame is None:
            raise PortalError("aucune image reçue de PipeWire")
        if self.logical_size and frame.shape[1] != self.logical_size[0]:
            # Remise à l'échelle logique pour que la sélection donne des coordonnées logiques.
            from PIL import Image
            frame = np.asarray(Image.fromarray(frame).resize(self.logical_size))
        return frame, self.origin

    def grab(self, x: int, y: int, w: int, h: int) -> np.ndarray:
        # Jamais d'attente quand une image existe déjà : on prend la plus récente disponible.
        if not self._pull(0 if self._sample is not None else 50):
            raise PortalError("pas encore d'image PipeWire")
        sample, fmt, fw, fh, stride, n = self._cur
        key = (n, x, y, w, h)
        memo = self._crop_memo
        if memo and memo[0] == key:
            return memo[1]                               # même image, même zone : déjà découpée
        x0, y0, x1, y1 = pixel_rect(fw, fh, self.origin, self.logical_size, x, y, w, h)
        # Seules les lignes de la zone sont copiées depuis la mémoire de l'image.
        rows = sample.get_buffer().extract_dup(y0 * stride, (y1 - y0) * stride)
        out = rows_to_rgb(rows, fmt, stride, x0, x1, y1 - y0)
        self._crop_memo = (key, out)
        return out

    def close(self):
        try:
            self.pipeline.set_state(self.Gst.State.NULL)
        except Exception:
            pass


def make_grabber(cfg):
    """Crée le grabber adapté. Le portail doit être créé dans le thread principal."""
    if choose_backend(cfg.capture_backend) == "portal":
        def save_token(tok):
            cfg.portal_restore_token = tok
            cfg.save()
        return PortalGrabber(cfg.portal_restore_token or None, on_token=save_token)
    return MssGrabber()
