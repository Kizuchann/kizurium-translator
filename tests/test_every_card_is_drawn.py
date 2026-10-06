"""Every card has to reach the screen, not just the first one.

A game codex entry computed eight translations, logged eight, and drew one. The
state was right and the drawing was wrong, and nothing said so: the log counts
what was stored, not what was painted.

The cause was a `return` where a `continue` was meant. The multi-colour branch
was added so that a line with a keyword in another colour keeps that colour in
the translation, and a card with nothing to highlight takes the single-colour
branch - which ended the frame instead of moving on to the next card. Since the
first card on a screen is nearly always the heading, and a heading is one
colour, that made "only the top line is translated" the normal outcome rather
than an occasional one.

Each card is given its own background colour and the surface is scanned for it,
so the test asks whether the card was drawn and not where it landed - a card
that was skipped leaves nothing to find.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from _where import source_text

from kizurium_translator import live  # noqa: E402

cairo = pytest.importorskip("cairo")

# One distinct colour per card.
COLOURS = [
    (1.0, 0.1, 0.1, 1.0),
    (0.1, 1.0, 0.1, 1.0),
    (0.1, 0.1, 1.0, 1.0),
    (1.0, 1.0, 0.1, 1.0),
    (1.0, 0.1, 1.0, 1.0),
    (0.1, 1.0, 1.0, 1.0),
    (0.6, 0.2, 0.8, 1.0),
    (0.9, 0.5, 0.1, 1.0),
]

# The codex entry as the game produced it: a heading, a paragraph the two OCR
# engines read separately, a one-word tail and a button.
CODEX = [
    ("Исследование мира: Синие столбы света", 664, 458, 470, 27),
    ("Неактивный сейф", 611, 498, 159, 76),
    ("Выглядят как голубые столбы света", 751, 498, 419, 50),
    ("Эти маяки служат ориентирами", 752, 497, 490, 78),
    ("при продвижении", 644, 521, 597, 77),
    ("через неизведанные места", 648, 549, 589, 30),
    ("первый", 647, 576, 68, 31),
    ("Закрыть", 911, 875, 84, 27),
]


def card(text: str, x: int, y: int, w: int, h: int, bg, **extra) -> dict:
    block = {
        "text": text,
        "x": x,
        "y": y,
        "src_w": w,
        "src_h": h,
        "font": 18,
        "kind": "ui",
        "bg": bg,
        "fg": (1.0, 1.0, 1.0, 1.0),
    }
    block.update(extra)
    return block


def codex_blocks() -> list[dict]:
    return [card(t, x, y, w, h, COLOURS[i]) for i, (t, x, y, w, h) in enumerate(CODEX)]


def render(blocks: list[dict], w: int = 1920, h: int = 1080) -> cairo.ImageSurface:
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    ctx = cairo.Context(surface)
    live.draw_blocks(ctx, blocks, (0, 0, w, h), "")
    surface.flush()
    return surface


def card_alpha(colour) -> float:
    """The alpha a card of this colour is actually painted with.

    A card has to hide the text it replaces, so the drawing clamps it to a
    floor well above the value the caller asked for. The tests read the
    surface, so they have to know the same number rather than the requested one.
    """
    requested = float(colour[3]) if len(colour) > 3 else 0.55
    return min(1.0, max(0.985, requested))


def pixels_matching(surface: cairo.ImageSurface, colour, tol: int = 10) -> int:
    """How many pixels carry this card's background colour.

    The surface stores premultiplied bytes, so what lands on it is the
    requested colour scaled by the alpha the card was painted with.
    """
    alpha = card_alpha(colour)
    want = tuple(int(round(ch * 255 * alpha)) for ch in colour[:3])
    stride = surface.get_stride()
    data = surface.get_data()
    hits = 0
    for row in range(surface.get_height()):
        base = row * stride
        for col in range(surface.get_width()):
            off = base + col * 4
            if not data[off + 3]:
                continue
            px = (data[off + 2], data[off + 1], data[off])
            if all(abs(px[i] - want[i]) <= tol for i in range(3)):
                hits += 1
    return hits


def ink_area(surface: cairo.ImageSurface) -> int:
    """Every covered pixel. The cards are the only thing on the surface."""
    stride = surface.get_stride()
    data = surface.get_data()
    return sum(
        1
        for row in range(surface.get_height())
        for col in range(surface.get_width())
        if data[row * stride + col * 4 + 3]
    )


def clear_rect(surface: cairo.ImageSurface, x: int, y: int, w: int, h: int) -> None:
    ctx = cairo.Context(surface)
    ctx.save()
    ctx.set_operator(cairo.Operator.CLEAR)
    ctx.rectangle(x, y, w, h)
    ctx.fill()
    ctx.restore()
    surface.flush()


class TestEveryCardIsDrawn:
    def test_three_plain_cards_all_reach_the_surface(self):
        """Cards that do not touch, so every colour stays visible in the frame."""
        blocks = [
            card("Первый заголовок", 40, 40, 300, 30, COLOURS[0]),
            card("Второй заголовок", 40, 120, 300, 30, COLOURS[1]),
            card("Третий заголовок", 40, 200, 300, 30, COLOURS[2]),
        ]
        surface = render(blocks, 400, 300)
        for b in blocks:
            assert pixels_matching(surface, b["bg"]) > 50, b["text"]

    def test_the_last_card_of_the_codex_entry_reaches_the_surface(self):
        """The reported case. The button stands 300px clear of the rest."""
        surface = render(codex_blocks())
        assert pixels_matching(surface, COLOURS[-1]) > 50

    def test_the_frame_covers_more_than_the_first_card_alone(self):
        """The failing state as a quantity: one card's marks for eight cards."""
        one = ink_area(render([codex_blocks()[0]]))
        eight = ink_area(render(codex_blocks()))
        assert eight > one * 1.5, (one, eight)

    def test_a_multi_colour_card_does_not_stop_the_next_one(self):
        """Both branches have to fall through to the card after them."""
        spans = [
            {"share": 0.5, "color": (1.0, 0.2, 0.2, 1.0)},
            {"share": 0.5, "color": (0.2, 1.0, 0.2, 1.0)},
        ]
        blocks = [
            card("Акцент в середине", 40, 40, 300, 30, COLOURS[0], color_spans=spans),
            card("После разноцветной", 40, 120, 300, 30, COLOURS[1]),
        ]
        surface = render(blocks, 400, 300)
        assert pixels_matching(surface, COLOURS[1]) > 50

    def test_a_single_card_is_still_drawn(self):
        surface = render([card("Одна карточка", 40, 40, 300, 30, COLOURS[0])], 400, 300)
        assert pixels_matching(surface, COLOURS[0]) > 50


class TestTheLoopReachesTheEnd:
    def test_the_card_loop_contains_no_return(self):
        """Read the source: a `return` inside the card loop ends the frame."""
        import ast

        src = source_text("draw_blocks")
        tree = ast.parse(src)
        fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "draw_blocks"
        )
        loop = next(
            n
            for n in ast.walk(fn)
            if isinstance(n, ast.For)
            and isinstance(n.target, ast.Name)
            and n.target.id == "block"
        )
        for node in ast.walk(loop):
            if isinstance(node, ast.Return):
                pytest.fail(
                    f"draw_blocks returns at line {node.lineno}, which ends the "
                    "whole frame instead of moving on to the next card"
                )

    def test_erasing_the_first_card_leaves_the_others_behind(self):
        """If only the first was ever drawn, erasing it empties the frame."""
        surface = render(codex_blocks())
        clear_rect(surface, 664, 458, 470, 27)
        assert pixels_matching(surface, COLOURS[-1]) > 50
