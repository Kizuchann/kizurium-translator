""": clean capture sync — paint barrier, sleep only as safety fallback."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.live import snap as snap_mod  # noqa: E402
from kizurium_translator.live.snap import (  # noqa: E402
    CAPTURE_PAINT_TIMEOUT_S,
    CAPTURE_SAFETY_SLEEP_S,
    clean_capture,
)


class _FakeState:
    def __init__(self) -> None:
        self.hidden = False
        self.waited: list[float] = []
        self.shown = False

    def hide(self) -> None:
        self.hidden = True

    def show(self) -> None:
        self.hidden = False
        self.shown = True

    def wait_hidden_paint(self, timeout: float = 0.35) -> None:
        self.waited.append(timeout)


def test_phase38_constants():
    assert CAPTURE_PAINT_TIMEOUT_S > 0
    assert CAPTURE_SAFETY_SLEEP_S < CAPTURE_PAINT_TIMEOUT_S


def test_clean_capture_waits_paint_before_grim(monkeypatch):
    order: list[str] = []
    st = _FakeState()

    def fake_grim(geom):
        order.append("grim")
        return Image.new("RGB", (8, 8), "black")

    def fake_wait(timeout=0.35):
        order.append(f"wait:{timeout}")
        st.waited.append(timeout)

    st.wait_hidden_paint = fake_wait
    monkeypatch.setattr(snap_mod, "grim_region", fake_grim)
    monkeypatch.setattr(snap_mod.time, "sleep", lambda s: order.append(f"sleep:{s}"))

    got = clean_capture(st, "0,0 8x8", paint_timeout=0.2, safety_sleep=0.01, restore=False)
    assert got.image is not None
    assert order[0].startswith("wait:")
    assert "grim" in order
    assert order.index("wait:0.2") < order.index("grim")
    # Safety sleep may run, but paint wait is first.
    assert st.hidden is True
    assert st.shown is False


def test_clean_capture_restore_shows_again(monkeypatch):
    st = _FakeState()
    monkeypatch.setattr(
        snap_mod, "grim_region", lambda g: Image.new("RGB", (4, 4), "white")
)
    monkeypatch.setattr(snap_mod.time, "sleep", lambda s: None)
    got = clean_capture(st, "0,0 4x4", paint_timeout=0.05, safety_sleep=0, restore=True)
    assert got.was_hidden is False
    assert st.shown is True
    assert st.hidden is False
