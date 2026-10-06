"""One paragraph, one card.

A game codex entry read as seven blocks and drew six cards, three of them on
top of each other. Two things produced the pile-up, and both were the same
mistake in two places: the ink-band pass and the line pass each read the same
paragraph and the result was kept as though they were different paragraphs.

The two reads begin at the same line and stop at different ones - three lines
against two - so the longer read has everything the shorter one has and the
shorter is thrown away. Then the line pass offers the same paragraph one line at
a time, and those lines sit exactly on top of the kept paragraph.

Boxes alone cannot tell these apart. A box for three lines contains a box for
two of them, and half the time it contains a single line as well, so an overlap
test would delete real text. The first line can: it is the smallest thing two
reads can both be about. And even the first lines are not equal, because the two
engines disagreed at the tail of it - "...of light. These" against "...of
light. 1" - so they are compared on the part they share.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from _where import all_sources

from kizurium_translator import live  # noqa: E402


def line(text: str, x1: int, y1: int, x2: int, y2: int) -> dict:
    return {"text": text, "box": (x1, y1, x2, y2)}


def block(text: str, box: tuple[int, int, int, int], lines: list[dict] | None = None) -> dict:
    out = {"text": text, "box": box, "conf": 62.0}
    if lines is not None:
        out["line_boxes"] = lines
    return out


# The two reads, as the screen produced them. Same first line, same top edge,
# and the first read reaches one line further down.
READ_THREE = block(
    "Inactive Safe Are as appear as blue pillars of light. These beacons serve as "
    "important landmarks when progressing through unexplored are as—its a good idea "
    "to head there",
    (613, 499, 1238, 573),
    [
        line("Inactive Safe Are as appear as blue pillars of", 613, 499, 1229, 518),
        line("beacons serve as important landmarks when prog", 613, 529, 1238, 546),
        line("through unexplored are as—its a good idea to h", 613, 554, 1236, 573),
    ],
)
READ_TWO = block(
    "Inactive Safe Are as appear as blue pillars of light. 1 beacons serve as "
    "important landmarks when progr",
    (630, 499, 1167, 546),
    [
        line("Inactive Safe Are as appear as blue pillars of", 630, 499, 1167, 518),
        line("beacons serve as important landmarks when prog", 630, 529, 1167, 546),
    ],
)

# The line pass, one line at a time, plus the tail that the paragraph missed.
LINE_BEACONS = block(
    "beacons serve as important landmarks when progressing",
    (648, 523, 1237, 596),
)
LINE_THROUGH = block(
    "through unexplored are as—it's a good idea to head there",
    (651, 550, 1234, 577),
)
LINE_FIRST = block("first", (649, 577, 706, 606))

HEADING = block("Overworld Exploration: Blue Pillars of Light", (666, 459, 1132, 484))
CLOSE = block("Close", (913, 876, 973, 901))

SCREEN = [HEADING, READ_THREE, READ_TWO, LINE_BEACONS, LINE_THROUGH, LINE_FIRST, CLOSE]


class TestTwoReadsOfOneParagraph:
    def test_the_two_reads_are_recognised(self):
        assert live.same_paragraph_read(READ_THREE, READ_TWO)

    def test_the_shorter_read_is_dropped(self):
        out = live.dedupe_paragraph_reads(list(SCREEN))
        assert READ_TWO not in out

    def test_the_longer_read_is_kept(self):
        out = live.dedupe_paragraph_reads(list(SCREEN))
        assert READ_THREE in out

    def test_the_tail_the_paragraph_missed_survives(self):
        """"first" is the fourth line; the read stopped at three."""
        out = live.dedupe_paragraph_reads(list(SCREEN))
        assert LINE_FIRST in out, "the paragraph does not contain this line"

    def test_the_heading_above_and_the_button_below_survive(self):
        out = live.dedupe_paragraph_reads(list(SCREEN))
        assert HEADING in out
        assert CLOSE in out

    def test_the_whole_screen_becomes_four_blocks(self):
        assert len(live.dedupe_paragraph_reads(list(SCREEN))) == 4

    def test_nothing_survives_twice(self):
        out = live.dedupe_paragraph_reads(list(SCREEN))
        for a in out:
            for b in out:
                if a is b:
                    continue
                assert not live.same_paragraph_read(a, b)


class TestLinesInsideAParagraph:
    def test_a_line_of_the_paragraph_is_recognised(self):
        assert live.covered_by_paragraph(LINE_BEACONS, READ_THREE)
        assert live.covered_by_paragraph(LINE_THROUGH, READ_THREE)

    def test_a_line_the_paragraph_stops_short_of_is_not(self):
        assert not live.covered_by_paragraph(LINE_FIRST, READ_THREE)

    def test_the_heading_is_not_a_line_of_the_paragraph(self):
        assert not live.covered_by_paragraph(HEADING, READ_THREE)

    def test_the_button_is_not_a_line_of_the_paragraph(self):
        assert not live.covered_by_paragraph(CLOSE, READ_THREE)

    def test_the_last_line_survives_even_though_it_starts_in_the_band(self):
        """It starts 4px below the paragraph's end and says something else."""
        near = block("всё", (649, 570, 700, 596))
        assert not live.covered_by_paragraph(near, READ_THREE)


class TestWhatIsNotADuplicate:
    def test_two_columns_with_the_same_text_survive(self):
        """A file listing says the same thing in every row."""
        left = block("12 hours ago", (20, 100, 200, 120), [line("12 hours ago", 20, 100, 200, 120)])
        right = block("12 hours ago", (1000, 100, 1180, 120), [line("12 hours ago", 1000, 100, 1180, 120)])
        assert len(live.dedupe_paragraph_reads([left, right])) == 2

    def test_two_paragraphs_one_below_the_other_survive(self):
        first = block(
            "Alpha beta gamma delta epsilon zeta eta theta",
            (20, 100, 600, 120),
            [line("Alpha beta gamma delta epsilon zeta", 20, 100, 600, 120)],
        )
        second = block(
            "Iota kappa lambda mu nu xi omicron pi",
            (20, 140, 600, 160),
            [line("Iota kappa lambda mu nu xi omicron", 20, 140, 600, 160)],
        )
        assert len(live.dedupe_paragraph_reads([first, second])) == 2

    def test_the_same_first_words_on_far_apart_lines_survive(self):
        """Repetition is common; proximity is what makes it a duplicate."""
        a = block("Servers are online now", (20, 100, 400, 120), [line("Servers are online now", 20, 100, 400, 120)])
        b = block("Servers are online now", (20, 600, 400, 620), [line("Servers are online now", 20, 600, 400, 620)])
        assert len(live.dedupe_paragraph_reads([a, b])) == 2

    def test_a_block_without_line_boxes_is_never_a_duplicate_by_start(self):
        lonely = block("Something", (20, 100, 300, 120))
        assert not live.same_paragraph_read(lonely, lonely)
        assert not live.same_paragraph_read(lonely, READ_THREE)


class TestTheComparison:
    def test_punctuation_and_case_do_not_matter(self):
        a = line("Beacons serve AS important landmarks", 0, 0, 10, 10)
        b = line("beacons, serve as important landmarks.", 0, 0, 10, 10)
        assert live._line_compare_key(a) == live._line_compare_key(b)

    def test_short_shared_prefixes_are_not_enough(self):
        """Two short labels that begin alike are two labels."""
        assert not live._shares_its_start("close", "clone")
        assert live._shares_its_start("close the window now", "close the window now")

    def test_the_real_pair_shares_almost_everything(self):
        a = live._line_compare_key(READ_THREE["line_boxes"][0])
        b = live._line_compare_key(READ_TWO["line_boxes"][0])
        shared = 0
        for ca, cb in zip(a, b):
            if ca != cb:
                break
            shared += 1
        assert shared >= 38, (a, b)


class TestItIsWiredIn:
    def test_dedupe_near_ui_lines_applies_it(self):
        out = live.dedupe_near_ui_lines(list(SCREEN))
        assert READ_TWO not in out
        # The kept read is rebuilt once the tail is folded in, so it is matched
        # on what it says rather than on being the same object.
        assert any(
            len(o.get("line_boxes") or []) >= 3
            and "blue pillars" in str(o.get("text", ""))
            for o in out
        ), [o["text"][:30] for o in out]

    def test_a_single_block_is_returned_unchanged(self):
        assert len(live.dedupe_paragraph_reads([CLOSE])) == 1

    def test_an_empty_list_is_fine(self):
        assert live.dedupe_paragraph_reads([]) == []


class TestStructure:
    def test_the_module_still_parses(self):
        import ast

        for path in all_sources():
            ast.parse(path.read_text(encoding="utf-8"))
