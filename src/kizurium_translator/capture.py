"""Screen capture.

Region selection is not here: it is the Quickshell overlay in selector.py.
What remains uses only standard wlroots tools, so any compositor that supports
layer-shell works:

* ``grim -g "x,y WxH" -``  capture a region straight into memory
* ``slurp -o -r``          pick one whole output, for --output
"""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from PIL import Image

GEOM_RE = re.compile(r"^\s*(-?\d+),(-?\d+)\s+(\d+)x(\d+)\s*$")


class CaptureError(RuntimeError):
    pass


def ensure_wayland_env() -> None:
    """Fill in the Wayland variables a compositor-spawned process may not have.

    A keybind runs the command through the compositor, and the compositor's
    environment is not guaranteed to carry WAYLAND_DISPLAY. Without it grim and
    quickshell both fail: the region selector simply did nothing when launched
    from the keybind, while working when started from a shell. So the variables
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
