"""A paragraph keeps the rows the original had.

Found on a character sheet (ws 9/11) and a codex panel (ws 6): the original was
two lines of type on a sheet, and the translation came out as one condensed
line of unreadable type across the top of a card that was built for two. The
paragraph was read as rows and stitched into one card, and the rows were thrown
away with the rest of the stitching, so the card's line budget came from the
newlines in the translation - of which there are none, because a translator
returns a sentence.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.core.text import (
    pack_translation_to_rows,
    row_pitch,
    source_rows,
)


def _rows(n: int = 2, top: int = 928, pitch: int = 55, width: int = 1300):
    return [
        {
            "text": "Zombie Girl can enter Dying Mode for 15 seconds",
            "box": (235, top + i * pitch, 235 + width, top + i * pitch + 44),
        }
        for i in range(n)
    ]


def test_a_stitched_paragraph_still_knows_its_rows():
    from kizurium_translator.layout.grouping import stitch_ui_body_paragraphs

    # Two lines of a panel description, as the recogniser returns them.
    stitched = stitch_ui_body_paragraphs(
        [
            {
                "text": "Zombie Girl can enter Dying Mode for 15 seconds when HP "
                "falls to the minimum.",
                "box": (235, 928, 1535, 966),
                "kind": "ui",
            },
            {
                "text": "She cannot enter Dying Mode again if not collecting any "
                "red hearts after quitting",
                "box": (235, 983, 1500, 1021),
                "kind": "ui",
            },
        ]
    )
    assert len(stitched) == 1, "a wrapped sentence is one card, not two"
    par = stitched[0]
    assert par["kind"] == "body"
    # The rows survive the stitch as geometry, so nothing downstream has to
    # guess how many lines the paragraph had.
    rows = source_rows(par)
    assert len(rows) == 2
    assert rows[0]["box"][1] < rows[1]["box"][1]
    assert row_pitch(rows) == 55
    # Geometry, not the dialogue path: these rows must not split the panel.
    assert not par.get("line_boxes")


def test_rows_are_the_line_budget_not_the_newlines():
    # Two rows of source, one sentence of translation: the card is budgeted two
    # lines.
    par = {"text": "One long sentence. Another.", "src_rows": _rows(2)}
    assert len(source_rows(par)) == 2


def test_the_rows_keep_the_original_leading():
    # The rows carry the game's own leading, which is what the translation has
    # to be set with. Divided by the row count instead it would average a
    # paragraph and a short line after it into one number.
    assert row_pitch(_rows(2, pitch=55)) == 55
    assert row_pitch(_rows(3, pitch=40)) == 40
    assert row_pitch(_rows(1)) == 0
    assert row_pitch([]) == 0


def test_one_odd_row_does_not_become_the_leading():
    # A descender or a box that caught half a line: one gap is not the leading.
    # Four rows, gaps of 40, 40 and 52 - two of the three say 40.
    rows = [
        {"text": "row one", "box": (235, 928, 1535, 968)},
        {"text": "row two", "box": (235, 968, 1535, 1008)},
        {"text": "row three", "box": (235, 1008, 1535, 1048)},
        {"text": "row four", "box": (235, 1060, 1535, 1100)},
    ]
    assert row_pitch(rows) == 40


def test_two_gaps_take_the_middle_not_the_bigger_one():
    # With an even number of gaps there is no middle row, and taking the larger
    # of the two makes the paragraph's leading the gap under a heading.
    rows = [
        {"text": "row one", "box": (235, 928, 1535, 968)},
        {"text": "row two", "box": (235, 968, 1535, 1008)},
        {"text": "row three", "box": (235, 1038, 1535, 1078)},
    ]
    assert row_pitch(rows) == 55


def test_a_translation_is_split_across_the_rows_it_came_from():
    rows = _rows(2)
    parts = pack_translation_to_rows(rows, "Девушка-зомби может войти в режим смерти на несколько секунд, когда HP падает до минимума.")
    assert len(parts) == 2
    # Nothing is dropped and nothing is invented between the parts.
    assert " ".join(parts).split() == (
        "Девушка-зомби может войти в режим смерти на несколько секунд, "
        "когда HP падает до минимума."
    ).split()


def test_a_short_translation_still_fills_every_row():
    # Two words on two rows: one each, rather than both on the first row and a
    # bare English line under it.
    parts = pack_translation_to_rows(_rows(2), "Да Нет")
    assert parts == ["Да", "Нет"]


def test_a_single_row_is_the_whole_translation():
    parts = pack_translation_to_rows(_rows(1), "Одна строка целиком")
    assert parts == ["Одна строка целиком"]


def test_a_card_for_a_two_row_paragraph_is_budgeted_two_lines():
    """The contract that failed on the screen: the card's own line budget."""
    import gi

    gi.require_version("Pango", "1.0")
    import cairo

    from kizurium_translator.render.cards import fit_layout

    # The original: two lines, 55 apart, in a 1357x114 box, type about 30.
    block = fit_layout(
        cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 2000, 1200)),
        "Первая часть перевода на две строки\nВторая часть перевода",
        1357,
        114,
        43,
        oneline=False,
        keep_breaks=True,
        tight=True,
        max_lines=2,
        line_pitch=55,
    )
    assert block is not None
    layout = block[0]
    lines = int(layout.get_line_count())
    # Not one condensed row of unreadable type, and not a paragraph that grew
    # past the two lines the original had.
    assert 2 <= lines <= 2, f"{lines} lines laid out for a two-line original"
    font_size = layout.get_font_description().get_size() / 1024.0
    assert font_size >= 16, f"type came out at {font_size}px for 30px source"


def test_the_leading_is_the_originals():
    """Two lines set at the distance the original kept between them."""
    import cairo
    import gi

    gi.require_version("Pango", "1.0")
    from kizurium_translator.render.cards import fit_layout

    cr = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 2000, 1200))
    text = "Первая строка перевода\nВторая строка перевода"
    loose = fit_layout(
        cr, text, 1200, 300, 40, oneline=False, keep_breaks=True, tight=True,
        max_lines=2, line_pitch=90,
    )[0]
    tight_layout = fit_layout(
        cr, text, 1200, 300, 40, oneline=False, keep_breaks=True, tight=True,
        max_lines=2, line_pitch=48,
    )[0]

    def line_top(layout, index: int) -> float:
        # A line's own extents are relative to its own origin, so every line
        # reports the same y. The layout's own index does not: it is where the
        # glyph would be drawn, which is what the leading is measured on.
        return layout.index_to_pos(layout.get_line_readonly(index).start_index).y / 1024.0

    loose_gap = line_top(loose, 1) - line_top(loose, 0)
    tight_gap = line_top(tight_layout, 1) - line_top(tight_layout, 0)
    # 90 asked for, 48 asked for: the leading follows the original, and a
    # bigger leading really does give bigger leading.
    assert loose_gap > tight_gap + 20
    assert 70 <= loose_gap <= 110, loose_gap
    assert 34 <= tight_gap <= 62, tight_gap