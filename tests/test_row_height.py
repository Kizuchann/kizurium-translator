"""Rows that span the whole box must not size the type.

The Tales of Berseria quest description (ws16) came back from the recogniser as
five rows inside a 145-pixel box with a 30-pixel pitch - correct - but every row
box was itself 145 pixels tall, the whole paragraph, and the type was sized from
that height: 134 pixels of font on a menu line. Leading bounds a line of text,
so the pitch is what measures it.

Also: the detector's rows are the truth about how many lines there were. Ink
bands can only add a line the screen does not have, so they are a fallback now,
not a third opinion.

    uv run pytest tests/test_row_height.py
"""

from __future__ import annotations

import pytest
from PIL import Image

from kizurium_translator.core.text import row_pitch
from kizurium_translator.render.cards import make_block


def _rows(n: int, pitch: int, box_h: int, top: int = 100) -> list[dict]:
    """`n` rows at `pitch`, each as tall as the whole box - the ws16 shape."""
    return [
        {"box": (200, top + i * pitch, 1200, top + box_h), "line_height": 28}
        for i in range(n)
    ]


def test_pitch_bounds_a_line_that_is_taller_than_its_leading():
    rows = _rows(5, pitch=30, box_h=145)
    pitch = row_pitch(rows)
    assert pitch == 30, "ведущий должен читаться из строк"
    assert max(r["box"][3] - r["box"][1] for r in rows) > pitch * 3


def test_five_rows_do_not_become_a_134_pixel_font():
    par = {
        "kind": "body",
        "text": "After successfully reaching Horunka, the party",
        "line_height": 28,
        "line_boxes": _rows(5, pitch=30, box_h=145),
        "box": (200, 100, 1200, 245),
    }
    region = Image.new("RGB", (1920, 1080), (24, 26, 30))
    block = make_block(par, "перевод", region, 0, 0, 1920, 1080)
    font = int(block.get("font") or 0)
    assert font <= 40, f"кегль {font} для абзаца с ведущим 30 - это размер заголовка"


def test_rows_win_over_ink_bands():
    """Five measured rows stay five rows even if the ink looks like six."""
    par = {
        "kind": "body",
        "text": "Resume the unfinished quest",
        "line_height": 30,
        "line_boxes": _rows(5, pitch=30, box_h=150),
        "box": (200, 100, 1200, 250),
    }
    # Ink stripes painted at six positions inside the box: a fallback that is
    # consulted when there are rows would count six and inflate the card.
    region = Image.new("RGB", (1920, 1080), (10, 10, 10))
    for i in range(6):
        y = 105 + i * 24
        for x in range(210, 1190, 3):
            region.putpixel((x, y), (240, 240, 240))
            region.putpixel((x, y + 1), (240, 240, 240))
    block = make_block(par, "перевод", region, 0, 0, 1920, 1080)
    assert int(block.get("lines") or 1) <= 5, "чернила не должны добавлять строки"


def test_no_rows_still_falls_back_to_the_box():
    """Without rows the old path still has to produce a card."""
    par = {
        "kind": "body",
        "text": "Resume the unfinished quest",
        "line_height": 30,
        "box": (200, 100, 1200, 250),
    }
    region = Image.new("RGB", (1920, 1080), (24, 26, 30))
    block = make_block(par, "перевод", region, 0, 0, 1920, 1080)
    assert int(block.get("src_w") or 0) > 0 and int(block.get("src_h") or 0) > 0
    assert not block.get("oneline"), "бокс без строк всё ещё должен дать абзац"


@pytest.mark.parametrize("pitch", [24, 30, 48, 60])
def test_font_tracks_the_leading_at_every_pitch(pitch):
    par = {
        "kind": "body",
        "text": "After successfully reaching Horunka",
        "line_height": pitch,
        "line_boxes": _rows(5, pitch=pitch, box_h=pitch * 5),
        "box": (200, 100, 1200, 100 + pitch * 5),
    }
    region = Image.new("RGB", (1920, 1080), (24, 26, 30))
    block = make_block(par, "перевод", region, 0, 0, 1920, 1080)
    font = int(block.get("font") or 0)
    assert 10 <= font <= pitch + 6, f"кегль {font} при ведущем {pitch}"


def test_row_pitch_still_reads_a_normal_paragraph():
    """A paragraph read one row per line keeps its own leading."""
    rows = [
        {"box": (100, 100, 900, 128)},
        {"box": (100, 158, 900, 186)},
        {"box": (100, 216, 900, 244)},
    ]
    assert row_pitch(rows) == 58
