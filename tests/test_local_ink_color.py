"""Ink and panel are read from the strip the line occupies, not from its box.

A slanted label comes back as a tall rectangle. The words occupy a band in
that rectangle and the rest is the panel under them, often a different colour
from the panel the words are painted on. Read across the whole rectangle, that
panel becomes the second-largest colour and the translation is painted in it.
And bold type in a tight box covers more of it than its panel does, so the
panel is the colour round the edge of the box, not the most common one.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from kizurium_translator.live import is_garbage_ocr, sample_ocr_cover_colors
from kizurium_translator.render.overlay import _word_cuts
from kizurium_translator.typography.metrics import edge_panels, measure_word_colors


def _slanted_label(ink: tuple[int, int, int]) -> Image.Image:
    """Magenta field, a line of type, and a bright panel filling the bottom."""
    im = Image.new("RGB", (240, 160), (186, 24, 110))
    draw = ImageDraw.Draw(im)
    draw.rectangle((0, 96, 240, 160), fill=(137, 255, 12))
    for x0 in range(18, 200, 22):
        draw.rectangle((x0, 28, x0 + 12, 34), fill=ink)
        draw.rectangle((x0 + 3, 34, x0 + 8, 52), fill=ink)
    return im


# The strip the line is in: centred on the type, level here for simplicity.
BAND = (120.0, 40.0, 220.0, 40.0, 0.0)


def test_white_strokes_stay_white_when_a_bright_panel_sits_below():
    im = _slanted_label((255, 255, 255))
    got = sample_ocr_cover_colors(im, (0, 0, 240, 160), band=BAND)
    assert got is not None
    bg, fg = got
    assert min(fg[:3]) > 0.9, fg
    assert bg[1] < 0.3, bg


def test_dark_strokes_are_not_replaced_by_the_panel_below():
    im = _slanted_label((120, 16, 28))
    got = sample_ocr_cover_colors(im, (0, 0, 240, 160), band=BAND)
    assert got is not None
    bg, fg = got
    assert fg[0] > fg[1] and fg[1] < 0.35, fg
    assert bg[1] < 0.3, bg


def test_bold_type_filling_its_box_is_not_taken_for_the_panel():
    im = Image.new("RGB", (200, 40), (48, 8, 72))
    draw = ImageDraw.Draw(im)
    # Ink covers well over half of the box interior.
    for x0 in range(6, 190, 16):
        draw.rectangle((x0, 5, x0 + 13, 34), fill=(250, 90, 250))
    got = sample_ocr_cover_colors(im, (4, 4, 196, 36))
    assert got is not None
    bg, fg = got
    assert bg[0] < 0.3 and bg[2] < 0.4, bg
    assert fg[0] > 0.9 and fg[2] > 0.9, fg


def _two_tone_line() -> Image.Image:
    """Dark word, then a white number with a dark outline joining them."""
    im = Image.new("RGB", (260, 44), (255, 0, 118))
    draw = ImageDraw.Draw(im)
    draw.rectangle((0, 0, 260, 4), fill=(255, 76, 160))
    for x0 in range(10, 130, 18):
        draw.rectangle((x0, 10, x0 + 12, 34), fill=(153, 0, 94))
    draw.rectangle((126, 18, 150, 22), fill=(153, 0, 94))
    for x0 in range(150, 240, 18):
        draw.rectangle((x0, 8, x0 + 14, 36), fill=(153, 0, 94))
        draw.rectangle((x0 + 2, 10, x0 + 12, 34), fill=(255, 255, 255))
    return im


def test_a_dark_word_and_a_white_number_are_two_colours():
    im = _two_tone_line()
    rgb = np.asarray(im, dtype=np.float32)
    panels = edge_panels(rgb)
    spans = measure_word_colors(im, (0, 0, 260, 44), panels=panels)
    assert len(spans) == 2, [(s.x1, s.x2, s.color) for s in spans]
    first, second = spans
    assert first.color[0] < 0.75 and first.color[1] < 0.1, first.color
    assert min(second.color) > 0.9, second.color


def test_the_colour_change_lands_on_the_matching_word():
    spans = [{"share": 0.55}, {"share": 0.45}]
    cuts = _word_cuts("ACCURACY 47.01%", "Точность 47.01%", spans, 1.0)
    assert cuts == [len("Точность ".encode("utf-8"))]
    spans = [{"share": 0.76}, {"share": 0.24}]
    cuts = _word_cuts("MAX COMBO 13", "МАКС КОМБО 13", spans, 1.0)
    assert cuts == [len("МАКС КОМБО ".encode("utf-8"))]
    # Fewer words in the translation: the number still trails.
    cuts = _word_cuts("MAX COMBO 13", "КОМБО 13", spans, 1.0)
    assert cuts == [len("КОМБО ".encode("utf-8"))]


def test_a_gradient_along_a_line_keeps_more_than_one_colour():
    im = Image.new("RGB", (220, 48), (20, 16, 28))
    draw = ImageDraw.Draw(im)
    colours = (
        (230, 40, 40),
        (230, 180, 30),
        (40, 200, 80),
        (40, 80, 230),
    )
    for i, colour in enumerate(colours):
        draw.rectangle((16 + i * 48, 12, 52 + i * 48, 34), fill=colour)
    spans = measure_word_colors(im, (0, 0, 220, 48))
    from kizurium_translator.typography.metrics import color_hue

    hues = {round(color_hue(s.color), 1) for s in spans}
    assert len(spans) >= 3, [(s.color, s.x1, s.x2) for s in spans]
    assert len(hues) >= 3, hues


def test_icon_scraps_are_not_a_line_of_text():
    assert is_garbage_ocr("L na o to")
    assert not is_garbage_ocr("MAX COMBO")
    assert not is_garbage_ocr("HP 230")
