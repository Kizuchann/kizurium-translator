"""Weight, role and line-count matching for overlay type."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.core.text import _role_for, weight_for_stroke
from kizurium_translator.fonts import ROLE_FACES, family_list


def _w(stroke: float, box_h: int = 24):
    return int(weight_for_stroke(stroke, box_h))


def test_thin_ink_is_not_bold():
    import gi

    gi.require_version("Pango", "1.0")
    from gi.repository import Pango

    assert _w(0.07) <= int(Pango.Weight.LIGHT)
    assert _w(0.10) == int(Pango.Weight.NORMAL)
    assert _w(0.13) == int(Pango.Weight.MEDIUM)
    assert _w(0.18) == int(Pango.Weight.BOLD)
    assert _w(0.22) >= int(Pango.Weight.HEAVY)


def test_name_plate_is_display_or_rounded():
    assert _role_for({
        "source": "Buro",
        "src_w": 220,
        "src_h": 78,
        "kind": "name",
        "stem": 0.20,
        "src_lines": 1,
    }) == "display"
    assert _role_for({
        "source": "Sonico",
        "src_w": 90,
        "src_h": 22,
        "kind": "dialogue",
        "italic": True,
        "src_lines": 1,
    }) == "serif"


def test_hud_label_is_score_not_body():
    assert _role_for({
        "source": "MAX COMBO",
        "src_w": 140,
        "src_h": 22,
        "kind": "ui",
        "stem": 0.14,
        "src_lines": 1,
    }) == "score"


def test_family_lists_end_in_cyrillic():
    for role in ROLE_FACES:
        names = family_list(role)
        assert "Noto Sans" in names, (role, names)


def test_two_line_box_does_not_wrap_to_three():
    cairo = pytest.importorskip("cairo")
    from kizurium_translator.render.cards import fit_layout

    cr = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 200, 80))
    text = (
        "Она любит сладкое и всегда носит с собой конфеты, "
        "а ещё умеет устраивать шум на любой сцене"
    )
    fitted = fit_layout(
        cr, text, 420, 52, 16, oneline=False, tight=True, keep_breaks=True, max_lines=2
    )
    assert fitted is not None
    layout, _w, _h, _px, _py = fitted
    assert layout.get_line_count() <= 2, layout.get_line_count()
