"""Detect the running Wayland compositor (tips and env recovery).

Capture and overlay use grim + gtk4-layer-shell on Hyprland, Niri,
MangoWC, Sway, and similar.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class CompositorInfo:
    id: str  # hyprland | niri | mango | sway | river | unknown
    label: str
    env_hint: str = ""


def detect() -> CompositorInfo:
    """Best-effort guess from environment variables set by the compositor."""
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return CompositorInfo("hyprland", "Hyprland", "HYPRLAND_INSTANCE_SIGNATURE")
    if os.environ.get("NIRI_SOCKET") or os.environ.get("NIRI_SESSION"):
        return CompositorInfo("niri", "Niri", "NIRI_SOCKET")
    if os.environ.get("MANGO_INSTANCE_SIGNATURE") or os.environ.get("MANGOWC"):
        return CompositorInfo("mango", "MangoWC", "MANGO_INSTANCE_SIGNATURE")
    if os.environ.get("SWAYSOCK"):
        return CompositorInfo("sway", "Sway", "SWAYSOCK")
    if os.environ.get("RIVER_WAYLAND_DISPLAY") or os.environ.get("RIVER_CONTROL_PATH"):
        return CompositorInfo("river", "River", "")
    desk = (os.environ.get("XDG_CURRENT_DESKTOP") or "").casefold()
    if "hypr" in desk:
        return CompositorInfo("hyprland", "Hyprland", "XDG_CURRENT_DESKTOP")
    if "niri" in desk:
        return CompositorInfo("niri", "Niri", "XDG_CURRENT_DESKTOP")
    if "mango" in desk:
        return CompositorInfo("mango", "MangoWC", "XDG_CURRENT_DESKTOP")
    if "sway" in desk:
        return CompositorInfo("sway", "Sway", "XDG_CURRENT_DESKTOP")
    if os.environ.get("WAYLAND_DISPLAY"):
        return CompositorInfo("unknown", "Wayland", "WAYLAND_DISPLAY")
    return CompositorInfo("none", "no Wayland session", "")


def hotkey_snippet(comp: CompositorInfo | None = None) -> str:
    """Copy-paste bind for the detected compositor."""
    c = comp or detect()
    if c.id == "hyprland":
        return (
            "# ~/.config/hypr/hyprland.lua\n"
            'hl.bind("SUPER + SHIFT + T", hl.dsp.exec_cmd("kizurium-translator --toggle"))\n'
            "\n"
            "# or hyprland.conf:\n"
            "# bind = SUPER_SHIFT, T, exec, kizurium-translator --toggle\n"
        )
    if c.id == "niri":
        return (
            "// ~/.config/niri/config.kdl  (inside binds {... })\n"
            'Mod+Shift+T { spawn "kizurium-translator" "--toggle"; }\n'
        )
    if c.id == "mango":
        return (
            "# ~/.config/mango/config.conf\n"
            "bind=SUPER+SHIFT,t,spawn,kizurium-translator --toggle\n"
        )
    if c.id == "sway":
        return (
            "# ~/.config/sway/config\n"
            "bindsym $mod+Shift+t exec kizurium-translator --toggle\n"
        )
    return "# kizurium-translator --toggle\n"


def recover_compositor_env() -> None:
    """Restore compositor env vars if a keybind stripped them."""
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        hypr = os.path.join(runtime, "hypr")
        try:
            entries = sorted(os.listdir(hypr))
        except OSError:
            entries = []
        for name in entries:
            if os.path.exists(os.path.join(hypr, name, ".hisig")):
                os.environ["HYPRLAND_INSTANCE_SIGNATURE"] = name
                break
    if not os.environ.get("NIRI_SOCKET"):
        try:
            for name in os.listdir(runtime):
                if name.startswith("niri.") and name.endswith(".sock"):
                    os.environ.setdefault("NIRI_SOCKET", os.path.join(runtime, name))
                    break
        except OSError:
            pass
    if not os.environ.get("MANGO_INSTANCE_SIGNATURE"):
        try:
            for name in os.listdir(runtime):
                if name.startswith("mango-") or name.startswith("mangowc"):
                    path = os.path.join(runtime, name)
                    if os.path.exists(path):
                        os.environ.setdefault("MANGO_INSTANCE_SIGNATURE", path)
                        break
        except OSError:
            pass
