"""The card has to be the same shape as the text it replaces.

Two ways a card failed to cover what it was drawn over, both found by
photographing the overlay on a game codex screen and measuring the result
rather than reading the log.

The first is geometry. A four-line entry was read as three lines and a separate
one-word line, and two cards fought over the same ground: the paragraph's card
was squeezed to half its height to stay clear of the one-word card, so two lines
of English stayed uncovered. The short line is the paragraph's own last line,
and folding it back in is what makes one card for one paragraph.

The second is the split. "Chip then banner" describes two chips the recogniser
joined on one line, and the pattern matches it finds - capitalised words then a
long sentence - matches the start of every paragraph, because every sentence
starts with a capital. It cut "Inactive Safe" off the front of a codex entry and
moved the rest of it 140px right, so the left of every line stayed uncovered.

Both are checked here on the shapes they were found on, and both are checked in
the direction that matters: the paragraph must survive them.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from _where import source_text

from kizurium_translator import live  # noqa: E402


def line(text: str, x1: int, y1: int, x2: int, y2: int) -> dict:
    return {"text": text, "box": (x1, y1, x2, y2), "line_height": y2 - y1}


PARA_LINES = [
    line("Inactive Safe Are as appear as blue pillars of", 613, 499, 1229, 518),
    line("beacons serve as important landmarks when prog", 613, 529, 1238, 546),
    line("through unexplored are as- its a good idea to h", 613, 554, 1236, 573),
]
PARA = {
    "text": (
        "Inactive Safe Are as appear as blue pillars of light. These beacons serve "
        "as important landmarks when progressing through unexplored areas"
    ),
    "box": (613, 499, 1238, 573),
    "line_boxes": PARA_LINES,
    "conf": 62.0,
}
FIRST = {"text": "first", "box": (649, 577, 706, 606), "line_height": 29}
CLOSE = {"text": "Close", "box": (913, 876, 973, 901), "line_height": 25}
TITLE = {
    "text": "Overworld Exploration: Blue Pillars of Light",
    "box": (666, 459, 1132, 484),
}


class TestTheShortTailGoesBackIntoTheParagraph:
    def test_the_tail_is_folded_in(self):
        out = live.merge_paragraph_tail([PARA, FIRST, CLOSE])
        assert FIRST not in out, "the last line became its own card again"
        assert len(out) == 2, [o["text"][:30] for o in out]

    def test_the_paragraph_grows_to_reach_it(self):
        out = live.merge_paragraph_tail([PARA, FIRST, CLOSE])
        para = next(o for o in out if len(o.get("line_boxes") or []) == 4)
        assert para["box"][3] >= FIRST["box"][3], para["box"]

    def test_the_text_gains_the_line(self):
        out = live.merge_paragraph_tail([PARA, FIRST, CLOSE])
        para = next(o for o in out if len(o.get("line_boxes") or []) == 4)
        assert para["text"].endswith("first")

    def test_a_button_below_is_not_folded_in(self):
        out = live.merge_paragraph_tail([PARA, CLOSE])
        assert any(o["text"] == "Close" for o in out)

    def test_a_line_with_a_gap_is_not_folded_in(self):
        far = {"text": "Settings", "box": (649, 700, 760, 730)}
        out = live.merge_paragraph_tail([PARA, far])
        assert any(o["text"] == "Settings" for o in out)

    def test_a_long_line_below_is_not_folded_in(self):
        long_line = {"text": "beacons serve as important landmarks", "box": (613, 578, 1237, 606)}
        out = live.merge_paragraph_tail([PARA, long_line])
        assert any("beacons" in o["text"] for o in out)

    def test_a_line_further_right_is_not_folded_in(self):
        """A label in the margin is not the paragraph's last line."""
        side = {"text": "Open", "box": (1500, 580, 1560, 606)}
        out = live.merge_paragraph_tail([PARA, side])
        assert any(o["text"] == "Open" for o in out)

    def test_a_single_line_block_is_never_grown(self):
        out = live.merge_paragraph_tail([TITLE, FIRST])
        assert len(out) == 2

    def test_nothing_happens_to_one_block(self):
        assert len(live.merge_paragraph_tail([PARA])) == 1


class TestTouching:
    def test_touching_lines_are_one_run(self):
        assert live._boxes_touch((613, 554, 1236, 573), (649, 577, 706, 606))

    def test_a_gap_ends_the_run(self):
        assert not live._boxes_touch((613, 554, 1236, 573), (649, 700, 706, 730))

    def test_no_horizontal_overlap_is_not_one_run(self):
        assert not live._boxes_touch((613, 554, 1236, 573), (1600, 577, 1700, 606))


class TestTheBannerSplitLeavesParagraphsAlone:
    def test_a_paragraph_is_not_split_into_chip_and_banner(self):
        out = live.split_title_banner_merges([PARA])
        assert len(out) == 1, out

    def test_the_paragraph_text_is_untouched(self):
        out = live.split_title_banner_merges([PARA])
        assert out[0]["text"] == PARA["text"]
        assert out[0]["box"] == PARA["box"]

    def test_the_real_chip_and_banner_case_still_splits(self):
        chip = {
            "text": "Start Quest Begin an incomplete journey through the ruins",
            "box": (200, 400, 900, 430),
        }
        out = live.split_title_banner_merges([chip])
        assert len(out) == 2, [o["text"][:24] for o in out]

    def test_the_split_keeps_the_chip_in_its_own_box(self):
        chip = {
            "text": "Start Quest Begin an incomplete journey through the ruins",
            "box": (200, 400, 900, 430),
        }
        head, rest = live.split_title_banner_merges([chip])
        assert head["text"] == "Start Quest"
        assert head["box"][2] < rest["box"][0]

    def test_a_plain_heading_is_not_split(self):
        out = live.split_title_banner_merges([TITLE])
        assert len(out) == 1


class TestTheCardCoversTheText:
    def test_a_card_is_drawn_opaque(self):
        """A card exists to replace what is under it."""
        import ast

        src = source_text("draw_blocks")
        assert "clamp(float(bg_a), 0.985, 1.0)" in src, (
            "the alpha floor came back down; the original will read through the "
            "translation again"
        )

    def test_the_layout_is_checked_against_the_original_height(self):
        cairo = pytest.importorskip("cairo")
        cr = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 100, 100))
        text = (
            "Неактивные сейфы выглядят как синие столбы света. "
            "Эти маяки служат важными ориентирами"
        )
        fitted = live.fit_layout(cr, text, 625, 107, 18, keep_breaks=True)
        assert fitted is not None
        _layout, w, h, _px, _py = fitted
        assert w >= 625, (w, "the card is narrower than the text it covers")
        assert h >= 107, (h, "the card is shorter than the text it covers")

    def test_a_short_translation_still_fits_the_original(self):
        cairo = pytest.importorskip("cairo")
        cr = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 100, 100))
        fitted = live.fit_layout(cr, "Короткая подпись", 625, 107, 18, keep_breaks=True)
        assert fitted is not None
        _layout, w, h, _px, _py = fitted
        assert w == 625
        assert h >= 107


class TestStructure:
    def test_the_module_still_parses(self):
        import ast

        ast.parse(source_text("draw_blocks"))
