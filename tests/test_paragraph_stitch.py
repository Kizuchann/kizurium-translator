"""A paragraph arrives one line at a time and has to be put back together.

The recogniser hands back a box per line. Nothing in the row grouping gathers
them, because consecutive lines of a paragraph are a whole line height apart and
that grouping's tolerance is a fraction of one — so each line became its own
card, and a sentence came back as several, its punctuation split across two of
them and its intonation gone.

The cases below are the ones that decide whether the stitch is safe. Each of
them is a way it went wrong first.
"""

from __future__ import annotations

from kizurium_translator.live import _continues_flow, stitch_paragraph_lines


def line(text, x1, y1, x2, y2, angle=0.0, line_height=None):
    return {
        "text": text,
        "box": (x1, y1, x2, y2),
        "line_height": line_height if line_height is not None else (y2 - y1),
        "conf": 99.0,
        "angle": angle,
        "engine": "rapidocr",
    }


# ------------------------------------------------------------ it stitches


def test_five_lines_of_a_sentence_become_one_block():
    lines = [
        line("These are your equipment", 469, 373, 924, 420),
        line("slots, picking up an", 528, 406, 916, 451),
        line("equippable item will", 523, 435, 865, 478),
        line("automatically equip that", 486, 468, 904, 508),
        line("item if a slot is empty.", 504, 495, 880, 538),
    ]
    out = stitch_paragraph_lines(lines)
    assert len(out) == 1
    assert out[0]["text"] == (
        "These are your equipment slots, picking up an equippable item will "
        "automatically equip that item if a slot is empty."
    )
    assert out[0]["wrapped_lines"] == 5


def test_the_lines_come_back_in_reading_order_and_not_left_to_right():
    """Every line of this one starts at a different x. Sorted by x it is nonsense."""
    lines = [
        line("Some items, like Helmets,", 472, 528, 916, 567),
        line("can not be equipped more", 469, 558, 916, 598),
        line("than once.", 604, 591, 786, 625),
    ]
    out = stitch_paragraph_lines(lines)
    assert len(out) == 1
    assert out[0]["text"] == "Some items, like Helmets, can not be equipped more than once."


def test_a_block_takes_the_width_of_the_widest_line_and_the_height_of_all():
    lines = [
        line("These are your equipment", 469, 373, 924, 420),
        line("slots, picking up an", 528, 406, 916, 451),
        line("equippable item will", 523, 435, 865, 478),
        line("automatically equip that", 486, 468, 904, 508),
        line("item if a slot is empty.", 504, 495, 880, 538),
    ]
    box = stitch_paragraph_lines(lines)[0]["box"]
    assert box == (469, 373, 924, 538)


# ---------------------------------------------------------- it does not


def test_a_list_of_labels_spaced_apart_is_not_a_paragraph():
    """The ws 6 right-hand column, which is a list and not a block of text.

    Every one of these would pass on width, on stacking, and on not ending a
    sentence. What separates them from a paragraph is the spacing: a list puts
    its items more than a line height apart, and a paragraph puts its lines
    closer than that.
    """
    lines = [
        line("Mob Bestiary", 1597, 175, 1791, 216),
        line("Player Stats", 1504, 241, 1714, 277),
        line("Extra Damage:", 1438, 294, 1611, 325),
    ]
    assert len(stitch_paragraph_lines(lines)) == 3


def test_neighbours_side_by_side_are_never_stitched():
    """Two items on one row are a hair apart vertically, so a gap test alone
    would call every pair of neighbours a paragraph."""
    assert not _continues_flow(
        line("Expertise", 1191, 183, 1330, 214),
        line("Spell Book", 966, 180, 1122, 216),
    )


def test_a_line_that_ends_a_sentence_starts_something_new():
    """The test a reader uses: a full stop means the sentence has finished."""
    assert not _continues_flow(
        line("item if a slot is empty.", 504, 495, 880, 538),
        line("These are your equipment", 469, 373, 924, 420),
    )


def test_a_slanted_row_is_left_to_its_own_geometry():
    """On the Muse Dash results screen the judgement labels sit on a slope, and
    GREAT/MISS and PERFECT/PASS overlap in width, sit within half a line of each
    other, and end in letters rather than punctuation — so they pass every other
    test here. A slanted line is a line, not a block of text."""
    assert not _continues_flow(
        line("MISS", 406, 876, 501, 940, angle=13.1),
        line("GREAT", 426, 840, 538, 906, angle=13.1),
    )


def test_two_lines_far_apart_in_height_are_two_blocks():
    lines = [
        line("SECRET", 10, 20, 400, 60),
        line("far away", 10, 400, 400, 440),
    ]
    assert len(stitch_paragraph_lines(lines)) == 2


def test_one_line_is_returned_untouched():
    only = [line("Tasks", 1432, 181, 1524, 213)]
    assert stitch_paragraph_lines(only) == only
