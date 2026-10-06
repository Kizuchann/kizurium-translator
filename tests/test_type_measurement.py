"""A line is measured by what is in it, not by how much room it takes up.

Type size, how many lines a box holds, and how heavy the lettering is: all three
are properties of the original that a translation can be matched to, and all
three were being guessed from the box instead. A 139-pixel character name came
out at a 42-pixel font in a card 145 tall - a large empty panel with a small line
of text at the top of it - because the font was capped by a constant and the
line count was read off the height against an assumed twenty-pixel line.
"""

from __future__ import annotations

from PIL import Image


def test_line_count_is_counted_from_the_gaps_and_not_the_box_height():
    """A box says how many lines it holds by having gaps in them.

    The height of a box says nothing of the kind, and reading it as a line count
    means guessing a type size. That is what put a 143-pixel character name at
    seven lines of twenty and produced a large empty panel with a small line of
    text sitting at the top of it.
    """
    from kizurium_translator.live import ink_line_count

    one = Image.new("RGB", (400, 120), "white")
    for x in range(0, 400, 20):  # one solid band of tall blocks
        for y in range(10, 100):
            one.putpixel((x, y), (0, 0, 0))
    assert ink_line_count(one, (0, 0, 400, 120)) == 1

    two = Image.new("RGB", (400, 220), "white")
    for x in range(0, 400, 20):
        for y in range(10, 80):
            two.putpixel((x, y), (0, 0, 0))
        for y in range(140, 210):
            two.putpixel((x, y), (0, 0, 0))
    assert ink_line_count(two, (0, 0, 400, 220)) == 2


def test_a_single_blank_row_is_not_a_gap():
    """One blank row is what tightly-leaded type looks like, not a new line."""
    from kizurium_translator.live import ink_line_count

    img = Image.new("RGB", (400, 90), "white")
    for x in range(0, 400, 20):
        for y in range(10, 44):
            img.putpixel((x, y), (0, 0, 0))
        for y in range(45, 80):  # a single blank row between them
            img.putpixel((x, y), (0, 0, 0))
    assert ink_line_count(img, (0, 0, 400, 90)) == 1


def test_an_empty_box_is_one_line_and_not_a_crash():
    from kizurium_translator.live import ink_line_count

    assert ink_line_count(Image.new("RGB", (100, 60), "white"), (0, 0, 100, 60)) == 1
    assert ink_line_count(None, (0, 0, 10, 10)) == 1


def test_a_heavy_face_measures_heavier_than_a_light_one():
    """Stroke is read off the original, so a translation can be lettered like it.

    Same type size, different weight: the heavy one has longer unbroken runs of
    ink across its rows, which is the difference between the face a game draws a
    title in and the face the overlay would otherwise substitute for it.
    """
    from kizurium_translator.live import measure_glyph_metrics

    def card(bar: int) -> Image.Image:
        img = Image.new("RGB", (400, 80), "white")
        for y in range(10, 50):  # a stem: one unbroken horizontal run
            for x in range(20, 20 + bar):
                img.putpixel((x, y), (0, 0, 0))
        return img

    heavy = measure_glyph_metrics(card(34), (0, 0, 400, 80))
    light = measure_glyph_metrics(card(8), (0, 0, 400, 80))
    assert heavy.stroke > light.stroke
    assert heavy.height == light.height  # same type size, different weight
