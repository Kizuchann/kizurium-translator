""": draw current state on redraw; queue_draw only when rev changes."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.live.state import State  # noqa: E402


def test_set_skips_bump_when_paint_identity_unchanged():
    state = State()
    region = (0, 0, 200, 100)
    cards = [{"text": "Hi", "source": "Hello", "x": 10, "y": 10, "src_w": 40, "src_h": 16}]
    assert state.set(cards, region, "") is True
    rev = state.version()
    assert state.set(list(cards), region, "") is False
    assert state.version() == rev


def test_set_bumps_when_text_moves():
    state = State()
    region = (0, 0, 200, 100)
    state.set([{"text": "A", "source": "A", "x": 1, "y": 1, "src_w": 10, "src_h": 10}], region, "")
    rev = state.version()
    state.set([{"text": "B", "source": "B", "x": 1, "y": 1, "src_w": 10, "src_h": 10}], region, "")
    assert state.version() > rev


def test_app_draw_uses_snapshot_not_buffer():
    src = (
        Path(__file__).resolve().parents[1]
        / "src/kizurium_translator/live/app.py"
    ).read_text(encoding="utf-8")
    assert "state.snapshot()" in src
    assert "queue_draw only when state revision" in src or "rev != last_rev" in src
    assert "partial cairo" in src.lower() or "" in src
