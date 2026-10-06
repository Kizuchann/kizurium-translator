""": global clear only on allowed reasons; never on incremental path."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.live.clear_policy import (  # noqa: E402
    ALLOWED_GLOBAL_CLEAR,
    FORBIDDEN_INCREMENTAL_CLEAR,
    may_global_clear,
)
from kizurium_translator.live.state import State  # noqa: E402


def test_phase46_allowed_reasons():
    assert ALLOWED_GLOBAL_CLEAR == {
        "scene_transition",
        "session_start",
        "session_stop",
        "empty_scene",
}
    for reason in ALLOWED_GLOBAL_CLEAR:
        assert may_global_clear(reason)
    for reason in FORBIDDEN_INCREMENTAL_CLEAR:
        assert not may_global_clear(reason)


def test_clear_denies_incremental_reasons():
    state = State()
    state.set([{"text": "HUD", "x": 1, "y": 1}], (0, 0, 100, 100), "")
    assert state.clear(reason="incremental") is False
    assert len(state.peek_blocks()) == 1
    assert state.clear(reason="empty_scene") is True
    assert state.peek_blocks() == []


def test_incremental_module_never_calls_clear():
    src = Path(__file__).resolve().parents[1] / "src/kizurium_translator/live/incremental.py"
    text = src.read_text(encoding="utf-8")
    assert "state.clear" not in text
    assert ".clear(" not in text or "paint_empty.clear" in text
