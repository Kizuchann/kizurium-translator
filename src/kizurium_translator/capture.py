"""Screen capture.

Region selection is not here: it is the GTK4 layer-shell overlay in selector.py.
On KDE two backends are used, in this order and never mixed up silently:

* ``org.kde.KWin.ScreenShot2``, tried once. KWin only answers it for apps it has
  authorized (X-KDE-DBUS-Restricted-Interfaces on an installed desktop file whose
  Exec is this executable). A ``NoAuthorized`` answer is logged with its reason and
  remembered for the process, so a denied interface is not called again per frame.
* the XDG ScreenCast portal (user consent, remembered through a restore token).

Elsewhere only standard wlroots tools are used, so any compositor that supports
layer-shell works:

* ``grim -g "x,y WxH" -``  capture a region straight into memory
* ``slurp -o -r``          pick one whole output, for --output
"""

from __future__ import annotations

import atexit
import io
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from PIL import Image

GEOM_RE = re.compile(r"^\s*(-?\d+),(-?\d+)\s+(\d+)x(\d+)\s*$")


class CaptureError(RuntimeError):
    pass


class KWinNotAuthorized(CaptureError):
    """KWin refused ScreenShot2 to this process; the portal is the supported route."""


class KWinUnsupported(CaptureError):
    """KWin answered in a layout this code cannot read; that does not change mid-session."""


log = logging.getLogger(__name__)
# None: not tried yet. True: KWin answered. A string: why it was refused.
_kwin_state: bool | str | None = None
_kwin_last_transient = ""
_frame_request = threading.local()
_portal_parent = ""


def set_portal_parent(handle: str) -> None:
    """Exported application toplevel, used to parent the consent dialog."""
    global _portal_parent
    _portal_parent = handle


@contextmanager
def fresh_frame_after(timestamp: float):
    """A clean capture must not consume a queued frame containing the overlay."""
    previous = getattr(_frame_request, "after", None)
    _frame_request.after = timestamp
    try:
        yield
    finally:
        _frame_request.after = previous


def ensure_wayland_env() -> None:
    """Fill in the Wayland variables a compositor-spawned process may not have.

    A keybind runs the command through the compositor, and the compositor's
    environment is not guaranteed to carry WAYLAND_DISPLAY. Without it grim and
    the GTK selector both fail: the region selector simply did nothing when
    launched from the keybind, while working when started from a shell. So the variables
    are recovered from XDG_RUNTIME_DIR when they are missing.
    """
    if not os.environ.get("WAYLAND_DISPLAY"):
        runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        try:
            sockets = sorted(
                p for p in os.listdir(runtime) if p.startswith("wayland-") and os.path.exists(os.path.join(runtime, p))
            )
        except OSError:
            sockets = []
        # wayland-1 is the usual first client socket; prefer it when present
        for name in sockets:
            if name == "wayland-1":
                os.environ["WAYLAND_DISPLAY"] = name
                break
        else:
            if sockets:
                os.environ["WAYLAND_DISPLAY"] = sockets[0]
    # Compositor-specific sockets (Hyprland / Niri / Mango) — optional recovery
    # so a keybind that stripped the env still works. Never required for grim.
    try:
        from .compositor import recover_compositor_env

        recover_compositor_env()
    except Exception:  # noqa: BLE001
        if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
            runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
            hypr = os.path.join(runtime, "hypr")
            try:
                entries = sorted(os.listdir(hypr))
            except OSError:
                entries = []
            for name in entries:
                sig_file = os.path.join(hypr, name, ".hisig")
                if os.path.exists(sig_file):
                    os.environ["HYPRLAND_INSTANCE_SIGNATURE"] = name
                    break


def have(tool: str) -> bool:
    return shutil.which(tool) is not None


def parse_geom(geom: str) -> tuple[int, int, int, int]:
    """``"x,y WxH"`` -> ``(x, y, w, h)``. Raises on anything malformed."""
    m = GEOM_RE.match(geom or "")
    if not m:
        raise ValueError(f"bad geometry {geom!r}, expected 'x,y WxH'")
    x, y, w, h = (int(g) for g in m.groups())
    if w <= 0 or h <= 0:
        raise ValueError(f"bad geometry {geom!r}: width and height must be positive")
    return x, y, w, h


def normalize_geom(geom: str) -> str:
    """Canonical form of a geometry, or ``""`` when it is not valid."""
    try:
        x, y, w, h = parse_geom(geom)
    except ValueError:
        return ""
    return f"{x},{y} {w}x{h}"


def capture_image(geom: str) -> Image.Image:
    """Screenshot a region and decode it in memory.

    grim writes PNG to stdout when given ``-``, which avoids writing a temporary
    file on every poll of the live loop.
    """
    if "KDE" in os.environ.get("XDG_CURRENT_DESKTOP", ""):
        return kde_capture(geom)
    if not have("grim"):
        raise CaptureError("grim is not installed (needs Wayland screencopy; Arch: grim, Nix: pkgs.grim)")

    from PIL import Image

    res = subprocess.run(
        ["grim", "-g", geom, "-"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if res.returncode != 0 or not res.stdout:
        err = res.stderr.decode("utf-8", "replace").strip()
        raise CaptureError(f"grim failed: {err or res.returncode}")
    return Image.open(io.BytesIO(res.stdout)).convert("RGB")


def select_output() -> str:
    """Pick one whole output.

    ``-o`` adds a rectangle per output, ``-r`` requires picking one of them, so
    this needs no compositor-specific IPC.
    """
    if not have("slurp"):
        raise CaptureError("slurp is not installed (Arch: slurp, Nix: pkgs.slurp)")

    res = subprocess.run(
        ["slurp", "-o", "-r"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    if res.returncode != 0:
        return ""
    return normalize_geom(res.stdout.strip())



# ------------------------------------------------------------------- KDE


def kde_capture(geom: str):
    """KWin ScreenShot2 where KWin allows it, otherwise the portal, said out loud.

    A refusal that will not change (not authorized, a layout we cannot read) is
    remembered for the process. A one-off failure (a timeout, an interrupted
    read) sends only that frame to the portal, so one hiccup does not demote the
    faster backend until the next restart.
    """
    global _kwin_state, _kwin_last_transient
    if _kwin_state is None or _kwin_state is True:
        try:
            image = kwin_capture(geom)
        except (KWinNotAuthorized, KWinUnsupported) as exc:
            _kwin_state = str(exc)
            log.warning("capture: KWin ScreenShot2 refused (%s); using the XDG ScreenCast portal", exc)
        except CaptureError as exc:
            if str(exc) != _kwin_last_transient:  # one line per distinct failure, not per frame
                log.warning("capture: KWin ScreenShot2 failed (%s); portal for this frame", exc)
            _kwin_last_transient = str(exc)
        else:
            _kwin_state = True
            _kwin_last_transient = ""
            return image
    return portal_capture(geom)


def capture_backend() -> str:
    """Which KDE backend is in use, for diagnostics: kwin / portal / untried."""
    if _kwin_state is True:
        return "kwin-screenshot2"
    if isinstance(_kwin_state, str):
        return f"portal (KWin ScreenShot2 refused: {_kwin_state})"
    return "untried"


def _kwin_image_layout(info):
    """QImage formats returned by ScreenShot2 (byte order matters).

    https://doc.qt.io/qt-6/qimage.html#Format-enum
    RGB32/ARGB32 are native-endian; RGBX8888/RGBA8888 are byte-ordered.
    """
    layouts = {
        4: ("RGB", "BGRX"),
        5: ("RGBA", "BGRA"),
        6: ("RGBa", "BGRa"),
        16: ("RGB", "RGBX"),
        17: ("RGBA", "RGBA"),
        18: ("RGBa", "RGBa"),
    }
    try:
        width, height, stride, fmt = (int(info[key]) for key in ("width", "height", "stride", "format"))
        mode, raw_mode = layouts[fmt]
    except (KeyError, TypeError, ValueError):
        raise KWinUnsupported(f"Unsupported KWin screenshot format: {info.get('format')}") from None
    if fmt in (4, 5, 6) and sys.byteorder != "little":
        raise KWinUnsupported("Unsupported native-endian KWin screenshot format")
    if width <= 0 or height <= 0 or stride < width * 4 or not 0 < stride * height <= 256 * 1024 * 1024:
        raise CaptureError("Invalid KWin screenshot dimensions or stride")
    return width, height, stride, mode, raw_mode


def _decode_kwin_image(data: bytes, info):
    from PIL import Image

    width, height, stride, mode, raw_mode = _kwin_image_layout(info)
    if len(data) != stride * height:
        raise CaptureError("KWin screenshot interrupted")
    image = Image.frombytes(mode, (width, height), data, "raw", raw_mode, stride)
    if mode == "RGBa":
        image = image.convert("RGBA")
    return image.convert("RGB")


def kwin_capture(geom: str):
    """KWin ScreenShot2 raw FD transport; blocking, called only from capture workers."""
    import select

    from gi.repository import Gio, GLib

    x, y, width, height = parse_geom(geom)
    read_fd, write_fd = os.pipe()
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        fds = Gio.UnixFDList.new()
        index = fds.append(write_fd)
        reply, _ = bus.call_with_unix_fd_list_sync(
            "org.kde.KWin", "/org/kde/KWin/ScreenShot2", "org.kde.KWin.ScreenShot2", "CaptureArea",
            GLib.Variant("(iiuua{sv}h)", (x, y, width, height, {"include-cursor": GLib.Variant("b", False)}, index)),
            GLib.VariantType.new("(a{sv})"), Gio.DBusCallFlags.NONE, 5000, fds, None)
        info = reply.unpack()[0]
        _, image_height, stride, _, _ = _kwin_image_layout(info)
        size = stride * image_height
        chunks, count, deadline = [], 0, time.monotonic() + 5
        while count < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([read_fd], [], [], remaining)[0]:
                raise CaptureError("KWin screenshot timed out")
            chunk = os.read(read_fd, min(1024 * 1024, size - count))
            if not chunk:
                raise CaptureError("KWin screenshot interrupted")
            chunks.append(chunk)
            count += len(chunk)
        image = _decode_kwin_image(b"".join(chunks), info)
        return image if image.size == (width, height) else image.resize((width, height))
    except GLib.Error as exc:
        if "NoAuthorized" in exc.message or "NoAuthorized" in str(exc):
            raise KWinNotAuthorized(exc.message) from None
        raise CaptureError("KWin ScreenShot2 unavailable: " + exc.message) from None
    finally:
        os.close(read_fd)
        os.close(write_fd)


# --------------------------------------------------------------- portal (KDE)

_PORTAL = "org.freedesktop.portal.Desktop"
_PORTAL_PATH = "/org/freedesktop/portal/desktop"
_CONSENT_TIMEOUT_S = 120.0
_FRAME_TIMEOUT_S = 3.0


class _PortalSession:
    """One ScreenCast session with a PipeWire stream per selected monitor.

    The first capture asks the user for consent; later runs reuse the restore
    token. A denial raises CaptureError and is not retried until the process
    restarts, so a refused prompt can neither loop nor freeze the caller.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.streams: list[dict] = []
        self.denied: str | None = None
        self.session_handle: str | None = None
        self._bus = None
        self._fd_list = None
        self.cancelled = threading.Event()

    # -- D-Bus plumbing -------------------------------------------------
    def _request(self, method: str, args, ctx, timeout: float):
        """Call a portal method that answers through a Request.Response signal."""
        from gi.repository import Gio, GLib

        bus = self._bus
        sender = bus.get_unique_name().lstrip(":").replace(".", "_")
        token = f"kz{os.getpid()}_{int(time.monotonic() * 1e6)}"
        req_path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        result: list = []

        def on_response(_c, _s, _p, _i, _sig, params):
            result.append(params.unpack())

        sub = bus.signal_subscribe(
            _PORTAL, "org.freedesktop.portal.Request", "Response", req_path, None,
            Gio.DBusSignalFlags.NONE, on_response,
        )
        try:
            options = dict(args["options"])
            options["handle_token"] = GLib.Variant("s", token)
            bus.call_sync(
                _PORTAL, _PORTAL_PATH, "org.freedesktop.portal.ScreenCast", method,
                args["build"](options), None, Gio.DBusCallFlags.NONE, 10000, None,
            )
            deadline = time.monotonic() + timeout
            while not result:
                if self.cancelled.is_set():
                    raise CaptureError("screen capture request cancelled")
                if time.monotonic() >= deadline:
                    raise CaptureError("screen capture permission prompt timed out")
                ctx.iteration(False) or time.sleep(0.02)
        finally:
            bus.signal_unsubscribe(sub)
            if not result:
                try:
                    bus.call_sync(_PORTAL, req_path, "org.freedesktop.portal.Request", "Close",
                                  None, None, Gio.DBusCallFlags.NONE, 1000, None)
                except GLib.Error:
                    pass
        code, data = result[0]
        if code == 1:
            raise CaptureError("screen capture permission was denied by the user")
        if code != 0:
            raise CaptureError("screen capture request was cancelled or failed")
        return data

    def _token_file(self):
        from .paths import default_paths

        return default_paths().state_dir / "screencast-restore-token"

    def start(self) -> None:
        from gi.repository import Gio, GLib

        self.cancelled.clear()
        ctx = GLib.MainContext.new()
        ctx.push_thread_default()
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            try:
                capabilities = self._bus.call_sync(
                    _PORTAL, _PORTAL_PATH, "org.freedesktop.DBus.Properties", "Get",
                    GLib.Variant("(ss)", ("org.freedesktop.portal.ScreenCast", "AvailableSourceTypes")),
                    None, Gio.DBusCallFlags.NONE, 3000, None,
                ).unpack()[0]
                if not int(capabilities) & 1:
                    raise CaptureError("ScreenCast portal cannot share a monitor")
            except GLib.Error as exc:
                raise CaptureError(
                    "XDG ScreenCast portal is unavailable (install xdg-desktop-portal-kde "
                    f"and make sure it is running): {exc.message}"
                ) from None
            created = self._request(
                "CreateSession",
                {"options": {"session_handle_token": GLib.Variant("s", "kz_session")},
                 "build": lambda o: GLib.Variant("(a{sv})", (o,))},
                ctx, 10.0,
            )
            self.session_handle = created["session_handle"]
            sel_opts = {
                "types": GLib.Variant("u", 1),  # monitors
                "multiple": GLib.Variant("b", True),
                "cursor_mode": GLib.Variant("u", 1),  # hidden
                "persist_mode": GLib.Variant("u", 2),
            }
            token_file = self._token_file()
            try:
                token = token_file.read_text().strip()
            except OSError:
                token = ""
            if token:
                sel_opts["restore_token"] = GLib.Variant("s", token)
            self._request(
                "SelectSources",
                {"options": sel_opts,
                 "build": lambda o: GLib.Variant("(oa{sv})", (self.session_handle, o))},
                ctx, 10.0,
            )
            started = self._request(
                "Start",
                {"options": {},
                 "build": lambda o: GLib.Variant("(osa{sv})", (self.session_handle, _portal_parent, o))},
                ctx, _CONSENT_TIMEOUT_S,
            )
            nodes = started.get("streams") or []
            if not nodes:
                raise CaptureError("screen capture portal returned no streams")
            self._validate_sources(nodes, restored=bool(token))
            new_token = started.get("restore_token")
            if new_token:
                try:
                    token_file.parent.mkdir(parents=True, exist_ok=True)
                    token_file.write_text(new_token)
                    token_file.chmod(0o600)
                except OSError:
                    pass
            fds = Gio.UnixFDList.new()
            reply, out_fds = self._bus.call_with_unix_fd_list_sync(
                _PORTAL, _PORTAL_PATH, "org.freedesktop.portal.ScreenCast",
                "OpenPipeWireRemote",
                GLib.Variant("(oa{sv})", (self.session_handle, {})), GLib.VariantType.new("(h)"),
                Gio.DBusCallFlags.NONE, 5000, fds, None,
            )
            self._fd_list = out_fds
            fd_index = reply.unpack()[0]
            for node, props in nodes:
                self.streams.append(self._open_stream(out_fds.get(fd_index), node, props))
        finally:
            ctx.pop_thread_default()

    def _validate_sources(self, nodes, *, restored=False):
        # A saved token may refer to a window selected by an earlier version.
        # Only fresh requests can rely on the monitor-only SelectSources mask.
        if any(props.get("source_type", None if restored else 1) != 1 for _, props in nodes):
            self.reset_consent()
            raise CaptureError("The restored capture source is not a monitor. Select the entire screen (DP-1); the old choice was reset.")

    def _open_stream(self, fd: int, node: int, props: dict) -> dict:
        import gi

        gi.require_version("Gst", "1.0")
        gi.require_version("GstApp", "1.0")
        from gi.repository import Gst, GstApp  # noqa: F401 - GstApp adds the appsink methods

        Gst.init(None)
        pipe = Gst.parse_launch(
            f"pipewiresrc fd={fd} path={node} always-copy=true do-timestamp=true ! "
            "videoconvert ! video/x-raw,format=BGRx ! "
            "appsink name=sink max-buffers=1 drop=true sync=false"
        )
        pos = props.get("position") or (0, 0)
        size = props.get("size") or (0, 0)
        stream = {"pipe": pipe, "sink": pipe.get_by_name("sink"), "fd": fd,
                  "condition": threading.Condition(), "closed": False,
                  "x": pos[0], "y": pos[1], "w": size[0], "h": size[1]}
        def sample_ready(sink):
            sample = sink.pull_sample()
            with stream["condition"]:
                stream["last"] = sample
                stream["received_at"] = time.monotonic()
                stream["condition"].notify_all()
            return Gst.FlowReturn.OK
        stream["sink"].set_property("emit-signals", True)
        stream["sink"].connect("new-sample", sample_ready)
        if pipe.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            pipe.set_state(Gst.State.NULL)
            os.close(fd)
            raise CaptureError("Cannot start PipeWire screen capture")
        return stream

    # -- frames ---------------------------------------------------------
    def grab(self, x: int, y: int, w: int, h: int, native: bool = False):
        from PIL import Image

        cx, cy = x + w // 2, y + h // 2
        match = None
        for st in self.streams:
            if st["w"] and st["x"] <= cx < st["x"] + st["w"] and st["y"] <= cy < st["y"] + st["h"]:
                match = st
        if match is None and len(self.streams) == 1:
            only = self.streams[0]
            # No position reported: the stream is taken to start at the origin,
            # which is only true when it covers the whole region asked for.
            if not only["w"] or (x >= 0 and y >= 0 and x + w <= only["w"] and y + h <= only["h"]):
                match = only
            else:
                self.reset_consent()
                raise CaptureError(
                    f"the screen share chosen in the portal dialog is only {only['w']}x{only['h']}: "
                    "choose a whole screen, not a window or region (the choice was reset, "
                    "the dialog will be shown again)"
                )
        if match is None:
            raise CaptureError("region is not on any monitor shared with the capture portal")
        if match["w"] and match["h"] and not (
            match["x"] <= x and match["y"] <= y
            and x + w <= match["x"] + match["w"] and y + h <= match["y"] + match["h"]
        ):
            raise CaptureError("capture region must fit entirely within one shared monitor")
        from gi.repository import Gst

        after = getattr(_frame_request, "after", None)
        sample = self._latest_sample(match, after)
        caps = sample.get_caps().get_structure(0)
        fw, fh = caps.get_value("width"), caps.get_value("height")
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if not ok:
            raise CaptureError("cannot map capture frame")
        try:
            import gi
            gi.require_version("GstVideo", "1.0")
            from gi.repository import GstVideo

            video_info = GstVideo.VideoInfo.new_from_caps(sample.get_caps())
            frame = Image.frombytes("RGB", (fw, fh), bytes(info.data), "raw", "BGRX", video_info.stride[0], 1)
        finally:
            buf.unmap(info)
        sx = fw / match["w"] if match["w"] else 1.0
        sy = fh / match["h"] if match["h"] else 1.0
        box = (
            max(0, round((x - match["x"]) * sx)), max(0, round((y - match["y"]) * sy)),
            min(fw, round((x - match["x"] + w) * sx)), min(fh, round((y - match["y"] + h) * sy)),
        )
        if box[2] <= box[0] or box[3] <= box[1]:
            raise CaptureError("region lies outside the captured monitor")
        img = frame.crop(box).convert("RGB")
        return img if native or img.size == (w, h) else img.resize((w, h))

    @staticmethod
    def _latest_sample(stream, after=None):
        """Static desktops reuse the last frame immediately; clean capture waits for new pixels."""
        deadline = time.monotonic() + _FRAME_TIMEOUT_S
        with stream["condition"]:
            while not stream.get("closed"):
                sample = stream.get("last")
                if sample is not None and (after is None or stream.get("received_at", 0) >= after):
                    return sample
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CaptureError("No fresh frame arrived after hiding the overlay")
                stream["condition"].wait(remaining)
        raise CaptureError("Screen capture session closed")

    def reset_consent(self) -> None:
        """Forget the remembered choice so the next capture asks again."""
        try:
            self._token_file().unlink()
        except OSError:
            pass
        self.close()

    def close(self) -> None:
        from gi.repository import Gst

        for st in self.streams:
            with st["condition"]:
                st["closed"] = True
                st["condition"].notify_all()
            st["pipe"].set_state(Gst.State.NULL)
            os.close(st["fd"])
        self.streams = []
        if self._bus and self.session_handle:
            try:
                self._bus.call_sync(
                    _PORTAL, self.session_handle, "org.freedesktop.portal.Session", "Close",
                    None, None, 0, 1000, None,
                )
            except Exception:  # noqa: BLE001
                pass
        self.session_handle = None


_session = _PortalSession()


def portal_capture(geom: str, native: bool = False):
    """Grab a region through the authorized XDG ScreenCast portal.

    Blocking: call from a capture worker, never from the GTK main loop (the
    first call may wait for the user to answer the consent dialog).
    """
    x, y, w, h = parse_geom(geom)
    with _session.lock:
        if _session.denied:
            raise CaptureError(_session.denied)
        if not _session.streams:
            try:
                _session.start()
            except CaptureError as exc:
                _session.close()
                if "denied" in str(exc):
                    _session.denied = str(exc)
                raise
            except Exception as exc:  # noqa: BLE001
                _session.close()
                raise CaptureError(f"screen capture portal failed: {exc}") from None
            atexit.register(close_portal_capture)
        return _session.grab(x, y, w, h, native)


def close_portal_capture() -> None:
    """Release the PipeWire streams and portal session (call on shutdown)."""
    with _session.lock:
        _session.close()


def cancel_capture_request() -> None:
    """Cancel a pending consent request without blocking the GUI on the session lock."""
    _session.cancelled.set()


def portal_ready() -> bool:
    return bool(_session.streams)


def grab_to_file(geom: str, path: str) -> int:
    """Save a region at the monitor's native resolution; used by the selector.

    Run as ``python -m kizurium_translator.capture GEOM PATH`` by the QML
    overlay on desktops where grim cannot capture (KDE). Exit code 0 = file
    written; anything else is reported on stderr and the overlay says so.
    """
    try:
        portal_capture(geom, native=True).save(path, "PNG")
    except (CaptureError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: python -m kizurium_translator.capture 'x,y WxH' out.png", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(grab_to_file(sys.argv[1], sys.argv[2]))
