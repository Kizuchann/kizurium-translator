"""Compositor detection and hotkey snippets."""

from __future__ import annotations

import os

from kizurium_translator.compositor import detect, hotkey_snippet, recover_compositor_env


def test_detect_hyprland(monkeypatch):
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "abc")
    monkeypatch.delenv("NIRI_SOCKET", raising=False)
    monkeypatch.delenv("MANGO_INSTANCE_SIGNATURE", raising=False)
    info = detect()
    assert info.id == "hyprland"
    snip = hotkey_snippet(info)
    assert "hl.bind" in snip
    assert "kizurium-translator --toggle" in snip


def test_detect_niri(monkeypatch):
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)
    monkeypatch.setenv("NIRI_SOCKET", "/run/user/1000/niri.sock")
    info = detect()
    assert info.id == "niri"
    snip = hotkey_snippet(info)
    assert "config.kdl" in snip
    assert 'spawn "kizurium-translator"' in snip


def test_detect_mango(monkeypatch):
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)
    monkeypatch.delenv("NIRI_SOCKET", raising=False)
    monkeypatch.setenv("MANGO_INSTANCE_SIGNATURE", "mango-1")
    info = detect()
    assert info.id == "mango"
    snip = hotkey_snippet(info)
    assert "SUPER+SHIFT" in snip
    assert "config.conf" in snip


def test_detect_unknown_wayland(monkeypatch):
    for key in (
        "HYPRLAND_INSTANCE_SIGNATURE",
        "NIRI_SOCKET",
        "NIRI_SESSION",
        "MANGO_INSTANCE_SIGNATURE",
        "MANGOWC",
        "SWAYSOCK",
        "XDG_CURRENT_DESKTOP",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    info = detect()
    assert info.id == "unknown"
    assert "Wayland" in info.label


def test_recover_does_not_invent_hyprland(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    for key in (
        "HYPRLAND_INSTANCE_SIGNATURE",
        "NIRI_SOCKET",
        "MANGO_INSTANCE_SIGNATURE",
    ):
        monkeypatch.delenv(key, raising=False)
    recover_compositor_env()
    assert "HYPRLAND_INSTANCE_SIGNATURE" not in os.environ
