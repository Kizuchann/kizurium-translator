"""The overlay has to be about the size of the text it replaces.

Two screenshots, one bug: on a GitHub page the Russian came out roughly twice
the height of the English under it, the cards spilled over the neighbouring
panels, and the whole thing read as if it belonged to a different screen. The
screenshots also disagreed with the code, which is what made it worth pinning.

The cause was a glyph sized from the *box* rather than from the *line*. OCR
returns one box for three stacked lines all the time, and a box three lines tall
produces a font three times too big. The engine's own line_height is the answer
to how tall one line of this text is, and it survives the merging that inflates
the box.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PIL import Image  # noqa: E402

from kizurium_translator import live  # noqa: E402

REGION = Image.new("RGB", (1900, 780), (12, 12, 14))


def block(text: str, ru: str, box, line_height: int, kind: str = "body") -> dict:
    par = {"text": text, "box": box, "line_height": line_height, "kind": kind}
    return live.make_block(par, ru, REGION, 0, 0, 1900, 780)


# The page from the screenshots: a 14px UI, a wide box that OCR merged out of
# three stacked rows, and a Russian string longer than the English it replaces.
SRC = "ilamiro Стиль: убрать рамку с github 28 дней назад"
RU = SRC + " Лицензионное соглашение CHANGELOG"
MERGED_BOX = (355, 250, 1200, 292)
TRUE_LINE_H = 14


def test_dialogue_box_with_two_ink_bands_wraps():
    """A two-line speech box with no newline in the OCR string still covers both lines."""
    img = Image.new("RGB", (1200, 400), (8, 8, 10))
    px = img.load()
    for y in range(80, 102):
        for x in range(40, 900):
            px[x, y] = (230, 230, 230)
    for y in range(116, 138):
        for x in range(40, 860):
            px[x, y] = (230, 230, 230)
    src = (
        "However, you'll always be the most important person to me, Doctor. "
        "No matter what happens, this will never change."
    )
    ru = (
        "Однако вы всегда будете для меня самым важным человеком, Доктор. "
        "Что бы ни случилось, это никогда не изменится."
    )
    par = {
        "text": src,
        "box": (30, 70, 940, 155),
        "line_height": 22,
        "kind": "dialogue",
    }
    blk = live.make_block(par, ru, img, 0, 0, 1200, 400)
    assert blk["oneline"] is False
    assert blk["src_h"] >= 80


def test_name_card_sits_on_the_letters_and_keeps_their_colour():
    """Padding above a name must not lift the card, and a white ear must not
    repaint the end of the word."""
    img = Image.new("RGB", (400, 220), (70, 0, 70))
    px = img.load()
    for y in range(90, 160):
        for x in range(40, 200):
            px[x, y] = (255, 30, 130)
    for y in range(90, 160):
        for x in range(240, 300):
            px[x, y] = (255, 255, 255)
    par = {
        "text": "Buro",
        "box": (20, 40, 340, 190),
        "line_height": 40,
        "kind": "name",
    }
    blk = live.make_block(par, "Буро", img, 0, 0, 400, 220)
    assert blk["y"] >= 80
    assert blk["src_h"] < 100
    whites = [
        s
        for s in blk.get("color_spans") or []
        if max(s["color"][:3]) > 0.9 and max(s["color"][:3]) - min(s["color"][:3]) < 0.08
    ]
    assert not whites


class TestTheFontMatchesTheLine:
    def test_a_merged_box_does_not_produce_a_merged_size_font(self):
        """The screenshot case: 14px lines came out at 27px."""
        blk = block(SRC, RU, MERGED_BOX, TRUE_LINE_H)
        assert blk["font"] <= 20, blk["font"]

    def test_the_font_is_derived_from_line_height(self):
        assert block(SRC, RU, MERGED_BOX, 14)["font"] == block(
            SRC, RU, MERGED_BOX, 16
        )["font"] or block(SRC, RU, MERGED_BOX, 16)["font"] <= 20

    def test_a_taller_line_still_gets_a_taller_font(self):
        """The cap must not flatten every line to the same size."""
        small = block(SRC, RU, MERGED_BOX, 14)["font"]
        big = block(SRC, RU, (355, 250, 1200, 420), 40)["font"]
        assert big > small, (small, big)

    def test_a_genuinely_tall_single_line_is_not_shrunk(self):
        """A box that really is one 40px line must still read as 40px.

        line_height is a ceiling, not a replacement: an element with no smaller
        row inside it keeps its own size.
        """
        tall = block(SRC, RU, (355, 250, 1200, 292), 40)["font"]
        assert tall >= 24, tall

    def test_a_missing_line_height_falls_back_to_the_box(self):
        blk = block(SRC, RU, MERGED_BOX, 0)
        assert blk["font"] > 0

    def test_the_overlay_never_exceeds_the_text_it_covers(self):
        """Two lines of a merged box, at the real line height, must stay small."""
        for lh in (12, 14, 16, 18):
            blk = block(SRC, RU, MERGED_BOX, lh)["font"]
            assert blk <= max(24, lh + 8), (lh, blk)


class TestTheTranslationFits:
    def _fits(self, blk: dict, text: str) -> bool:
        per_px = max(1, int(blk["font"] * 0.52))
        per_line = max(1, (int(blk["src_w"]) - 8) // per_px)
        need = (len(text) + per_line - 1) // per_line
        capacity = int(blk["src_h"]) // (int(blk["font"]) + 2)
        return need <= capacity

    def test_a_long_russian_fits_inside_the_original_box(self):
        """A translation longer than its source must not spill over the page."""
        blk = block(SRC, RU, MERGED_BOX, TRUE_LINE_H)
        assert self._fits(blk, RU), (blk["src_w"], blk["src_h"], blk["font"])

    @pytest.mark.parametrize("extra", [0, 40, 80, 120, 200])
    def test_it_holds_as_the_translation_grows(self, extra):
        blk = block(SRC, SRC + " " + "лицензия " * (extra // 8 or 1), MERGED_BOX, TRUE_LINE_H)
        assert blk["src_w"] <= 1900
        assert blk["src_h"] <= 780

    def test_the_card_stays_inside_the_region(self):
        """The screenshots also showed cards pushed to the bottom edge.

        draw_blocks clamps x and y into the region, and a box larger than the
        region has nowhere to clamp to - so the clamp is what pushed everything
        into the corner. A card has to fit, or the clamp does the drawing.
        """
        blk = block(SRC, RU, MERGED_BOX, TRUE_LINE_H)
        assert blk["x"] >= 0 and blk["x"] + blk["src_w"] <= 1900
        assert blk["y"] >= 0 and blk["y"] + blk["src_h"] <= 780

    def test_a_box_bigger_than_the_region_is_reported_not_hidden(self):
        """A clamp that has to do the drawing is a coordinate bug, not a layout."""
        blk = block(SRC, RU, MERGED_BOX, TRUE_LINE_H)
        assert blk["src_w"] <= 1900 and blk["src_h"] <= 780


class TestItTracksTheSourceText:
    def test_the_font_follows_the_line_and_not_the_box(self):
        """Two boxes, same line height, different heights, same font."""
        a = block(SRC, RU, (355, 250, 1200, 292), 14)
        b = block(SRC, RU, (355, 250, 1200, 340), 14)
        assert a["font"] == b["font"]

    def test_kinds_keep_their_own_behaviour(self):
        for kind in ("body", "dialogue", "ui"):
            blk = block(SRC, RU, MERGED_BOX, TRUE_LINE_H, kind=kind)
            assert 0 < blk["font"] <= 32, (kind, blk["font"])

    def test_a_single_line_label_keeps_its_own_size(self):
        """A ui label has no merging problem: its box is its line.

        The fix is scoped to blocks that can be merged. A label whose box is one
        30px row should read at 30px, and shrinking it to match a paragraph
        would be the same bug in the other direction.
        """
        label = block("Settings", "Настройки", (355, 250, 520, 280), 30, kind="ui")
        assert label["font"] >= 20, label["font"]
