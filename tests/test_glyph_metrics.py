"""What the original actually looks like, measured from its own pixels.

Three complaints pointed at the same gap. A translation sits too high. A line
drawn with a fixed size is too big next to a small one and too small next to a
large one, because a line of tightly tracked capitals and a line of loosely
spaced lowercase are both "eighteen point" in the request while being nothing
alike on screen. And a line whose words are in two colours comes back painted
in one, because the colour was sampled from the whole box rather than from the
words in it.

None of that can be answered from the recognised text: it is not in the text. It
is in the pixels, and it has to be measured there - the height the glyphs
actually occupy, the width they actually take, and the colours the words are
actually drawn in.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator import live  # noqa: E402


def _img():
    return Image.new("RGB", (900, 240), (16, 17, 20))


class TestGlyphMetrics:
    def test_it_reports_height_and_width(self):
        """The metrics a card is laid out from.

        Every layout decision downstream - the size of the type, the width of
        the box, the padding - currently comes from the recognised line height,
        which is the engine's estimate of a line and not a measurement of the
        glyphs on this screen.
        """
        img = Image.new("RGB", (600, 120), (10, 10, 12))
        _draw_text(img, (20, 30), "Settings", (235, 235, 235), size=28)
        m = live.measure_glyph_metrics(img, (20, 30, 200, 66))
        assert m.height > 0, m
        assert m.width > 0, m
        assert m.aspect > 0, m
        assert m.tracking >= 0.0, m

    def test_an_empty_box_measures_nothing(self):
        m = live.measure_glyph_metrics(_img(), (10, 10, 14, 14))
        assert m.height == 0 or m.width == 0, m

    def test_a_wider_gap_reports_wider_tracking(self):
        """Letter spacing is visible on screen and is not in the text.

        A title set with wide tracking and a body line set tight are the same
        string length and nothing like the same shape, which is why a fixed font
        size cannot fit both.
        """
        img = Image.new("RGB", (900, 240), (0, 0, 0))
        _draw_spaced(img, (20, 20), "HELLO", spacing=18, size=28)
        wide = live.measure_glyph_metrics(img, (20, 20, 20 + 5 * 46, 60))

        img2 = Image.new("RGB", (900, 240), (0, 0, 0))
        _draw_spaced(img2, (20, 100), "HELLO", spacing=3, size=28)
        tight = live.measure_glyph_metrics(img2, (20, 100, 20 + 5 * 30, 140))

        assert wide.tracking > tight.tracking, (wide.tracking, tight.tracking)

    def test_a_taller_glyph_reports_a_taller_height(self):
        img = Image.new("RGB", (900, 400), (0, 0, 0))
        _draw_spaced(img, (20, 20), "Abc", spacing=2, size=14)
        small = live.measure_glyph_metrics(img, (20, 20, 140, 50))
        _draw_spaced(img, (20, 200), "Abc", spacing=2, size=40)
        large = live.measure_glyph_metrics(img, (20, 200, 300, 260))
        assert large.height > small.height, (small.height, large.height)

    def test_it_does_not_raise_on_a_degenerate_box(self):
        m = live.measure_glyph_metrics(_img(), (500, 500, 100, 100))
        assert m.height >= 0


class TestWordColours:
    def test_two_colour_line_reports_both(self):
        """A name highlighted in the middle of a line is a real thing.

        On a game screen a line is often one colour with the speaker's name, a
        number, or a keyword in another. Sampling the whole box gives one colour
        for both and the highlight is lost, so the span has to be found.
        """
        img = Image.new("RGB", (600, 120), (14, 14, 16))
        _draw_text(img, (20, 20), "Settings", (235, 235, 235), size=28)
        _draw_text(img, (160, 20), "Reset", (120, 200, 255), size=28)
        spans = live.measure_word_colors(img, (20, 20, 240, 56))
        assert len(spans) >= 2, spans
        hues = {live.color_hue(s.color) for s in spans}
        assert len(hues) >= 2, [s.color for s in spans]

    def test_one_colour_line_reports_one(self):
        img = Image.new("RGB", (600, 120), (14, 14, 16))
        _draw_text(img, (20, 20), "Settings Reset", (235, 235, 235), size=28)
        spans = live.measure_word_colors(img, (20, 20, 280, 56))
        hues = {live.color_hue(s.color) for s in spans}
        assert len(hues) == 1, [s.color for s in spans]

    def test_a_span_carries_its_own_extent(self):
        """A colour is only useful with the place it applies to."""
        img = Image.new("RGB", (600, 120), (14, 14, 16))
        _draw_text(img, (20, 20), "Settings", (235, 235, 235), size=28)
        _draw_text(img, (160, 20), "Reset", (120, 200, 255), size=28)
        spans = live.measure_word_colors(img, (20, 20, 240, 56))
        for s in spans:
            assert s.x2 > s.x1, s
            assert s.y2 > s.y1, s
            assert 0.0 <= s.share <= 1.0, s

    def test_an_empty_box_has_no_spans(self):
        assert live.measure_word_colors(_img(), (10, 10, 12, 12)) == []


class TestMakeBlockSurvivesAMeasuredLine:
    def test_a_card_is_built_with_a_real_image_and_a_measured_box(self):
        """The path that broke, and the reason it broke.

        `make_block` already had a local named `ink` holding an outline colour.
        The measurement took the same name, and a line that reached the outline
        branch put a tuple where a measurement was expected - a crash in the
        live loop, on the first card of the first frame, with nothing on screen
        to show for it. The unit tests all passed because none of them built a
        card from a real capture.
        """
        from PIL import ImageDraw

        img = Image.new("RGB", (900, 240), (12, 14, 18))
        draw = ImageDraw.Draw(img)
        draw.text((40, 60), "Settings", fill=(235, 235, 235), font=_font(30))
        draw.rectangle((40, 55, 200, 100), outline=(90, 110, 200), width=2)
        par = {"text": "Settings", "box": (40, 55, 200, 100),
               "line_height": 30, "conf": 95.0, "kind": "ui"}
        blk = live.make_block(par, "Настройки", img, 0, 0, 900, 240)
        assert blk["text"] == "Настройки"
        assert blk["font"] > 0
        assert "tracking" in blk
        assert "color_spans" in blk

    def test_it_survives_a_box_with_no_ink(self):
        """A box the recogniser returned for something that is not there."""
        img = Image.new("RGB", (900, 240), (12, 14, 18))
        par = {"text": "Something", "box": (400, 100, 420, 120),
               "line_height": 14, "conf": 60.0, "kind": "ui"}
        blk = live.make_block(par, "Что-то", img, 0, 0, 900, 240)
        assert blk["text"] == "Что-то"
        assert blk["src_w"] > 0 and blk["src_h"] > 0


class TestHue:
    def test_hue_says_whether_a_colour_is_itself(self):
        """White and grey have no hue of their own.

        A saturated blue and a saturated orange are distinguishable; white,
        black and every grey between them are not, and a layout that treated
        them as different would invent a second colour where there is none.
        """
        assert live.color_hue((255, 255, 255)) == live.NEUTRAL_HUE
        assert live.color_hue((0, 0, 0)) == live.NEUTRAL_HUE
        assert live.color_hue((128, 128, 128)) == live.NEUTRAL_HUE
        assert live.color_hue((255, 0, 0)) != live.NEUTRAL_HUE
        assert live.color_hue((0, 128, 255)) != live.NEUTRAL_HUE

    def test_two_hues_of_the_same_colour_are_the_same(self):
        assert live.color_hue((200, 60, 60)) == live.color_hue((210, 70, 70))

    def test_two_different_hues_are_different(self):
        assert live.color_hue((220, 60, 60)) != live.color_hue((60, 60, 220))


def _draw_spaced(img, at, text, spacing, size):
    from PIL import ImageDraw, ImageFont

    draw = ImageDraw.Draw(img)
    font = _font(size)
    x, y = at
    for ch in text:
        draw.text((x, y), ch, fill=(240, 240, 240), font=font)
        x += int(size * 0.62) + spacing


def _draw_text(img, at, text, color, size):
    from PIL import ImageDraw

    draw = ImageDraw.Draw(img)
    draw.text(at, text, fill=color, font=_font(size))


def _font(size):
    from PIL import ImageFont

    for path in (
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/LiberationSans-Regular.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()
