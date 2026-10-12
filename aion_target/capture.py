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
        self.last_frame: np.ndarray | None = None
        self.frames_received = 0          # images NOUVELLES reçues de PipeWire (perf)
        self.last_frame_at = 0.0          # horloge monotone de la dernière image reçue

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
            "! videoconvert ! video/x-raw,format=RGB "
            "! appsink name=sink max-buffers=1 drop=true sync=false")
        self.sink = self.pipeline.get_by_name("sink")
        self.pipeline.set_state(Gst.State.PLAYING)
        for _ in range(25):  # attendre la première image (jusqu'à ~5 s)
            if self._pull(200) is not None:
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
    def _pull(self, timeout_ms: int = 200) -> np.ndarray | None:
        sample = self.sink.emit("try-pull-sample", timeout_ms * 1_000_000)
        if sample is not None:
            s = sample.get_caps().get_structure(0)
            w, h = s.get_value("width"), s.get_value("height")
            buf = sample.get_buffer()
            ok, info = buf.map(self.Gst.MapFlags.READ)
            if ok:
                try:
                    self.last_frame = decode_rgb(bytes(info.data), w, h)
                    self.frames_received += 1
                    self.last_frame_at = time.monotonic()
                finally:
                    buf.unmap(info)
        # PipeWire n'envoie une image que si l'écran change : on garde la dernière.
        return self.last_frame

    def full(self) -> tuple[np.ndarray, tuple[int, int]]:
        frame = None
        for _ in range(25):  # jusqu'à ~5 s pour la première image
            frame = self._pull(200)
            if frame is not None:
                break
        if frame is None:
            raise PortalError("aucune image reçue de PipeWire")
        if self.logical_size and frame.shape[1] != self.logical_size[0]:
            # Remise à l'échelle logique pour que la sélection donne des coordonnées logiques.
            from PIL import Image
            frame = np.asarray(Image.fromarray(frame).resize(self.logical_size))
        return frame, self.origin

    def grab(self, x: int, y: int, w: int, h: int) -> np.ndarray:
        frame = self._pull(50)
        if frame is None:
            raise PortalError("pas encore d'image PipeWire")
        return crop_global(frame, self.origin, self.logical_size, x, y, w, h)

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
