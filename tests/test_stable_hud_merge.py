""": stable HUD is preserved when dynamic dialogue changes."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.live.reconcile import (  # noqa: E402
    is_stable_hud_card,
    merge_incremental_cards,
)


def _card(text: str, *, kind: str, x: int, y: int, ru: str | None = None) -> dict:
    return {
        "x": x,
        "y": y,
        "src_w": 120,
        "src_h": 24,
        "source": text,
        "text": ru or text,
        "kind": kind,
}


def test_stable_hud_classifier():
    assert is_stable_hud_card(_card("Settings", kind="ui", x=10, y=10))
    assert not is_stable_hud_card(
        _card("I see... Pleased to meet you", kind="dialogue", x=10, y=200)
)


def test_dialogue_change_keeps_stable_hud_card():
    """Acceptance: A (HUD) untouched when only B (dialogue) is reprocessed."""
    hud = _card("Settings", kind="ui", x=20, y=20, ru="Настройки")
    old_dlg = _card("Hello there friend", kind="dialogue", x=40, y=300, ru="Привет")
    new_dlg = _card("Goodbye for now", kind="dialogue", x=40, y=300, ru="Пока")
    # Fresh OCR returned only the new dialogue line (HUD missed this pass).
    merged = merge_incremental_cards(
        [hud, old_dlg],
        [new_dlg],
        new_source_fps={"settings", "goodbye for now"},
)
    texts = {(c.get("source"), c.get("kind")) for c in merged}
    assert ("Settings", "ui") in texts
    assert ("Goodbye for now", "dialogue") in texts
    # HUD translation object identity / text preserved when kept.
    kept_hud = next(c for c in merged if c.get("kind") == "ui")
    assert kept_hud.get("text") == "Настройки"
