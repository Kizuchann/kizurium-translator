"""Отрисовка перевода на экране.

Здесь блоки, собранные `render/cards.py`, превращаются в пиксели: сначала раскладка (`fit_layout`) находит кегль и
перенос, потом карточка вписывается между соседями, и только потом рисуется -
плашка, обводка и текст, причём цветными прогонами по словам.

Порядок именно такой, потому что соседи известны только слева направо: карточка,
нарисованная первой, должна знать, сколько места оставила справа. Поэтому
`_free_width_near` меряет свободное место по боксам ОРИГИНАЛА, а не уже
нарисованных карточек, и `_fit_between_neighbours` двигает и сужает текущую
карточку, а не соседнюю.

`_snap_to_glyph_edge` и `_fill_by_color_spans` отвечают за один случай: в
оригинале часть строки выделена другим цветом. Граница прогона не должна
проходить сквозь букву, поэтому она привязывается к границе между глифами.

Углы карточки считает `card_quad` из `render/cards.py`: на наклонной строке
bounding box заметно больше самой карточки, и любая проверка по bounding box
называет столкновением то, что не пересекается.

Тела перенесены из `live.py` дословно."""
from __future__ import annotations

import math
import re

from . import cards  # noqa: F401
from .card_cache import CARD_CACHE, CardCacheKey  # noqa: F401

try:
    import gi

    gi.require_version("Pango", "1.0")
    import cairo
    from gi.repository import Pango, PangoCairo
except Exception:  # noqa: BLE001 - слои разбираются без окна
    Pango = PangoCairo = cairo = None  # type: ignore[assignment]
import cairo

from ..core import active
from ..core.text import (  # noqa: F401
    _family_for,
    clamp,
    tlog,
)
from ..typography.metrics import (  # noqa: F401
    COLOR_MIN_HUE_GAP,
    _hue_gap,
    color_hue,
)
from .cards import (  # noqa: F401
    CARD_BG_DEFAULT,
    CARD_FG_DEFAULT,
    CARD_RADIUS,
    COLOR_SPAN_MIN_SHARE,
    card_quad,
    fit_layout,
)


def _quads_overlap(
    a: list[tuple[float, float]], b: list[tuple[float, float]]
) -> bool:
    """Whether two convex quadrilaterals share any area.

    Separating axis: two convex polygons are disjoint exactly when one of the
    four edge normals separates them, and for a rectangle that is four axes
    rather than the infinite pair a general pair would need.
    """
    for poly in (a, b):
        for i in range(4):
            x1, y1 = poly[i]
            x2, y2 = poly[(i + 1) % 4]
            nx, ny = -(y2 - y1), (x2 - x1)
            lo_a = min(px * nx + py * ny for px, py in a)
            hi_a = max(px * nx + py * ny for px, py in a)
            lo_b = min(px * nx + py * ny for px, py in b)
            hi_b = max(px * nx + py * ny for px, py in b)
            if hi_a < lo_b or hi_b < lo_a:
                return False
    return True


def _free_width_near(
    block: dict,
    blocks: list[dict],
    rw: int,
) -> int:
    """How far a card may grow to the right before it reaches another element.

    Measured from the *source* boxes rather than from the cards already drawn.
    The drawn cards only know about what came before them, so a label on the
    left of a pair grew into the gap while the label on the right had no idea
    the first one was there. Source boxes are known for the whole frame, so the
    limit is the same in both directions and neither card is chosen as the one
    that gives way.

    Only elements whose vertical range overlaps this one count: a paragraph
    under a row of buttons is not in the way of that row, and treating it as
    though it were would cap every card on the screen at the first block
    anywhere below it.

    Zero when no answer can be trusted - an element sitting on top of this one
    has no gap to grow into, and growing over it is the failure this is here to
    prevent.
    """
    ox = block.get("ox")
    oy = block.get("oy")
    if ox is None or oy is None:
        return 0
    x0 = float(ox)
    sw = float(block.get("src_w") or 0)
    y0 = float(oy)
    sh = float(block.get("src_h") or 0)
    right = float(rw)
    for other in blocks:
        if other is block:
            continue
        oxo = other.get("ox")
        oyo = other.get("oy")
        if oxo is None or oyo is None:
            continue
        ow = float(other.get("src_w") or 0)
        oh = float(other.get("src_h") or 0)
        if oyo + oh <= y0 or y0 + sh <= oyo:
            continue
        if oxo + ow <= x0:
            continue
        if oxo < x0 + sw:
            return 0
        right = min(right, float(oxo))
    return max(0, int(right - x0))


def _blocked_below(block: dict, blocks: list[dict]) -> int | None:
    """The top of the nearest different element this card runs into.

    `_fit_between_neighbours` knows about the cards drawn before this one. It
    cannot know about the ones after, and on a screen where two paragraphs are
    read as boxes that overlap by a few pixels that is exactly the one that
    matters: a card laid out over five lines runs its last line into the box
    below, the next card is painted after it, and the line disappears under the
    neighbour - the card is the right size for the text it covers and the text
    is cut in half.

    Every block of the frame is in hand here, so the neighbour is looked up
    before the card is fitted. Only an element that starts inside this card's own
    box counts: one that begins above the middle of it is this paragraph's own
    continuation, not something to stay off.
    """
    x, y = int(block["x"]), int(block["y"])
    w = int(block.get("src_w", 0) or 0)
    h = int(block.get("src_h", 0) or 0)
    if w <= 0 or h <= 0:
        return None
    limit: int | None = None
    for other in blocks:
        if other is block:
            continue
        ox, oy = int(other["x"]), int(other["y"])
        ow = int(other.get("src_w", 0) or 0)
        oh = int(other.get("src_h", 0) or 0)
        if ow <= 0 or oh <= 0:
            continue
        if min(x + w, ox + ow) - max(x, ox) <= 0:
            continue  # beside it, not below it
        if oy <= y + max(8, int(h * 0.4)):
            continue  # its own text, or something above
        if limit is None or oy < limit:
            limit = oy
    return None if limit is None else max(8, limit - 2)


def _resolve_vertical_overlaps(blocks: list[dict], rh: int) -> None:
    """Cards stacked on one another are separated before anything is fitted.

    Two paragraphs read off a panel come back as boxes that overlap by a few
    pixels, because a box is the recogniser's guess at where the type is and
    neither guess is shy of the other. The cards are opaque, so whichever is
    drawn second covers the other's last line: the paragraph above was laid out
    over five lines and had its fifth line painted over by the paragraph below
    it, and shrinking the upper card instead left a strip of English showing
    between the two.

    Both cards are wanted whole, so the lower one steps down to the upper one's
    bottom - which still covers its own text, because the strip it gives up is
    the strip the upper card covers. The move is refused when it is more than a
    nudge, and then the upper one steps up if there is room.
    """
    for _ in range(3):
        upright = [
            b
            for b in blocks
            if abs(float(b.get("angle", 0.0) or 0.0)) < 1.2
            and int(b.get("src_h", 0) or 0) >= 8
            and int(b.get("src_w", 0) or 0) >= 8
        ]
        upright.sort(key=lambda b: int(b["y"]))
        moved = False
        for i, upper in enumerate(upright):
            ux, uy = int(upper["x"]), int(upper["y"])
            uw, uh = int(upper["src_w"]), int(upper["src_h"])
            for lower in upright[i + 1 :]:
                lx, ly = int(lower["x"]), int(lower["y"])
                lw, lh_ = int(lower["src_w"]), int(lower["src_h"])
                if min(ux + uw, lx + lw) - max(ux, lx) <= 0:
                    continue  # beside it
                if ly >= uy + uh - 2:
                    continue  # clear of it
                down = uy + uh + 2
                if down - ly <= max(8, int(lh_ * 0.4)) and down + lh_ <= rh:
                    lower["y"] = down
                    moved = True
                    continue
                up = ly - lh_ - 2
                if up >= 0 and ly - up <= max(8, int(uh * 0.4)):
                    upper["y"] = up
                    moved = True
        if not moved:
            return


def _fit_between_neighbours(
    x: int,
    y: int,
    w: int,
    h: int,
    block: dict,
    placed: list[list[tuple[float, float]]],
    rw: int,
    rh: int,
) -> tuple[int, int, int, int]:
    """Keep a card from covering the cards already drawn this frame.

    A Russian translation of a short English label is routinely longer, so the
    card outgrows the element it belongs to and lands on the next one along.
    Rather than let the later card win - which is what produced a heading's
    translation spread over three elements - the overflowing card is narrowed
    and moved until it fits, and only shrinks its type if narrowing is not
    enough. Text is never dropped: if it still does not fit, the card is left
    where it is, because an overlong card is a smaller problem than a card that
    covers a different sentence.
    """
    if not placed:
        return x, y, w, h

    angle = float(block.get("angle", 0.0) or 0.0)

    def quad(xx: float, yy: float, ww: float, hh: float) -> list[tuple[float, float]]:
        return card_quad(xx + ww * 0.5, yy + hh * 0.5, ww, hh, angle)

    def hits(
        xx: float, yy: float, ww: float, hh: float
    ) -> list[list[tuple[float, float]]]:
        mine = quad(xx, yy, ww, hh)
        return [other for other in placed if _quads_overlap(mine, other)]

    if not hits(x, y, w, h):
        return x, y, w, h

    # Only cards that belong to a *different* element matter. A card next to
    # its own source text is expected to touch it. The comparison is against
    # the element's own OCR box - ox, oy - because x and y are the card's
    # position and it may already have been moved away from them.
    src_w = int(block.get("src_w", w) or w)
    src_h = int(block.get("src_h", h) or h)
    own_x, own_y = int(block.get("ox", x)), int(block.get("oy", y))
    own = own_x + src_w // 2, own_y + src_h // 2

    # The element's own box, as drawn. A card has to keep covering the text it
    # read: a card that has wandered off its own line is a translation sitting
    # on top of nothing while the original shows through, which is the failure
    # this function exists to prevent and the one it was causing. So a move is
    # only taken if the card still covers its own source afterwards, and a
    # narrow one is measured the same way.
    own_quad = quad(own_x, own_y, src_w, src_h)
    start = (x, y, w, h)

    def acceptable(nx: float, ny: float, nw: float, nh: float) -> bool:
        return _quads_overlap(quad(nx, ny, nw, nh), own_quad)

    for _ in range(4):
        blockers = [b for b in hits(x, y, w, h) if _quad_far(b, own) > 24]
        if not blockers:
            break
        # Move above the blockers if there is room, else narrow. The corners of a
        # rotated card are fractional, so every one of these is a float and is
        # rounded back to whole pixels before it is returned: the drawing and
        # the placement have to agree on where the card is, and a card drawn at
        # a tenth of a pixel from the place the collision test cleared is not
        # cleared at all.
        top = min(min(p[1] for p in b) for b in blockers)
        if y > top - 2:
            ny = max(0.0, top - h - 2)
            if acceptable(x, ny, w, h) and not hits(x, ny, w, h):
                y = int(round(ny))
                break
        # Below. The move above only clears a card that starts above this one;
        # two paragraphs whose boxes overlap by a few pixels - which is what a
        # recogniser returns for two stacked panels - need the lower one to step
        # down instead. It still covers its own text, and the paragraph above it
        # keeps its last line, where before the lower card was painted over it
        # and that line disappeared.
        bottom = max(max(p[1] for p in b) for b in blockers)
        if top < y - 2 and bottom > y:
            ny = min(float(rh - h), bottom + 2)
            if ny > y + 2 and acceptable(x, ny, w, h) and not hits(x, ny, w, h):
                y = int(round(ny))
                break
        left = max(0.0, min(min(p[0] for p in b) for b in blockers) - 2)
        if x > left and acceptable(left, y, w, h):
            if not hits(left, y, w, h):
                x = int(round(left))
                break
        right = min(max(p[0] for p in b) for b in blockers)
        new_w = right - x - 2
        if new_w >= max(40, src_w // 2) and acceptable(x, y, new_w, h):
            if not hits(x, y, new_w, h):
                w = int(round(new_w))
                break
    if not acceptable(x, y, w, h):
        return start
    return clamp(x, 0, max(0, rw - w)), clamp(y, 0, max(0, rh - h)), w, h


def _quad_far(q: list[tuple[float, float]], centre: tuple[int, int]) -> int:
    """How far a drawn card's middle is from a point, in pixels."""
    cx = sum(p[0] for p in q) / len(q)
    cy = sum(p[1] for p in q) / len(q)
    return int(max(abs(cx - centre[0]), abs(cy - centre[1])))


def _snap_to_glyph_edge(layout, lx: float, text_len: int) -> float:
    """Ближайшая граница между глифами к позиции lx в раскладке.

    Граница цвета не должна проходить сквозь букву. Спан режется по
    пропорции длины строки, а пропорция попадает куда угодно, и на одном
    экране первый глиф слова «Покупка» выходил наполовину жёлтым,
    наполовину малиновым: буква
    пополам своего цвета — это не выделение, это опечатка в картинке.

    Pango знает, где начинается каждый символ, поэтому граница переносится на
    ближайший край соседнего глифа.

    Две особенности API, на которые легко наступить: index_to_pos отдаёт
    Pango.Rectangle в единицах Pango (px * Pango.SCALE), а не кортеж в пикселях,
    и xy_to_index отдаёт тройку (внутри_ли, индекс, хвост), где индекс —
    байтовый, как и его собственный index_to_pos. На тихо проглоченном
    TypeError здесь возвращался исходный lx, то есть ровно то поведение, ради
    устранения которого функция и писалась.
    """
    if lx <= 0:
        return 0.0
    try:
        width = float(layout.index_to_pos(text_len).x) / Pango.SCALE
    except Exception:  # noqa: BLE001
        return lx
    if lx >= width:
        return width
    try:
        # xy_to_index через PyGObject не годится: pango_layout_xy_to_index
        # отдаёт индекс через int*, и обёртка возвращает там 0 для любой
        # позиции — проверено на строке шириной 492px, все четыре пробы дали
        # индекс 0. index_to_pos работает и монотонен по индексу, поэтому
        # граница ищется делением пополам.
        lo, hi = 0, text_len
        while lo < hi:
            mid = (lo + hi) // 2
            if float(layout.index_to_pos(mid).x) / Pango.SCALE < lx:
                lo = mid + 1
            else:
                hi = mid
        idx = max(0, min(lo, text_len))
        here = float(layout.index_to_pos(idx).x) / Pango.SCALE
        nxt = (
            float(layout.index_to_pos(idx + 1).x) / Pango.SCALE
            if idx + 1 < text_len
            else here
        )
    except Exception:  # noqa: BLE001
        return lx
    # Ближайший из двух краёв того же глифа: глиф остаётся целым либо слева,
    # либо справа от границы, но не разрезан пополам.
    if abs(nxt - lx) < abs(here - lx):
        return nxt
    return here


def _word_cuts(source: str, drawn: str, spans: list[dict], total_ink: float) -> list:
    """Byte offsets in the drawn text where each span ends, when a word says so.

    A change of colour in a label is nearly always between words - a name and
    a number, a key and its action - and the translation has the same words in
    the same places at its ends even when its middle is longer. Where the
    original's share of ink lands on a word boundary, the cut goes on the
    matching boundary of the translation, counted from the end of the line
    when the word counts differ, because numbers and names trail. None means
    the share does not land on a boundary and the proportion is used instead.
    """
    src_words = source.split()
    out_words = [(m.start(), m.group()) for m in re.finditer(r"\S+", drawn or "")]
    if len(src_words) < 2 or len(out_words) < 2:
        return []
    letters = [len(wd) for wd in src_words]
    total = float(sum(letters)) or 1.0
    bounds = []
    run = 0
    for n in letters[:-1]:
        run += n
        bounds.append(run / total)
    cuts: list = []
    cursor = 0.0
    for span in spans[:-1]:
        cursor += float(span.get("share", 0.0)) / total_ink
        k = min(range(len(bounds)), key=lambda j: abs(bounds[j] - cursor))
        if abs(bounds[k] - cursor) > 0.12:
            cuts.append(None)
            continue
        words_before = k + 1
        if len(out_words) == len(src_words):
            idx = words_before
        else:
            idx = len(out_words) - (len(src_words) - words_before)
        if not 0 < idx < len(out_words):
            cuts.append(None)
            continue
        start_char = out_words[idx][0]
        cuts.append(len(drawn[:start_char].encode("utf-8")))
    return cuts


def _fill_by_color_spans(
    cr,
    x: float,
    y: float,
    w: float,
    h: float,
    pad_x: float,
    pad_y: float,
    layout,
    fg,
    spans: list[dict],
    total_ink: float,
    source: str = "",
) -> None:
    """Paint a text layout with each span in the colour the original had there.

    The spans are shares of the original line's ink, laid end to end, and each
    one is clipped to its share of the drawn layout. The clip is what makes it
    correct: the translation is a different string with a different number of
    characters, so the only thing the original can tell us is where the change
    in colour happened relative to the line, not which word it was.
    """
    ink = tuple(float(c) for c in fg[:3])
    ink_a = float(fg[3]) if len(fg) > 3 else 1.0
    # Base colour everywhere, then the highlights on top of their own bands.
    cr.save()
    cr.rectangle(x, y, w, h)
    cr.clip()
    cr.move_to(x + pad_x, y + pad_y)
    PangoCairo.layout_path(cr, layout)
    cr.set_source_rgba(*ink, ink_a)
    cr.fill()

    cursor = 0.0
    # Индексы Pango — байтовые, а не посимвольные: кириллица в UTF-8 занимает
    # по два байта, и счётчик символов указывал бы в середину буквы.
    raw = ""
    try:
        raw = layout.get_text()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        n_bytes = len(raw if isinstance(raw, bytes) else str(raw).encode("utf-8"))
    except Exception:  # noqa: BLE001
        n_bytes = max(1, int(layout.get_character_count()))
    # Границы спанов считаются заранее и привязываются к глифам: полосы должны
    # идти встык, иначе соседний спан заливает предыдущий. Общий край двух
    # соседей — одна и та же привязанная точка, поэтому стыка не видно.
    try:
        text_w = float(layout.index_to_pos(n_bytes).x) / Pango.SCALE
    except Exception:  # noqa: BLE001
        text_w = 0.0
    if text_w <= 1.0:
        text_w = max(1.0, w - pad_x * 2)
    word_cuts = _word_cuts(source, str(raw or ""), spans, total_ink)
    cuts: list[float] = []
    for i, span in enumerate(spans):
        cursor += float(span.get("share", 0.0)) / total_ink
        at = word_cuts[i] if i < len(word_cuts) else None
        if at is not None:
            try:
                cuts.append(x + pad_x + float(layout.index_to_pos(at).x) / Pango.SCALE)
                continue
            except Exception:  # noqa: BLE001
                pass
        # Shares are of the original's ink, so they map onto the drawn text,
        # not onto the card around it: a card wider than its text put the
        # change of colour in the middle of the next word.
        cuts.append(x + pad_x + _snap_to_glyph_edge(layout, text_w * cursor, n_bytes))
    edge = x + w
    for i, span in enumerate(spans):
        left = cuts[i - 1] if i else x
        right = min(cuts[i], edge) if i < len(cuts) else edge
        if right - left < 0.5:
            continue
        color = span.get("color") or ink
        try:
            rgb = tuple(float(c) for c in color[:3])
        except (TypeError, ValueError):
            continue
        cr.save()
        cr.rectangle(left, y, right - left + 0.5, h)
        cr.clip()
        cr.move_to(x + pad_x, y + pad_y)
        PangoCairo.layout_path(cr, layout)
        cr.set_source_rgba(*rgb, ink_a)
        cr.fill()
        cr.restore()
    cr.restore()


def draw_blocks(cr: cairo.Context, blocks: list[dict], region: tuple[int, int, int, int] | None, status: str) -> None:
    _ = region, status
    cr.save()
    cr.set_operator(cairo.Operator.CLEAR)
    cr.paint()
    cr.restore()
    cr.set_operator(cairo.Operator.OVER)

    # Cards already drawn this frame, in the order they are drawn, as the four
    # corners they were actually drawn at. Used to keep a long translation from
    # covering a shorter neighbour - which needs the shape as drawn, because a
    # card on a slanted line and its bounding box are not the same size.
    placed: list[list[tuple[float, float]]] = []
    _resolve_vertical_overlaps(blocks, region[3] if region else 0)
    for block in blocks:
        x, y = block["x"], block["y"]
        block.setdefault("ox", x)
        block.setdefault("oy", y)
        box_h = int(block.get("src_h", 0) or 0)
        if abs(float(block.get("angle", 0.0) or 0.0)) >= 1.2:
            # A slanted card's room is measured along its own line, where a
            # neighbouring label is sharp ink like any other. The upright boxes
            # of two slanted labels overlap without the labels touching, and
            # that read as "no room" for every row of a slanted list.
            grow_w = int(block.get("grow_limit") or 0)
        else:
            grow_w = _free_width_near(block, blocks, region[2]) if region else 0
            if block.get("grow_limit") is not None:
                grow_w = min(grow_w, int(block["grow_limit"]))
        # Short HUD labels (MAX COMBO, a judgement) sit next to empty
        # panel. Growing into that emptiness stretches two words across
        # half the screen and picks up the neighbour's colour.
        src_words = len(str(block.get("source") or "").split())
        if block.get("tight") and block.get("oneline") and src_words <= 4:
            grow_w = 0
        fitted = fit_layout(
            cr,
            block["text"],
            block.get("src_w", block.get("w", 40)),
            box_h or block.get("src_h", block.get("h", 16)),
            block["font"],
            oneline=bool(block.get("oneline")),
            align=str(block.get("align") or "left"),
            keep_breaks=bool(block.get("keep_breaks")),
            tight=bool(block.get("tight")),
            family=_family_for(block),
            italic=bool(block.get("italic")),
            fill_width=bool(block.get("fill_width")),
            tracking=float(block.get("tracking", 0.0) or 0.0),
            weight=block.get("weight"),
            grow_w=grow_w,
            max_lines=int(block.get("src_lines") or 0),
            line_pitch=int(block.get("line_pitch") or 0),
        )
        if fitted is None:
            continue
        layout, w, h, pad_x, pad_y = fitted
        turn = float(block.get("angle", 0.0) or 0.0)
        base_w = float(block.get("src_w") or w)
        if abs(turn) >= 1.2 and w > base_w:
            # A slanted card turns about its own centre, and a wider card has
            # a different centre: turned there, the part over the original
            # slid along the slant and off the letters it was there to cover.
            # The room it grew into is along the line, so the centre moves
            # along the line by half of it.
            extra = (w - base_w) / 2.0
            rad = math.radians(turn)
            x = x - extra + extra * math.cos(rad)
            y = y + extra * math.sin(rad)
        if region:
            _rx, _ry, rw, rh = region
            # блоки в координатах региона (окно слоя = geom)
            x = clamp(int(x), 0, max(0, rw - int(w)))
            y = clamp(int(y), 0, max(0, rh - int(h)))
            # A translation is longer than the text it replaces, so the card
            # grows past its own OCR box and lands on whatever is next to it -
            # which is how a heading's translation ended up written across the
            # elements beside it. Shrinking the card to fit the space its own
            # neighbours leave is better than covering them: a slightly small
            # card is readable, a card over three other elements is not.
            # The element's own box, kept so the fit can tell this card's
            # source text from a neighbour it happens to be drawn over.
            block.setdefault("ox", block.get("x", x))
            block.setdefault("oy", block.get("y", y))
            bx, by = x, y
            x, y, w, h = _fit_between_neighbours(
                x, y, w, h, block, placed, rw, rh
            )
            if (x, y) != (bx, by):
                tlog(f"ui-move {bx},{by} -> {x},{y} size={w}x{h}")
            placed.append(
                card_quad(
                    x + w * 0.5,
                    y + h * 0.5,
                    w,
                    h,
                    float(block.get("angle", 0.0) or 0.0),
                )
            )

        # cover: ширина по OCR; высота — не раздувать пустотой под текстом
        # tight UI: рамка = OCR bbox, без раздувания
        if not block.get("tight"):
            src_w = int(block.get("src_w", w))
            # The height the card was fitted to, which may be less than its own
            # box when the element below it starts inside it. Re-reading the box
            # here would undo that and paint the last line under the neighbour.
            src_h = int(box_h or block.get("src_h", h))
            if block.get("cover") and block.get("kind") in ("dialogue", "body"):
                w = max(w, src_w)
                # A card shorter than the text it replaces leaves the bottom
                # lines of the original showing under it. The text is centred
                # in the height instead, so the extra is shared above and below.
                if src_h > h:
                    pad_y += (src_h - h) / 2.0
                    h = src_h
            elif block.get("fit_text"):
                w = max(w, min(src_w, w + 24))
                h = max(h, min(src_h, h + 10))
            else:
                w = max(w, src_w)
                h = max(h, src_h)

        angle = float(block.get("angle", 0.0) or 0.0)
        # cache blit is gated until paint→put is wired (get-only is dead).
        _ = CARD_CACHE, CardCacheKey

        cr.save()
        if abs(angle) >= 1.2:
            cx, cy = x + w * 0.5, y + h * 0.5
            cr.translate(cx, cy)
            cr.rotate(math.radians(angle))
            cr.translate(-cx, -cy)

        bg = block.get("bg", active.card_bg())
        # Цвет снят с экрана — плашка неотличима от подложки, и её геометрия не
        # должна быть видна: ни скруглённых углов, ни обводки. Обе вещи existed
        # только ради «карточки», а плашка, повторяющая исходный цвет, и есть
        # исходный фон. Скругление оставляло в углах куски оригинала, а обводка
        # рисовала серую рамку там, где игра её не рисовала, — на зелёной кнопке
        # это читалось как «у слова селект рамка другая».
        measured = bool(block.get("bg_measured"))
        if measured:
            cr.rectangle(x, y, w, h)
        else:
            r = min(active.card_radius(), min(w, h) / 3.0)
            cr.new_sub_path()
            cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
            cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
            cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
            cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
            cr.close_path()
        bg_a = bg[3] if len(bg) > 3 else 0.55
        # A card sits on top of the text it replaces, so what matters is not
        # whether the scene shows faintly through but whether the sentence the
        # card is there to replace can still be read through it. The original
        # was about 170 levels darker than the panel it sat on; at ten per cent
        # transmission that is seventeen levels of ghost text beside the
        # translation, which on a codex screen left "ra of Light" sitting inside
        # the card that read "Исследование мира: Синие столбы света". At one and
        # a half per cent it is under three, which is not readable.
        cr.set_source_rgba(*bg[:3], clamp(float(bg_a), 0.985, 1.0))
        cr.fill_preserve()
        if not measured:
            # Обводка есть только у заглушки. У плашки, повторяющей фон экрана,
            # рамка была бы выдуманным элементом интерфейса.
            if bg[0] > 0.5:
                cr.set_source_rgba(0.45, 0.45, 0.50, 0.28)
            else:
                cr.set_source_rgba(0.55, 0.58, 0.62, 0.16)
            cr.set_line_width(1.0)
            cr.stroke()
        else:
            cr.new_sub_path()
            cr.rectangle(x, y, w, h)
            cr.clip()

        cr.rectangle(x, y, w, h)
        cr.clip()

        cr.move_to(x + pad_x, y + pad_y)
        PangoCairo.layout_path(cr, layout)
        fg = block.get("fg", active.card_fg())
        stroke = block.get("stroke")
        stroke_w = float(block.get("stroke_w") or 0.0)
        if stroke and stroke_w > 0.5:
            cr.set_source_rgba(*stroke[:3], stroke[3] if len(stroke) > 3 else 0.95)
            cr.set_line_width(stroke_w)
            cr.set_line_join(cairo.LineJoin.ROUND)
            cr.stroke_preserve()
        elif bg[0] < 0.5 and not block.get("fill_width"):
            # лёгкий контур только у обычных тёмных карточек
            cr.set_source_rgba(0.0, 0.0, 0.0, 0.45)
            cr.set_line_width(1.4)
            cr.stroke_preserve()

        # A line on a game screen is often one colour with a name, a number or a
        # keyword in another, and filling the whole layout with one colour throws
        # the highlight away - the translation comes out uniformly coloured where
        # the original emphasised something. The spans were measured from the
        # original's own pixels, so this only paints what was actually there.
        segments = block.get("color_spans") or []
        # Сравнивать прогоны между собой, а не с основным цветом строки. Сравнение
        # с основным выбрасывало и сам основной прогон - он от него и не
        # отличается, - и две заливки в строке становились одной: в строке
        # жёлтое имя и розовое "Purchase", основным считалось жёлтое, и
        # оставался один прогон, то есть ровно то, чего эта проверка искала.
        usable = [
            s for s in segments if float(s.get("share", 0.0)) >= COLOR_SPAN_MIN_SHARE
        ]
        # Остаются прогоны, заметно отличающиеся друг от друга; соседние с одним
        # оттенком - это один и тот же цвет, разбитый пробелом.
        distinct: list[dict] = []
        for s in usable:
            hue = color_hue(s.get("color", (1, 1, 1)))
            # all() по пустому списку истинно, и без проверки на пустоту первый
            # прогон отбрасывался сам с собой - а он и есть первый цвет строки.
            # Список оставался пустым, и любая строка рисовалась одним цветом.
            rgb = tuple(float(c) for c in s.get("color", (1, 1, 1))[:3])
            if distinct and all(
                _hue_gap(hue, color_hue(k.get("color", (1, 1, 1)))) <= COLOR_MIN_HUE_GAP
                # White and black have no hue, so a hue test calls them the
                # same as any colour; they are not.
                and sum(
                    (a - float(b)) ** 2 for a, b in zip(rgb, k.get("color", (1, 1, 1))[:3])
                )
                < 0.09
                for k in distinct
            ):
                continue
            distinct.append(s)
        usable = distinct
        if len(usable) < 2:
            # One colour, or nothing distinct enough to be a highlight.
            # Если у нас есть ровно один цвет и он заметно отличается от дефолтного fg,
            # используем его вместо дефолтного — это чинит тёмно-фиолетовый текст
            # в низу экрана, который иначе получал яркий fg по умолчанию.
            if len(usable) == 1:
                only = usable[0]
                only_rgb = tuple(float(c) for c in only.get("color", (1,1,1))[:3])
                fg_rgb = tuple(float(c) for c in fg[:3])
                # Сравниваем по евклидову расстоянию в RGB; порог подобрать эмпирически
                dist = sum((a-b)**2 for a,b in zip(only_rgb, fg_rgb))**0.5
                if dist > 0.25:
                    cr.set_source_rgba(*only_rgb, fg[3] if len(fg) > 3 else 1.0)
                    cr.fill()
                    cr.restore()
                    continue
            # One colour, or nothing distinct enough to be a highlight.
            cr.set_source_rgba(*fg[:3], fg[3] if len(fg) > 3 else 1.0)
            cr.fill()
            cr.restore()
            continue

        # One path per span, clipped to its own share of the line, so a
        # highlight in the middle of the line lands on the middle of the
        # translation rather than on its first word.
        total_ink = max(1e-6, sum(float(s.get("share", 0.0)) for s in usable))
        _fill_by_color_spans(
            cr, x, y, w, h, pad_x, pad_y, layout, fg, usable, total_ink,
            source=str(block.get("source") or ""),
        )
        cr.restore()
