"""A blinking cursor should not cost a full pass over the screen.

The detector already knew two things: that something moved, and whether any
element it is tracking moved with it. When nothing it tracks had moved, it read
the whole frame again anyway, on the reasoning that new text must have appeared
somewhere. That reasoning is not wrong, it is just expensive, and the screens
that pay for it most are the ones where the reasoning is wrong most often: a
video playing behind a codex panel, a health bar, a cursor.

So the movement is read for what it is. Motion without ink is not text, and
text arriving where nothing was is ink. The measure is local contrast in the new
frame rather than brightness, because the answer has to come out the same on a
dark screen with pale type and on a pale page with dark type.

Two directions matter and both are tested here. Decoration must not trigger a
pass, and text must - including text on a dark background, text that replaces
text rather than adding to it, and text smaller than the block it sits under.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from _where import source_text

from kizurium_translator import live  # noqa: E402
from kizurium_translator.live import TrackedBlock  # noqa: E402

pytest.importorskip("PIL.Image", reason="the project depends on Pillow")
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

# Real glyphs rather than drawn rectangles: the question is whether the detector
# can tell type from a bar, and rectangles are the easy half of that.
_FONT_PATH = None
for _p in (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
):
    if Path(_p).exists():
        _FONT_PATH = _p
        break


def font(size: int = 15):
    if _FONT_PATH is None:
        pytest.skip("no system font to draw real glyphs with")
    return ImageFont.truetype(_FONT_PATH, size)


def label(img: Image.Image, text: str, xy, fill: int) -> None:
    ImageDraw.Draw(img).text(xy, text, fill=fill, font=font())

W, H = 800, 450


def blank(value: int = 40) -> Image.Image:
    return Image.new("L", (W, H), value)


def block(box: tuple[int, int, int, int], text: str = "label") -> TrackedBlock:
    return TrackedBlock(
        id=1,
        box=box,
        text=text,
        norm=live.normalize_for_compare(text),
        script="en",
        lang="en",
        conf=90.0,
        engine="rapidocr",
    )


def ask(a: Image.Image, b: Image.Image, blocks=None) -> tuple[bool, float, float]:
    return live.change_outside_blocks_is_text(
        live.probe(a), live.probe(b), blocks or [], W, H
    )


class TestDecorationIsNotText:
    def test_a_blinking_cursor(self):
        a, b = blank(), blank()
        ImageDraw.Draw(b).rectangle((400, 200, 402, 212), fill=240)
        looks, _area, _ink = ask(a, b)
        assert not looks, "a cursor is not a line of text"

    def test_a_moving_bar(self):
        a, b = blank(), blank()
        ImageDraw.Draw(a).rectangle((100, 100, 700, 130), fill=90)
        ImageDraw.Draw(b).rectangle((100, 100, 500, 130), fill=90)
        looks, _area, _ink = ask(a, b)
        assert not looks, "a progress bar is not a line of text"

    def test_a_moving_gradient(self):
        a, b = blank(), blank()
        da, db = ImageDraw.Draw(a), ImageDraw.Draw(b)
        for x in range(0, W, 20):
            da.rectangle((x, 50, x + 18, 70), fill=x % 200)
            db.rectangle((x + 8, 50, x + 26, 70), fill=(x + 8) % 200)
        looks, _area, _ink = ask(a, b)
        assert not looks, "a sliding gradient is not text"

    def test_movement_inside_a_known_box(self):
        """The element owns it, and the per-block path will ask properly."""
        a, b = blank(), blank()
        ImageDraw.Draw(a).rectangle((100, 100, 400, 140), fill=60)
        ImageDraw.Draw(b).rectangle((100, 100, 400, 140), fill=200)
        looks, _area, _ink = ask(a, b, [block((100, 100, 400, 140))])
        assert not looks, "a repaint inside a tracked box must not force a pass"

    def test_an_unchanged_frame(self):
        a = blank()
        looks, area, _ink = ask(a, a.copy())
        assert not looks
        assert area == 0.0


class TestTextIsText:
    def test_a_new_line_on_a_dark_screen(self):
        a = blank(30)
        b = a.copy()
        label(b, "Inactive Safe Areas appear as blue pillars", (80, 200), 235)
        looks, _area, runs = ask(a, b)
        assert looks, f"new text read as motion (runs={runs:.2f})"

    def test_a_new_line_on_a_pale_screen(self):
        a = blank(235)
        b = a.copy()
        label(b, "Inactive Safe Areas appear as blue pillars", (80, 200), 25)
        looks, _area, runs = ask(a, b)
        assert looks, f"new text read as motion (runs={runs:.2f})"

    def test_text_replacing_text(self):
        """Not new ink in an empty place - different ink in a used one."""
        a = blank(235)
        b = blank(235)
        label(a, "Fourteen items remaining", (80, 200), 25)
        label(b, "Fifteen items remaining", (80, 200), 25)
        looks, _area, _runs = ask(a, b)
        assert looks, "a changed line is still a line"

    def test_text_outside_a_known_box_while_a_box_sits_elsewhere(self):
        a = blank(235)
        b = a.copy()
        label(a, "Settings", (40, 60), 25)
        label(b, "Autosave every thirty seconds", (80, 300), 25)
        looks, _area, runs = ask(a, b, [block((40, 60, 160, 82))])
        assert looks, f"text below the tracked box read as motion (runs={runs:.2f})"

    def test_a_short_label_still_counts(self):
        a = blank(235)
        b = a.copy()
        label(b, "1/1", (400, 200), 25)
        looks, area, runs = ask(a, b)
        assert looks or runs > 0, "a two-glyph label produced no ink at all"
        assert looks, f"a two-glyph label read as motion (area={area:.2f}, runs={runs:.2f})"


class TestItAnswersYesWhenItCannotTell:
    def test_mismatched_probe_sizes(self):
        """Guessing is unavoidable here, so guess towards reading more."""
        a = live.probe(blank())
        b = Image.new("L", (160, 90), 40)
        looks, _area, _ink = live.change_outside_blocks_is_text(a, b, [], W, H)
        assert looks, "an unreadable comparison must not lose text"

    def test_an_empty_probe(self):
        a = Image.new("L", (0, 0))
        looks, _area, _ink = live.change_outside_blocks_is_text(a, a, [], W, H)
        assert looks


class TestTheDecisionIsWired:
    def test_the_worker_asks_before_reading_the_frame_again(self):
        src = source_text("worker")
        assert "change_outside_blocks_is_text(" in src
        assert "probe-skip motion" in src, (
            "the question is asked and nothing acts on the answer"
        )

    def test_the_periodic_recheck_can_overrule_it(self):
        """The safety net has to stay able to say no to this."""
        src = source_text("worker")
        # Find the CALL site, not the function definition
        idx = src.rindex("change_outside_blocks_is_text(")
        call = src[max(0, idx - 900) : idx]
        assert "not force_ocr" in call, (
            "the skip is not gated on the periodic recheck, so a decision this "
            "makes can be final"
        )

    def test_the_per_block_path_is_still_required(self):
        """Without tracked elements there is nothing to compare against."""
        src = source_text("worker")
        idx = src.rindex("change_outside_blocks_is_text(")
        # The call site is gated by `and tracked` just above it; 800 chars is
        # enough to reach that line from the call.
        call = src[max(0, idx - 800) : idx]
        assert "tracked" in call, "the skip can fire with no tracker to compare against"


class TestStructure:
    def test_the_module_still_parses(self):
        import ast

        ast.parse(source_text("worker"))
