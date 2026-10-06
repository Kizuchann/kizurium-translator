""": game-agnostic semantic roles after visual grouping."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.ocr.roles import (  # noqa: E402
    PLAN_ROLES,
    SemanticRole,
    annotate_roles,
    classify_line_role,
)


def test_phase51_role_set():
    # DECOR arrived with: a strip of glyphs a profile declares to be
    # symbols rather than words. It is a role like the rest, and the plan's set
    # grows by exactly that one.
    assert PLAN_ROLES == {
        "DIALOGUE",
        "SPEAKER",
        "SUBTITLE",
        "HUD",
        "MENU",
        "NOTIFICATION",
        "ITEM",
        "TITLE",
        "BODY",
        "CHOICE",
        "DECOR",
        "UNKNOWN",
}


def test_classify_by_kind_and_shape():
    assert classify_line_role({"text": "Hello", "kind": "dialogue"}) == SemanticRole.DIALOGUE
    assert classify_line_role({"text": "Alice", "kind": "name"}) == SemanticRole.SPEAKER
    assert (
        classify_line_role(
            {"text": "Settings", "box": (10, 10, 90, 28), "kind": "hud"}
)
        == SemanticRole.HUD
)
    assert classify_line_role({"text": "Obtained!", "kind": "toast"}) == SemanticRole.NOTIFICATION


def test_annotate_roles_fills_kind():
    lines = annotate_roles(
        [{"text": "Chapter One", "box": (0, 0, 400, 40), "src_lines": 1}]
)
    assert lines[0]["semantic_role"] in PLAN_ROLES
    assert lines[0]["kind"]
