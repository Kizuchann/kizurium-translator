"""A name plate is one line, and a paragraph is not a speaker.

The ws16 quest description came through as one 1012x145 box with five rows of
30px leading. It sat just above another block, the pair had the shape of
speaker-over-body, and it was annotated `kind=name` / `oneline=True`. The card
then sized its type from the 145-pixel box - 134-pixel letters on a menu line -
and reached 300 pixels left of the text it was replacing, over its neighbour.

Nothing here knows about any game: a block with rows the recogniser measured,
or a box taller than one line's own height, is not a name plate.

    uv run pytest tests/test_name_plate_single_line.py
"""

from __future__ import annotations

from kizurium_translator.layout.name_gap import (
    is_name_above_body,
    is_name_dialogue_side_pair,
    is_single_line_block,
)

PLATE = {"text": "KIRITO", "box": (700, 700, 900, 730), "line_height": 30}
BODY = {
    "text": "Blade. They head to West Forest in search of the quest objective.",
    "box": (640, 738, 1600, 900),
    "line_height": 30,
}

# The same shape as PLATE, but a paragraph: five rows, 30px leading, 145 tall.
PARAGRAPH_ABOVE_BODY = {
    "text": "After successfully reaching Horunka, the party",
    "box": (726, 279, 1738, 424),
    "line_height": 29,
    "line_boxes": [
        {"box": (726, 279 + i * 30, 1738, 279 + 145), "line_height": 29}
        for i in range(5)
    ],
}


def test_a_name_plate_is_a_single_line():
    assert is_single_line_block(PLATE)


def test_five_rows_are_not_a_name_plate():
    assert is_single_line_block(PARAGRAPH_ABOVE_BODY) is False


def test_a_tall_box_with_a_line_height_is_not_a_name_plate():
    par = {"text": "x", "box": (700, 700, 1600, 845), "line_height": 29}
    assert is_single_line_block(par) is False


def test_stacked_paragraph_is_not_annotated_as_speaker():
    assert is_name_above_body(PARAGRAPH_ABOVE_BODY, BODY) is False


def test_stacked_plate_is_still_annotated_as_speaker():
    assert is_name_above_body(PLATE, BODY) is True


def test_side_pair_paragraph_is_not_a_speaker():
    left = dict(PARAGRAPH_ABOVE_BODY)
    right = dict(BODY)
    right["box"] = (1760, 279, 1900, 424)
    assert is_name_dialogue_side_pair(left, right) is False


def test_side_pair_plate_is_still_a_speaker():
    left = dict(PLATE)
    right = dict(BODY)
    right["box"] = (1000, 700, 1600, 900)
    assert is_name_dialogue_side_pair(left, right) is True
