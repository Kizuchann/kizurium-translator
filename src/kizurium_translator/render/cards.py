"""Карточка: как перевод ложится в рамку, которую занимал оригинал.

Отрисовка перевода - это три разных вопроса, и жили они в одном файле:

* какую рамку занимает карточка и каким кеглем в неё ложится перевод - здесь,
  в `make_block` и `fit_layout`;
* где именно на экране лежат углы уже нарисованной карточки - `card_quad`;
* как всё это рисуется - `render/overlay.py`.

`make_block` превращает распознанную строку в словарь блока: размер рамки,
кегль, семейство, наклон, цвета плашки и чернил, цветные прогоны по словам.
`fit_layout` - вторая половина того же: по готовой рамке подбирает кегль,
перенос и сжатие глифей так, чтобы текст влез и закрыл оригинал.

Модуль ничего не знает про распознавание и про переводчик: на входе уже
переведённая строка и её бокс, на выходе - блок и раскладка.

Тела перенесены из `live.py` дословно."""
from __future__ import annotations

import math
import re

from .. import trace as trace_mod

try:
    from ..layer_shell_lib import preload as _preload_layer_shell

    _preload_layer_shell()
except Exception:  # noqa: BLE001
    pass
try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Pango", "1.0")
    import cairo
    from gi.repository import Gtk, Pango, PangoCairo
except Exception:  # noqa: BLE001 - слои разбираются без окна
    Gtk = Pango = PangoCairo = cairo = None  # type: ignore[assignment]
import cairo
from gi.repository import Gtk
from PIL import Image

from ..core.text import (  # noqa: F401
    _contrast_ratio,
    _empty_run_right,
    clamp,
    pack_translation_to_rows,
    row_pitch,
    sample_bubble_cover_colors,
    sample_ocr_cover_colors,
    source_rows,
    tdetail,
    tlog,
    weight_for_stroke,
)
from ..typography.metrics import (  # noqa: F401
    RE_JPN,
    detect_overlay_typeface,
    edge_icon_trim,
    edge_panels,
    extend_leading_glyphs,
    extend_trailing_glyphs,
    ink_is_light,
    ink_line_count,
    locate_slant_band,
    measure_glyph_metrics,
    measure_slant_band,
    measure_word_colors,
    sample_outline_color,
    slant_room_ahead,
)

CARD_BG_DEFAULT = (0.05, 0.07, 0.12, 0.55)


CARD_FG_DEFAULT = (1.0, 1.0, 1.0, 1.0)


CARD_RADIUS = 7.0


CARD_WIDTH_MAX_RATIO = 1.35


def make_block(par: dict, translated: str, region_img: Image.Image, rx: int, ry: int, rw: int, rh: int) -> dict:
    px1, py1, px2, py2 = par["box"]
    raw_w = max(8, px2 - px1)
    raw_h = max(8, py2 - py1)
    # A slanted line comes back as the box that contains it, not as the line
    # itself: at 15 degrees a line of 40 pixels of type is 130 pixels tall in
    # its box. Laid out from that height the card is three times too tall, its
    # type size is taken from the diagonal instead of the glyphs, and the text
    # it draws has to break to fit a shape that is not on the screen. The
    # original's own length and thickness are recovered from the angle, which
    # is what the quadrilateral the recogniser returned already knew.
    skew = float(par.get("angle", 0.0) or 0.0)
    src_preview = str(par.get("text", "") or "")
    # A line written down the page comes back as a tall narrow box, often with
    # no angle at all. The slant solver then treats the height as padding and
    # the card is drawn sideways through the middle of the word. The box
    # already bounds those glyphs; the card is rotated onto it later.
    vertical_line = abs(skew) >= 75.0 or (
        abs(skew) < 12.0
        and raw_h > raw_w * 2.1
        and raw_w >= 8
        and "\n" not in src_preview
        and len(src_preview.strip()) >= 2
    )
    draw_angle = skew if abs(skew) >= 45.0 else (-90.0 if vertical_line else skew)
    if vertical_line:
        skew = 0.0
    if abs(skew) >= 1.0:
        rad = math.radians(skew)
        cos_a, sin_a = abs(math.cos(rad)), abs(math.sin(rad))
        # Solve w*cos + h*sin = raw_w and w*sin + h*cos = raw_h for w and h.
        det = cos_a * cos_a - sin_a * sin_a
        if abs(det) > 1e-3:
            line_w = (raw_w * cos_a - raw_h * sin_a) / det
            line_h = (raw_h * cos_a - raw_w * sin_a) / det
            if line_w >= 8 and line_h >= 6:
                # Only trust it when both come back sane: a very shallow
                # angle makes the system ill-conditioned and the answer grows
                # without bound, and a box that covers two lines would then be
                # "corrected" into a single enormous one.
                if line_w <= raw_w * 1.05 and line_h <= raw_h:
                    raw_w = int(round(line_w))
                    raw_h = int(round(line_h))
                else:
                    line_h = raw_h * cos_a
                    if line_h >= 6:
                        raw_h = int(round(line_h))
    else:
        skew = 0.0
    if abs(skew) >= 1.0:
        # What the angle gives back is the shape of the band plus the recogniser's
        # padding, and only the band is the type. Three answers, in order of how
        # much they can be trusted: the quadrilateral's own short side, which is
        # the recogniser measuring the line it found; the ink measured across the
        # line, which is right where there is a real gap to find the edge of the
        # band at and wrong where something else in the crop is not a gap; and
        # the arithmetic, which cannot tell the band from the padding at all.
        px1, py1, px2, py2 = par["box"]
        band = int(par.get("band") or 0)
        if band < 6:
            band = measure_slant_band(region_img, (px1, py1, px2, py2), skew)
        if band >= 6:
            # The box's width is the line's length plus the climb of its
            # thickness; only the first is covered by a card turned onto the
            # line. The whole width ran a card for "SCORE" onto the score.
            rad = math.radians(skew)
            along = (max(8, px2 - px1) - band * abs(math.sin(rad))) / max(0.2, abs(math.cos(rad)))
            raw_w = max(8, min(px2 - px1, int(round(along))))
            raw_h = band
        tdetail(f"ui-skew angle={skew:+.1f} box={px2 - px1}x{py2 - py1} band={band}")
        skew, band_off = locate_slant_band(region_img, (px1, py1, px2, py2), skew, raw_h)
    else:
        band_off = 0.0
    # The card is drawn rotated about the centre of its own box, so where its
    # centre sits decides where it lands, and that is the one thing that has to
    # be right before anything else about it.
    #
    # A card is shorter than the box it came out of either way - measured, or
    # fitted from the angle - and both answers are centred on the box's centre,
    # so the top comes back down by half of what was lost and the middle stays
    # where the letters are. Leaving the top at the box's own top instead put the
    # card a full half-height above the line it covers, and on a row of labels at
    # fifteen degrees that is more than twenty pixels: the card sat in the gap
    # above its own text with the text showing through below it.
    dy = 0
    if skew:
        box_h = max(8, py2 - py1)
        dy = int(round((box_h - raw_h) * 0.5))
        py1 += dy
        py2 = py1 + raw_h
        # Width, too: the same shortening applies across the box, and the card
        # is centred on it.
        box_w = max(8, px2 - px1)
        dx = int(round((box_w - raw_w) * 0.5))
        px1 += dx
        px2 = px1 + raw_w
        if band_off:
            # The letters are not where the box's centre is; the card goes to
            # the letters, along the line's normal.
            rad = math.radians(skew)
            sx = int(round(-band_off * math.sin(rad)))
            sy = int(round(band_off * math.cos(rad)))
            px1, px2, py1, py2 = px1 + sx, px2 + sx, py1 + sy, py2 + sy
            tdetail(f"ui-skew-shift off={band_off:+.1f} -> ({sx},{sy}) angle={skew:+.1f}")
        # Along-width already drops the climb of the band from the OCR box, which
        # is what kept SCORE off its digits. A further ink-extent trim was tried
        # and pulled white-on-magenta titles ("Wonderful Pain") to half their
        # length whenever AA along the band looked like a neighbour.
    # What the glyphs actually measure, not what the engine thinks a line is.
    # Everything below lays the card out from these, which is the whole point:
    # a line of tracked-out capitals and a tight lowercase line are the same
    # size in the request and nothing alike on screen.
    # Named apart from the local `ink` below, which is an outline colour and
    # predates this: reusing one name for a measurement and a colour is how a
    # tuple ends up where a GlyphMetrics was expected.
    if not skew and not vertical_line and region_img is not None:
        cut_l, cut_r = edge_icon_trim(region_img, (px1, py1, px2, py2), str(par.get("text", "")))
        if cut_l or cut_r:
            px1, px2 = px1 + cut_l, px2 - cut_r
            raw_w = px2 - px1
    glyphs = measure_glyph_metrics(region_img, (px1, py1, px2, py2))
    # A name plate's box is the recogniser's rectangle, which starts above the
    # letters and, on a character select screen, runs into the sprite. The
    # card then sits in that padding — small, and higher than the word — and
    # the last letter past the box ("Reimu" read as "Reim") stays visible.
    if (
        str(par.get("kind") or "") == "name"
        and glyphs.height >= 16
        and not skew
        and not vertical_line
    ):
        # Full ink height, not the dense stripe. On these titles the stripe
        # was the middle of the letters, and a card that tall sat above the
        # rest of the word.
        letter_h = glyphs.height
        grown = extend_trailing_glyphs(
            region_img,
            (px1, py1, px2, py2),
            color=glyphs.color,
            band_top=glyphs.top,
            band_h=letter_h,
        )
        if grown[2] > px2:
            px2 = grown[2]
            glyphs = measure_glyph_metrics(region_img, (px1, py1, px2, py2))
            letter_h = glyphs.height
        lead = extend_leading_glyphs(
            region_img,
            (px1, py1, px2, py2),
            color=glyphs.color,
            band_top=glyphs.top,
            band_h=letter_h,
        )
        if lead[0] < px1:
            px1 = lead[0]
            glyphs = measure_glyph_metrics(region_img, (px1, py1, px2, py2))
            letter_h = glyphs.height
        if glyphs.left > 1:
            px1 += glyphs.left
        if glyphs.top > 1:
            py1 += glyphs.top
        raw_h = max(8, letter_h)
        raw_w = max(8, px2 - px1)
        py2 = py1 + raw_h
    elif (
        str(par.get("kind") or "") == "dialogue"
        and glyphs.height >= 12
        and glyphs.top > 2
        and raw_h < 80
        and not skew
        and not vertical_line
    ):
        # "CV:" was drawn a few pixels off the letters: the box starts in the
        # padding, and the card followed the box.
        lead = extend_leading_glyphs(
            region_img,
            (px1, py1, px2, py2),
            color=glyphs.color,
            band_top=glyphs.top,
            band_h=glyphs.height,
        )
        if lead[0] < px1:
            px1 = lead[0]
            if not str(par.get("text") or "").lstrip().startswith((".", "…", "·")):
                translated = ".." + (translated or "").lstrip()
            glyphs = measure_glyph_metrics(region_img, (px1, py1, px2, py2))
        if glyphs.left > 1:
            px1 += min(glyphs.left, 24)
        py1 += glyphs.top
        raw_h = max(8, glyphs.height)
        raw_w = max(8, px2 - px1)
        py2 = py1 + raw_h
    ink_h = glyphs.height if glyphs.height >= 8 else 0
    ink_w = glyphs.width if glyphs.width >= 8 else 0
    if abs(skew) >= 1.0 and raw_h >= 8:
        # On a slanted line the box above is a strip through the middle of the
        # line, not a box round it: the line climbs, so a strip tall enough to
        # hold the type only contains the middle of the words at either end and
        # the ink measure reads the middle of the alphabet. The band's own
        # thickness is the recogniser's measurement of the line it found, and on
        # a single line that is the size of the type - which is the number the
        # rest of this function is asking for.
        ink_h = raw_h
    src_text = str(par.get("text", ""))
    nbreaks = max(src_text.count("\n"), (translated or "").count("\n"))
    # How many lines the original had. Counted from the rows the paragraph was
    # read as, because the text a translator returns has no newlines in it: a
    # two-line game description arrives as one string, so counting breaks in it
    # gave the card a budget of one line and the whole paragraph was condensed
    # into a single row of unreadable type sitting in a box built for two.
    rows = source_rows(par)
    kind = str(par.get("kind", "") or "")
    lh = int(par.get("line_height", 14))
    paragraph_like = kind in ("dialogue", "body") or bool(par.get("wrap"))
    # Rows are only usable if they add up to the box they were found in. A
    # recogniser that merged three lines into one "row" and reported one real
    # line beside it says two rows 95 pixels apart inside a 126-pixel box:
    # taking that at its word gave the card a 95-pixel leading and a 74-pixel
    # starting size, the fitting shrank the type until two lines fitted under
    # that leading, and the card came out holding two lines of small type at
    # the top of a box built for four.
    if len(rows) >= 2 and not _rows_match_box(rows, raw_h):
        tdetail(f"rows-drop n={len(rows)} box_h={raw_h}")
        rows = []
    src_lines = max(1, nbreaks + 1, len(rows))
    if not rows and paragraph_like and lh > 0 and raw_h >= lh * 2:
        # No rows, but the recogniser said how tall a line of this text is, and
        # that is the one number that can divide the box. A codex panel returned
        # a 165-pixel box for a five-line paragraph and a line height of 33; the
        # box was read as two lines, the type was sized from half the box, and
        # the card came out holding two lines of small type with a band of
        # empty panel under it.
        src_lines = max(src_lines, int(raw_h / max(1, lh)))
    if len(rows) >= 2:
        # A translation that arrived as one line still has to land on the rows
        # the original occupied: where each line started, how far the next one
        # was from it, where the last one ended.
        parts = pack_translation_to_rows(rows, translated or src_text)
        translated = "\n".join(p for p in parts if p) or translated
    tlen = max(1, len(re.sub(r"\s+", "", translated or src_text)))
    angle = draw_angle if vertical_line else (skew or float(par.get("angle", 0.0) or 0.0))
    ocr_cx = (px1 + px2) / 2
    narrow = rw < 720
    pin_box = bool(par.get("pin_box")) or kind == "dialogue-line"
    ui_label = kind in ("ui", "chip", "menu", "latin") or (
        narrow and kind not in ("dialogue", "body", "dialogue-line")
    )
    centered = (
        (not ui_label or pin_box)
        and (not narrow or pin_box)
        and (
            abs(ocr_cx - rw / 2) <= rw * 0.18
            or kind in ("dialogue", "body", "dialogue-line")
            or pin_box
        )
    )
    if kind in ("dialogue", "body", "dialogue-line") or pin_box:
        centered = False

    tip_tall = False
    font_family = "Sans"
    italic = False
    display_title = False
    room_px = None
    if ui_label or pin_box:
        pad = 2
        # ширина/высота строго по OCR — длинный RU ужимаем шрифтом внутри бокса
        src_w = min(rw, raw_w + pad * 2)
        src_h = min(rh, max(10, raw_h + pad))
        if skew:
            # A slanted letter's edge is smeared over two pixels by the
            # antialiasing, and a card that stops one short of it leaves the
            # line's bottom showing as a dashed rule. Half a degree of error in
            # the measured angle is another pixel at either end of the line.
            src_h = min(rh, max(10, raw_h + pad * 3))
        body_wrap = kind == "body" or (pin_box and (par.get("wrap") or nbreaks >= 1))
        if body_wrap:
            # Сколько строк в боксе — по разрывам между ними, а не по высоте.
            # Высота говорит только о том, сколько места бокс занимает, и
            # читать её как число строк значит угадывать кегль: двадцать с
            # небольшим пикселей на строку. Для интерфейсного текста это правда,
            # для крупного заголовка — нет, и 143-пиксельное имя выходило
            # шестью строками по 23, то есть плашкой под абзац с кеглем 17 и
            # маленькой строкой текста наверху большой пустой панели.
            #
            # Бокс говорит, сколько в нём строк, разрывами: у абзаца между
            # строками есть пустые ряды, а у одной строки - нет, как бы
            # крупной она ни была. Два пикселя - порог, потому что одна пустая
            # строка это то, что бывает у плотно набранного текста между
            # ножками одной строки и верхами следующей, и считать их -
            # прочесть любой абзац как одну полосу букв.
            if rows:
                # The detector already measured every rendered line: PP-OCRv6
                # hands back one quad per line, not one quad per paragraph, and
                # the rows come from those quads. Counting ink bands on top of
                # that can only ever add a line the screen does not have - which
                # is the whole class of bug where a paragraph came out with an
                # extra row and the card spilled past its own box.
                est_lines = len(rows)
            else:
                est_lines = max(
                    src_lines,
                    nbreaks + 1,
                    ink_line_count(region_img, (px1, py1, px2, py2)),
                )
            tip_tall = est_lines >= 3
            # A line's own height, not the box's divided by the count: the rows
            # were measured one at a time, and dividing the box trusts them to
            # share it evenly. On a sheet with a paragraph and a short line
            # after it, that short line's own height is what sizes the type.
            per_h = max(10, int(raw_h / est_lines))
            if len(rows) >= 2:
                per_h = max(int(r["box"][3] - r["box"][1]) for r in rows)
                # The leading bounds a line, and that is arithmetic, not taste:
                # no line of text is taller than the gap to the next one. A long
                # quest description handed back five rows in
                # a 145-pixel box with a 30-pixel pitch, but every one of those
                # row boxes was 145 pixels tall - the whole paragraph each - and
                # taking their height at its word sized the type at 134 pixels.
                # The pitch is the number that survived, so the pitch is what
                # measures the line.
                pitch = row_pitch(rows)
                if pitch > 0:
                    per_h = min(per_h, int(pitch * 1.15))
            elif lh > 0 and est_lines >= 2:
                # No rows to measure, and the recogniser's own line height is
                # what it measured one line of this text to be.
                per_h = max(per_h, min(raw_h, lh))
            glyph = max(10, per_h - 2)
            font = clamp(
                int(glyph * 0.82),
                10,
                max(28, min(int(raw_h) - 1, glyph)),
            )
            fit_text = True
            oneline = False
            cover = True
            tight = True
            pad = 4 if tip_tall else 3
            src_w = min(rw, raw_w + pad * 2)
            src_h = min(rh, max(raw_h + pad, int(font * 1.15 * est_lines) + pad * 2))
            src_h = min(src_h, max(raw_h + pad, int(raw_h * 1.06) + 4))
        else:
            # A single-line label: the ink height is the type size, and it is
            # measured rather than assumed. A box the recogniser padded around
            # a 12px label came out at a 22px font, which is the "text sits too
            # high and too big" that screenshots kept showing.
            glyph = ink_h or max(12, raw_h - 2)
            cap = _cap_height(glyphs, ink_h, src_text)
            # The font may not run past the ink it was measured from, and it is
            # not held to a constant. The 42-pixel ceiling was there to stop a
            # font running away on a box whose ink came from several merged lines
            # - and in this branch there is no such box, it is a single line and
            # the ink is that line. What the ceiling actually did was hold back
            # any genuinely large type: a character name at 139 pixels of ink
            # came out at a 42-pixel font in a card 145 tall, which is a large
            # empty panel with a small line of text sitting at the top of it, and
            # that is what the screenshot showed. What does bound the font is the
            # card it has to sit in, so that is the bound; the rules that fit a
            # line to its box - condense, then wrap, then shrink - all live
            # further down and none of them could act on a font capped here.
            font = clamp(int(glyph * 0.92), 11, max(42, min(int(raw_h) - 1, glyph)))
            if cap:
                # Pango draws a capital about 1.07 times the size it is given.
                font = clamp(int(round(cap * 0.94)), 11, max(42, min(int(raw_h) - 1, glyph)))
            # A slanted band is the type's own thickness. A short capital from
            # a dense mid-stripe of the letters (or from a half-mask) is not:
            # SCORE came out at 24 in a 59-pixel band while the original filled
            # the band.
            if skew and ink_h and (not cap or cap < ink_h * 0.7):
                font = clamp(int(round(ink_h * 0.94)), 11, max(42, min(int(raw_h) - 1, ink_h)))
            # line_height is one line, where raw_h may be the whole box the
            # recogniser returned. A font sized to the box of a three-line merge
            # lands well below the lines it belongs to.
            # `line_height` — оценка движка, `ink_h` — измерение по пикселям.
            # Оценка ужимает кегль, когда бокс распознан с полями: у «Buro» бокс
            # 236x103 при высоте глифов 78, line_height приходил 32, и кегль 71
            # ужимался до 34. Перевод выходил вчетверо мельче оригинала в рамке,
            # которая накрывала его целиком, — пустое место снизу и справа.
            #
            # Зажим остаётся для случая, когда измерения нет: там он по-прежнему
            # спасает от бокса, в который слились несколько строк.
            if not ink_h:
                cap_h = max(8, int(par.get("line_height") or 0) or raw_h)
                font = min(font, max(11, cap_h + 2))
            fit_text = True
            oneline = True
            cover = True
            tight = True
            # The recogniser's boxes say where the other labels are and nothing
            # about icons, so the card may also grow only over what the pixels
            # show to be clear.
            if region_img is None:
                room_px = 0
            elif skew:
                room_px = slant_room_ahead(
                    region_img,
                    ((px1 + px2) / 2.0, (py1 + py2) / 2.0),
                    skew,
                    raw_h + pad * 2,
                    raw_w / 2.0 + pad,
                )
            else:
                room_px = _empty_run_right(region_img, py1, px2, py2, px1=px1)
            # длинный RU на кнопке/заголовке — чуть шире + мельче, не обрезать «сейчас»
            ru_len = len(re.sub(r"\s+", "", translated or ""))
            en_len = max(1, len(re.sub(r"\s+", "", src_text)))
            if ru_len > en_len * 1.12:
                # The card keeps the width of the text it covers. It used to be
                # widened by the ratio of the two lengths, which pushed the
                # translation past the right edge of the original and, since
                # cards are laid out around their box, lifted it clear of the
                # line entirely: "Victorian Empire" translated to a taller card
                # sitting above it with the English still visible underneath.
                #
                # fit_layout condenses the glyphs to make a longer translation
                # fit the width it was given, so the card can stay where the
                # original is. Growing is the last resort, and it needs room to
                # grow into: a stat panel reads "Spell Cooldown:" on the left and
                # its number on the right, and a card 40 per cent wider than the
                # label it covers lands on the number. So the space to the right
                # is measured from the pixels, and where something is standing
                # there the width is kept and the type is condensed instead.
                stretch = min(1.4, ru_len / float(en_len))
                want = raw_w + pad * 2
                room_right = room_px
                short_hud = len(src_text.split()) <= 4 and oneline
                if short_hud or int(raw_w * stretch) <= want + max(24, raw_w // 3):
                    src_w = min(rw, want)
                    tdetail(f"ui-condense-fit '{translated[:28]}' w={raw_w}")
                elif room_right < int(raw_w * (stretch - 1.0)):
                    src_w = min(rw, want)
                    tdetail(
                        f"ui-condense-crowded '{translated[:28]}' w={raw_w} room={room_right}"
                    )
                else:
                    # Grow, but never past what is actually clear to the right.
                    # The neighbour is a fixed distance away and a card that
                    # reaches it is a card that covers it, so the space measured
                    # is the space used.
                    limit_w = want + max(0, room_right - 4)
                    src_w = min(rw, max(want, min(int(raw_w * stretch), limit_w)))
                    tlog(f"ui-widen '{translated[:28]}' {raw_w}->{src_w} room={room_right}")
        font_family, italic, display_title = detect_overlay_typeface(
            region_img,
            (px1, py1, px2, py2),
            angle=angle,
            raw_w=raw_w,
            raw_h=raw_h,
            src_text=src_text,
            kind=kind,
        )
        if display_title and kind != "name":
            # заголовок: плашка = бокс оригинала, шрифт под высоту глифа
            pad = 3
            src_w = min(rw, max(raw_w + pad * 2, int(raw_w * 1.02)))
            src_h = min(rh, max(raw_h + pad, int(raw_h * 1.04)))
            font = clamp(int(raw_h * 0.78), 14, min(48, raw_h - 1))
            oneline = True
            tight = True
            cover = True
            # центрируем как оригинал, если строка около центра
            if abs(ocr_cx - rw / 2) <= rw * 0.22:
                centered = True
        if pin_box and centered or centered:
            x = clamp(int(ocr_cx - src_w / 2), 0, max(0, rw - src_w))
        else:
            x = clamp(px1 - pad, 0, max(0, rw - src_w))
        y = clamp(py1 - (pad if skew else pad // 2), 0, max(0, rh - src_h))
    else:
        pad = 6
        ocr_w = raw_w + pad * 2
        ocr_h = raw_h + pad * 2
        if kind in ("dialogue", "body") or par.get("wrap"):
            # The height of a *line*, not of the box. OCR routinely returns one
            # box for three stacked lines, so raw_h is three lines tall and a
            # glyph sized from it is three times too big - which is what put a
            # 27px font on a 14px line and made the overlay swallow the window
            # it was translating. line_height is the engine's own answer to how
            # tall one line of this text is, and it survives the merging that
            # inflates the box. It is a floor only: a box that really is one tall
            # line still gets its tall line back.
            per_line_h = max(16, int(raw_h / max(1, src_lines)))
            if lh > 0:
                per_line_h = min(per_line_h, max(16, int(lh) + 4))
            if ink_h:
                per_line_h = min(per_line_h, max(12, ink_h + 2))
            glyph = clamp(per_line_h - 2, 12, 32)
            font = clamp(int(glyph * 0.86), 15, 30)
            # Ширина плашки — от оригинала, а не от длины перевода. Раньше здесь
            # стояло `glyph * 0.55 * tlen`, и ширина росла вместе с переводом:
            # в одном разборе оригинал в 236px давал плашку 619px - в 2.6 раза
            # шире, с текстом, уехавшим за свою область. Длинный перевод должен
            # переноситься, а не растягивать карточку: если строка не влезает в
            # ширину оригинала, она ложится в две строки, а высота растёт.
            wanted_w = int(glyph * 0.55 * min(tlen, 48) + pad * 2)
            cap_w = int(raw_w * CARD_WIDTH_MAX_RATIO) + pad * 2
            src_w = min(int(rw * 0.92), max(ocr_w, min(cap_w, wanted_w)))
            src_w = max(src_w, min(int(rw * 0.88), ocr_w))
            src_h = max(ocr_h, int(font * 1.28 * src_lines) + pad * 2)
            if src_lines >= 2 or ocr_h >= max(44, int(glyph * 2.0)):
                src_h = max(src_h, ocr_h)
            fit_text = True
            # OCR often returns two stacked lines as one string and one tall box.
            # Counting only newline characters then draws a single row and leaves
            # the second source line showing under the card.
            # Подписи и подсказки: строки детектора здесь и есть ответ. Оценка
            # по чернильным полосам нужна только когда детектор не дал строк
            # вовсе - иначе она может лишь добавить несуществующую строку.
            if rows:
                src_lines = len(rows)
            else:
                ink_lines = ink_line_count(region_img, (px1, py1, px2, py2))
                if ink_lines >= 2:
                    src_lines = max(src_lines, ink_lines)
                elif raw_h >= max(48, int(glyph * 1.85)) and tlen >= 40:
                    # Two ink bands sometimes read as one stripe; a box this
                    # tall on a sheet is still two lines, not one condensed run.
                    src_lines = max(src_lines, 2)
            oneline = src_lines == 1
            if wanted_w > cap_w and src_lines == 1:
                # Stay one line and condense. Opening a second row here is
                # how a two-line sheet description became a three-line card.
                oneline = True
                trace_mod.decide(
                    "card-width",
                    "condense",
                    ocr_w=raw_w,
                    wanted=wanted_w,
                    cap=cap_w,
                    chars=tlen,
                    ratio=CARD_WIDTH_MAX_RATIO,
                )
            cover = True
            tight = False
        else:
            # Однострочный текст: в src_text нет переносов - это подпись, а не абзац.
            # Тогда lh = реальная высота бокса, oneline=True, шрифт от реальной высоты.
            is_single_line = chr(10) not in (src_text or "") and chr(13) not in (src_text or "")
            if is_single_line:
                lh = raw_h
            elif lh > 28:
                lh = 16
            if not vertical_line and ocr_w < ocr_h * 0.9 and tlen >= 3:
                from ..core.scale import px_per_char

                pitch = px_per_char(lh, mult=0.65)
                ocr_w = max(ocr_w, min(int(rw * 0.45), tlen * pitch + 40))
                ocr_h = max(lh + pad * 2, min(lh * 2 + 18, max(56, int(lh * 3.2))))
                tdetail(f"box-fix-vertical '{translated[:28]}' -> {ocr_w}x{ocr_h}")
            # Оценка ширины по длине перевода нужна, чтобы короткий русский не
            # растягивал плашку. Но она же была и верхним пределом: когда перевод
            # короче оригинала, плашка выходила уже бокса. Pitch from line height
            # (not fixed 14px) so HiDPI / large fonts cover the OCR box.
            from ..core.scale import px_per_char as _ppc

            pitch = _ppc(lh, mult=0.75)
            src_w = max(ocr_w, min(ocr_w, max(56, min(rw, tlen * pitch + 36))))
            if src_w < ocr_w:
                tdetail(
                    f"card-w-floor '{translated[:28]}' {ocr_w}->{src_w}"
                )
            # A character name is one line of large type. Capping the card at
            # 72px and pinning it to the top of a taller OCR box is what put
            # "Буро" in the padding above "Buro".
            if kind == "name" and raw_h > 36:
                src_h = raw_h + 2
                src_w = max(src_w, raw_w + 4)
                font = clamp(int(raw_h * 0.92), 14, raw_h)
                oneline = True
                tight = True
                cover = True
                fit_text = True
            else:
                src_h = min(ocr_h, max(28, min(lh * 2 + 16, 72)))
                fit_text = bool(par.get("wrap"))
                # Однострочный текст - всегда oneline=True, не эвристика
                oneline = is_single_line or (bool(par.get("oneline")) if "oneline" in par else src_h <= max(24, lh * 1.8))
                cover = True
                tight = False
                font = clamp(int(lh * 0.95), 11, max(18, int(raw_h * 0.8)))
        font_family, italic, display_title = detect_overlay_typeface(
            region_img,
            (px1, py1, px2, py2),
            angle=angle,
            raw_w=raw_w,
            raw_h=raw_h,
            src_text=src_text,
            kind=kind,
        )
        if display_title and kind != "name":
            src_w = min(rw, raw_w + 6)
            src_h = min(rh, max(raw_h + 4, int(raw_h * 1.04)))
            font = clamp(int(raw_h * 0.78), 14, min(48, raw_h - 1))
            tight = True
            oneline = True
            if abs(ocr_cx - rw / 2) <= rw * 0.22:
                centered = True
        if centered:
            x = clamp(int(ocr_cx - src_w / 2), 0, max(0, rw - src_w))
        else:
            x = clamp(px1 - pad, 0, max(0, rw - src_w))
        y = clamp(py1 - pad, 0, max(0, rh - src_h))

    if kind == "name":
        x = clamp(px1, 0, max(0, rw - src_w))
        y = clamp(py1, 0, max(0, rh - src_h))
        oneline = True
        tight = True

    bubble = None
    if kind in ("dialogue", "body", "dialogue-line") or pin_box:
        # высокие tip-панели обычно тёмные — не force light bubble
        if tip_tall or (kind == "body" and raw_h >= 72):
            bubble = sample_ocr_cover_colors(
                region_img, (px1, py1, px2, py2), prefer_light_bg=False
            )
        else:
            bubble = sample_bubble_cover_colors(region_img, (px1, py1, px2, py2))
        # тёмный Ren'Py-попап: светлый bubble-сэмпл не сработает — берём реальные цвета
        if bubble is None:
            bubble = sample_ocr_cover_colors(
                region_img, (px1, py1, px2, py2), prefer_light_bg=False
            )
    band_geom = None
    if not vertical_line and abs(skew) >= 1.0:
        band_geom = (
            (px1 + px2) * 0.5,
            (py1 + py2) * 0.5,
            max(8, px2 - px1),
            max(6, py2 - py1),
            skew,
        )
    ui_colors = None
    if ui_label or kind in ("ui", "chip", "menu", "latin"):
        # белая страница + цветной заголовок: иначе фиолет глифа становится «фоном» плашки
        buttonish = ink_is_light(region_img, (px1, py1, px2, py2)) and raw_h >= 18
        light_page = False
        try:
            if region_img is not None and not buttonish:
                # сэмпл ВОКРУГ глифа — белый фон сайта, даже если бокс забит фиолетовым текстом
                pad_bg = max(6, raw_h)
                around = (
                    max(0, px1 - pad_bg),
                    max(0, py1 - pad_bg),
                    min(region_img.width, px2 + pad_bg),
                    min(region_img.height, py2 + pad_bg),
                )
                light_page = (
                    sample_ocr_cover_colors(region_img, around, prefer_light_bg=True)
                    is not None
                )
        except Exception:
            light_page = False
        if light_page and not buttonish:
            ui_colors = sample_ocr_cover_colors(
                region_img, (px1, py1, px2, py2), prefer_light_bg=True
            )
            # подтянуть цвет чернил заголовка (фиолетовый), фон оставить светлым
            ink = sample_ocr_cover_colors(
                region_img, (px1, py1, px2, py2), prefer_light_bg=False
            )
            if ui_colors is not None and ink is not None:
                _bg, _ = ui_colors
                _, ink_fg = ink
                ui_colors = (_bg, ink_fg)
        else:
            ui_colors = sample_ocr_cover_colors(
                region_img, (px1, py1, px2, py2), prefer_light_bg=False, band=band_geom
            )
    if ui_colors is None and bubble is None:
        # One reader for every script. A block that is neither a labelled
        # control nor a dialogue bubble used to skip sampling and fall through
        # to a near-black card, which is where Japanese lines came out as a
        # different object from the English on the same screen.
        ui_colors = sample_ocr_cover_colors(
            region_img, (px1, py1, px2, py2), prefer_light_bg=False, band=band_geom
        )
    # Плашка, чей цвет снят с экрана, закрывает ровно то, что под ней было.
    # Такую не нужно ни обводить, ни скруглять: рамка и скругление рисуют то, чего
    # в кадре нет, и именно их видно как «подставленный» текст. Заглушка, когда
    # цвет снять не удалось, остаётся карточкой — ей граница нужна, чтобы читаться
    # как карточка.
    bg_measured = bubble is not None or ui_colors is not None
    if bubble is not None:
        bg, fg = bubble
    elif ui_colors is not None:
        bg, fg = ui_colors
    else:
        bg = (0.04, 0.06, 0.10, 0.99)
        fg = (1.0, 1.0, 1.0, 1.0)

    # контраст плашки: не допускать чёрный текст на тёмном / белый на светлом
    def _lum(c: tuple) -> float:
        return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]

    def _sat(c: tuple) -> float:
        return max(c[0], c[1], c[2]) - min(c[0], c[1], c[2])

    bl, fl = _lum(bg), _lum(fg)
    # Readability is a question about the pair, and the pair is read with a
    # contrast ratio rather than a difference of brightness. Two colours at the
    # same brightness can be far apart to the eye and a difference threshold
    # calls them identical: dark green on bright green measures 0.29 apart on
    # brightness and is perfectly readable, and the old test threw the sampled
    # pair away and painted a white card with purple letters on it. What the
    # original had was green on green, and that is what the card gets.
    if _contrast_ratio(bg, fg) < 1.35:
        # Genuinely unreadable. Two light colours, or two dark ones, are the
        # failure where the ink was sampled from the panel: pushing the panel
        # even lighter leaves white text on a white card. Move the ink, and
        # leave the panel at the colour that was measured.
        if bl >= 0.62 and fl >= 0.62:
            fg = (min(0.16, fg[0] * 0.15), min(0.16, fg[1] * 0.15), min(0.16, fg[2] * 0.15), 1.0)
        elif bl <= 0.28 and fl <= 0.28:
            fg = (max(0.92, fg[0]), max(0.92, fg[1]), max(0.92, fg[2]), 1.0)
        elif bl >= fl:
            lift = [min(1.0, c + (1.0 - c) * 0.55) for c in bg[:3]]
            bg = (lift[0], lift[1], lift[2], 0.96)
        else:
            drop = [c * 0.35 for c in bg[:3]]
            bg = (drop[0], drop[1], drop[2], 0.96)
        bl = _lum(bg)

    # светлая страница: плотнее плашка, иначе EN просвечивает под RU
    if bl >= 0.72 and len(bg) >= 4 and bg[3] < 0.96:
        bg = (bg[0], bg[1], bg[2], 0.97)

    stroke = None
    stroke_w = 0.0
    fill_width = kind == "name"
    # Заголовок на арте: светлые чернила поверх светлого фона, и читать их
    # можно только если подложить свою плашку. Но «светлые чернила» не значит
    # «светлый фон»: надпись `CONTINUE` на зелёной кнопке светлая, а фон под
    # ней тёмно-зелёный, и своя плашка с синей обводкой поверх этого давала
    # чёрный прямоугольник с белым текстом в рамке на месте, где у оригинала
    # были зелёные буквы на зелёном фоне. Условие, за которым нужна подложка,
    # — не яркость чернил, а то, что они не читаются на своём фоне.
    if display_title:
        light = ink_is_light(region_img, (px1, py1, px2, py2))
        readable = _contrast_ratio(bg, fg) >= 1.35
        if light and not readable:
            outline = sample_outline_color(region_img, (px1, py1, px2, py2))
            bg = (0.06, 0.08, 0.14, 0.36)
            fg = (0.98, 0.98, 1.0, 1.0)
            # не брать ультра-синий/фиолетовый UI-акцент — только умеренный контур
            if outline is not None:
                sat = max(outline[0], outline[1], outline[2]) - min(outline[0], outline[1], outline[2])
                if sat < 0.55 and outline[2] < 0.85:
                    stroke = outline
                else:
                    stroke = (0.18, 0.28, 0.48, 0.9)
            else:
                stroke = (0.18, 0.28, 0.48, 0.9)
            stroke_w = max(2.0, min(5.0, raw_h * 0.08))
            fill_width = True
            src_w = min(rw, max(8, raw_w))
            src_h = min(rh, max(10, raw_h))
            font = clamp(int(raw_h * 0.82), 14, min(52, max(14, raw_h - 1)))
            if abs(ocr_cx - rw / 2) <= rw * 0.25:
                centered = True
            if centered:
                x = clamp(int(ocr_cx - src_w / 2), 0, max(0, rw - src_w))
            else:
                x = clamp(px1, 0, max(0, rw - src_w))
            y = clamp(py1, 0, max(0, rh - src_h))
            tight = True
            oneline = True
        else:
            # ложный display на тёмном UI — откат к обычной карточке
            display_title = False
            font_family = "Sans"
            italic = False
            a = bg[3] if len(bg) > 3 else 0.88
            bg = (bg[0], bg[1], bg[2], min(0.78, max(0.50, a * 0.85)))
    ink_c = getattr(glyphs, "color", None)
    if ink_c and max(ink_c[:3]) - min(ink_c[:3]) >= 0.10:
        # Saturated ink (pink names, teal HUD) beats a washed sample that
        # drifted toward white or the panel.
        if _sat(ink_c) > _sat(fg) + 0.04 and _contrast_ratio(bg, ink_c) >= 1.2:
            fg = (float(ink_c[0]), float(ink_c[1]), float(ink_c[2]), 1.0)
            fl = _lum(fg)

    if not display_title:
        a = bg[3] if len(bg) > 3 else 0.88
        # body/абзац — плотнее, чтобы EN не просвечивал («дубли»)
        if kind in ("dialogue", "dialogue-line", "body"):
            bg = (bg[0], bg[1], bg[2], min(0.97, max(0.88, a)))
        else:
            # Полупрозрачная плашка не выполняет своей работы, когда сквозь неё
            # читается то, ради чего она и нарисована. На панели статов
            # подпись темнее фона в разы, и при 0.62 оригинал просвечивал
            # справа от более короткого перевода: «Атака» поверх «Attack», а хвост
            # «ck» - рядом с буквой. Абзацам уже ставили нижнюю границу 0.88 по
            # той же причине; подписи на светлом фоне получали 0.62, и это была
            # не мягкость, а просто незакрытый оригинал.
            bg = (bg[0], bg[1], bg[2], min(0.90, max(0.88, a * 0.9)))

    if vertical_line:
        # Layout the translation along the reading direction, then rotate the
        # card onto the tall box. Rotating the tall box itself swings it into
        # a horizontal bar through the middle of the line.
        long_side = max(8, int(px2 - px1), int(py2 - py1))
        short_side = max(8, min(int(px2 - px1), int(py2 - py1)))
        src_w = long_side
        src_h = short_side
        x = int(round((px1 + px2) / 2 - src_w / 2))
        y = int(round((py1 + py2) / 2 - src_h / 2))
        oneline = True
        font = clamp(int(short_side * 0.78), 11, max(14, short_side - 1))
        angle = draw_angle
    if abs(angle) >= 1.5:
        tlog(f"angle={angle:.1f}deg '{(translated or '')[:36]}'")
    tlog(
        f"card {kind or '-'} ocr={raw_w}x{raw_h} -> {src_w}x{src_h} "
        f"font={font} ink_h={ink_h} ink_w={ink_w} "
        f"fill_width={int(bool(fill_width))} oneline={int(bool(oneline))} "
        f"lines={int(src_lines)} "
        f"pitch={int(row_pitch(rows) or (max(8, raw_h // max(1, src_lines)) if src_lines >= 2 else 0))} "
        f"rows={len(rows)} lh={lh} "
        f"{font_family}{' italic' if italic else ''} tight={int(tight)} "
        f"local=({x},{y}) screen=({rx + x},{ry + y}) '{(translated or '')[:40]}'"
    )
    return {
        "x": x,
        "y": y,
        "src_w": src_w,
        "src_h": src_h,
        # What the original glyphs measured, kept on the block so the drawing
        # pass can lay the translation out against the same shape rather than
        # against a line height the engine estimated.
        "tracking": float(glyphs.tracking),
        "stem": float(glyphs.stroke),
        # The measured shape of the original, carried so the face is picked from
        # features rather than from the game's name. A screenshot
        # cannot tell us the typeface; it can tell us how wide a line is for its
        # size, how tall the letters stand and how heavy the strokes are, and
        # that is enough to walk the list of faces we are allowed to ship.
        "ink_aspect": float(glyphs.aspect),
        "cap_h": int(glyphs.cap),
        "asc_h": int(glyphs.tall),
        "weight": (
            weight_for_stroke(glyphs.stroke, raw_h)
            if kind != "name" or raw_h < 48
            else weight_for_stroke(max(glyphs.stroke, 0.18), raw_h)
        ),
        "ink_h": int(ink_h),
        "src_lines": int(src_lines),
        # The distance the original kept between its lines, so the translation
        # is set with the same leading instead of the font's own. Rows measured
        # one at a time are the best answer; without them the box itself is:
        # it spans exactly the lines it holds, so dividing it by their count is
        # the leading the game used. The recogniser's line height is a floor for
        # the count, not the distance - on a tightly-led paragraph it read 28
        # where the lines sat 33 apart.
        "line_pitch": int(
            row_pitch(rows) or (max(8, raw_h // max(1, src_lines)) if src_lines >= 2 else 0)
        ),
        "ink_color": glyphs.color,
        "color_spans": [
            {
                "x1": s.x1,
                "y1": s.y1,
                "x2": s.x2,
                "y2": s.y2,
                "color": s.color,
                "share": s.share,
            }
            for s in _line_colors(region_img, (px1, py1, px2, py2), band_geom, bg, fg)
        ],
        "grow_limit": (
            None
            if room_px is None
            else max(0, int((src_w if skew else px2 - x) + room_px - 3))
        ),
        "text": translated,
        "source": src_text,
        "conf": float(par.get("conf", 0)),
        "bg": bg,
        "bg_measured": bg_measured,
        "fg": fg,
        "stroke": stroke,
        "stroke_w": stroke_w,
        "font": font,
        "font_family": font_family,
        "italic": italic,
        "oneline": oneline,
        "cover": cover,
        "tight": tight,
        "fill_width": fill_width,
        "kind": "dialogue" if kind == "dialogue-line" else kind,
        "fit_text": fit_text,
        "angle": angle,
        "align": "left"
        if (kind in ("dialogue", "dialogue-line", "body") or pin_box)
        else ("center" if centered else "left"),
        "keep_breaks": bool(tip_tall or (kind == "body" and not oneline) or (src_lines > 1 and not pin_box)),
        "wrap_box": bool(tip_tall or (kind == "body" and not oneline)),
    }


def _cap_height(glyphs, ink_h: int, src_text: str) -> int:
    """Height of the original's capitals, or 0 when it cannot be read.

    Only for an alphabet with capitals: a Japanese line has no baseline with
    strokes hanging below it, and its ink height already is its size. A word
    with nothing that rises above the lowercase - "ammo", "seen" - measures
    its x-height, which is about three quarters of the capital.
    """
    cap = int(getattr(glyphs, "cap", 0) or 0)
    if not ink_h or cap < 8 or cap > ink_h:
        return 0
    # Against the ink height a short capital means the measure caught only a
    # dense stripe of the letters - unless that height is faint artwork behind
    # a dialogue box, which the dense rows leave out.
    tall = int(getattr(glyphs, "tall", 0) or 0)
    if cap < ink_h * 0.55 and not (tall and cap >= tall * 0.7):
        return 0
    text = str(src_text or "")
    if RE_JPN.search(text) or not re.search(r"[A-Za-zА-Яа-яЁё]", text):
        return 0
    if not re.search(r"[A-ZА-ЯЁ0-9bdfhikltбй]", text):
        cap = int(round(cap * 1.35))
    return min(cap, ink_h)


def _line_colors(region_img, box, band_geom, bg, fg=None) -> list:
    """Colour runs along a line, read along the line itself.

    The runs are cut by columns. A slanted line's columns cross the panel
    above and below it, so they are read off an upright copy of the strip,
    turned back by the line's own angle. A run whose colour is the panel's
    is the edge of the letters blending into it, not a colour they have.
    """
    img, rbox = region_img, box
    if band_geom is not None and region_img is not None:
        cx, cy, bw, bh, angle = band_geom
        half = int(math.hypot(bw, bh) / 2) + 4
        x1 = max(0, int(cx) - half)
        y1 = max(0, int(cy) - half)
        x2 = min(region_img.width, int(cx) + half)
        y2 = min(region_img.height, int(cy) + half)
        if x2 - x1 >= 8 and y2 - y1 >= 8:
            piece = region_img.crop((x1, y1, x2, y2)).rotate(
                angle, resample=Image.BICUBIC, center=(cx - x1, cy - y1)
            )
            lx, ly = cx - x1, cy - y1
            img = piece
            rbox = (
                max(0, int(lx - bw / 2)),
                max(0, int(ly - bh / 2)),
                min(piece.width, int(lx + bw / 2)),
                min(piece.height, int(ly + bh / 2)),
            )
    panels = []
    if img is not None:
        import numpy as np

        x1, y1, x2, y2 = (int(v) for v in rbox)
        rgb = np.asarray(img.crop((x1, y1, x2, y2)).convert("RGB"), dtype=np.float32)
        panels = edge_panels(rgb)
    bg_rgb = (float(bg[0]), float(bg[1]), float(bg[2]))
    if not any(sum((a - b) ** 2 for a, b in zip(p, bg_rgb)) < 0.04 for p in panels):
        panels.append(bg_rgb)
    spans = measure_word_colors(img, rbox, panels=panels)

    def against_panels(c) -> float:
        return min(
            _contrast_ratio((c[0], c[1], c[2], 1.0), (p[0], p[1], p[2], 1.0)) for p in panels
        )

    # The sampled ink is the line's colour unless a run says otherwise with
    # some force. A run much fainter than it is the panel showing between thin
    # strokes, and painting the whole translation in it greys the line out.
    ink = against_panels(fg) if fg is not None else 1.3
    kept = [s for s in spans if against_panels(s.color) >= max(1.3, ink * 0.5)]

    def _sat(c: tuple) -> float:
        rgb = c[:3]
        return float(max(rgb) - min(rgb))

    # A pink name that runs into a white ear is a second span of pure white.
    # Painting that onto the translation turns the last letters white.
    if any(_sat(s.color) >= 0.28 for s in kept):
        kept = [s for s in kept if _sat(s.color) >= 0.12 or max(s.color[:3]) < 0.82]
    if len(kept) == 1 and against_panels(kept[0].color) < ink * 0.8:
        return []
    if len(kept) == 1 and fg is not None and img is not None:
        # One run is one colour for the whole line, and it is drawn instead of
        # the sampled ink. Where the two disagree, the one more of the line is
        # painted in is the line's colour: a run reads a column's brightest
        # colour, and a big yellow number standing at the end of "SCORE" made
        # the dark red word yellow.
        import numpy as np

        span_rgb = np.array(kept[0].color[:3], dtype=np.float32) * 255.0
        fg_rgb = np.array([float(c) for c in fg[:3]], dtype=np.float32) * 255.0
        if float(np.sqrt(((span_rgb - fg_rgb) ** 2).sum())) > 64.0:
            x1, y1, x2, y2 = (int(v) for v in rbox)
            px = np.asarray(img.crop((x1, y1, x2, y2)).convert("RGB"), dtype=np.float32)
            near_span = int((np.sqrt(((px - span_rgb) ** 2).sum(axis=2)) < 48.0).sum())
            near_fg = int((np.sqrt(((px - fg_rgb) ** 2).sum(axis=2)) < 48.0).sum())
            if near_fg > near_span:
                return []
    return kept


MIN_X_SCALE = 0.62


def _tracking_spacing(tracking: float, font_px: int) -> int:
    """Pango letter-spacing in Pango units from the original's ink gaps."""
    if tracking <= 0.0 or font_px < 8:
        return 0
    default_gap = max(1.0, font_px * 0.06)
    extra = tracking - default_gap
    clamped = max(-font_px * 0.14, min(font_px * 0.38, extra))
    return int(round(clamped * Pango.SCALE)) if Pango is not None else 0


def _stretch_for(x_scale: float):
    if Pango is None or not x_scale or x_scale >= 0.99:
        return None
    if x_scale >= 0.92:
        return Pango.Stretch.SEMI_CONDENSED
    if x_scale >= 0.80:
        return Pango.Stretch.CONDENSED
    if x_scale >= 0.70:
        return Pango.Stretch.EXTRA_CONDENSED
    return Pango.Stretch.ULTRA_CONDENSED


def _rows_match_box(rows: list[dict], box_h: int) -> bool:
    """Whether a paragraph's rows account for the box they were read from.

    The rows come from a recogniser that can hand back a paragraph as one tall
    box plus one real line beside it. Taken at face value that is a two-line
    paragraph with a 95-pixel leading inside a 126-pixel box, and every number
    taken from it - the leading, the size of a line, the number of lines the
    card is allowed - describes a paragraph that is not on the screen.

    What has to add up is the rows against the box: `rows x pitch` is the height
    the rows claim, and it has to be the height of the box within a line's
    slack. A paragraph read correctly satisfies it whichever way its last line
    ends.
    """
    if box_h < 8 or len(rows) < 2:
        return True
    pitch = row_pitch(rows)
    if pitch <= 0:
        return True
    claimed = pitch * len(rows)
    return box_h - max(12, int(box_h * 0.15)) <= claimed <= box_h * 1.25


def _apply_line_pitch(layout, pitch_px: int) -> None:
    """Set the lines at the distance the original kept between them.

    Leading belongs to the game, not to the font. Pango's own leading is a
    property of the face at the size it was asked for, so a translation fitted
    into a smaller size also got tighter leading: two description lines 55
    pixels apart came out 40 apart, and the block sat in the top half of the
    box it was given with a band of empty panel under it. The rows the original
    occupied are what fixes that - the same pitch, whatever size the fitting
    settles on.

    Asked of Pango as a factor of its own leading, because that is what it takes
    (Pango.Layout.set_line_spacing is a multiplier, not pixels), and clamped: a
    row measured off a box that caught a descender should not pull the lines on
    top of each other.
    """
    if Pango is None or pitch_px < 6:
        return
    try:
        count = int(layout.get_line_count())
        if count < 2:
            return
        natural = float(layout.get_pixel_size()[1]) / float(count)
        if natural <= 1.0:
            return
        factor = pitch_px / natural
        if abs(factor - 1.0) < 0.04:
            return
        layout.set_line_spacing(clamp(factor, 0.6, 2.2))
    except Exception:
        return


def pango_layout(
    cr: cairo.Context,
    text: str,
    font_px: int,
    wrap_w: int | None,
    ellipsize: bool = False,
    align: str = "left",
    family: str = "Sans",
    italic: bool = False,
    x_scale: float = 1.0,
    weight: object = None,
    tracking: float = 0.0,
) -> object:
    layout = PangoCairo.create_layout(cr)
    font_desc = Pango.FontDescription()
    font_desc.set_family(family or "Sans")
    font_desc.set_size(max(8, font_px) * Pango.SCALE)
    font_desc.set_weight(Pango.Weight.BOLD if weight is None else weight)
    if italic:
        font_desc.set_style(Pango.Style.ITALIC)
    else:
        font_desc.set_style(Pango.Style.NORMAL)
    if x_scale and abs(x_scale - 1.0) > 0.01:
        # Horizontal scale on the glyphs, not on the layout: condensing the type
        # keeps the cap height, so a translation that is too wide for its box is
        # narrowed rather than made smaller. Games do this themselves - a label
        # that must fit a fixed slot is usually condensed, not shrunk - and a
        # translation that came out at a smaller size than the original reads as
        # a mismatch even when it fits.
        #
        # Pango has no continuous horizontal scale on a font description, so
        # the scale picks the face's own narrower widths. Letter-spacing below
        # then takes up the rest of the difference.
        stretch = _stretch_for(x_scale)
        if stretch is not None:
            font_desc.set_stretch(stretch)
    layout.set_font_description(font_desc)
    spacing = _tracking_spacing(tracking, font_px)
    if x_scale and x_scale < 0.98:
        # Squeeze remaining width after the discrete stretch step.
        squeeze = int(round((1.0 - x_scale) * max(8, font_px) * 0.35 * Pango.SCALE))
        spacing -= squeeze
    if spacing:
        attrs = Pango.AttrList()
        attrs.insert(Pango.attr_letter_spacing_new(int(spacing)))
        layout.set_attributes(attrs)
    if wrap_w is None:
        layout.set_width(-1)
    else:
        layout.set_width(max(1, wrap_w) * Pango.SCALE)
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
    if ellipsize:
        layout.set_ellipsize(Pango.EllipsizeMode.END)
    if align == "center":
        layout.set_alignment(Pango.Alignment.CENTER)
    else:
        layout.set_alignment(Pango.Alignment.LEFT)
    layout.set_text(text, -1)
    return layout


def fit_layout(
    cr: cairo.Context,
    text: str,
    src_w: int,
    src_h: int,
    preferred: int,
    oneline: bool = False,
    align: str = "left",
    keep_breaks: bool = False,
    tight: bool = False,
    family: str = "Sans",
    italic: bool = False,
    fill_width: bool = False,
    tracking: float = 0.0,
    weight: object = None,
    grow_w: int = 0,
    max_lines: int = 0,
    line_pitch: int = 0,
):
    """Фон ≥ src_w. tight: никогда не раздувать рамку — только ужимать шрифт.
    fill_width: подогнать кегль так, чтобы текст занял ширину OCR-бокса."""
    pad_x, pad_y = (2, 1) if tight else (6, 3)
    inner_w = max(8, src_w - pad_x * 2)
    # A tight card is fitted to the element it covers, so its type may be as
    # large as the element's was. The 52 here capped a 139-pixel character name
    # at 52 and left a card three times its height; what the size is actually
    # bounded by is the card, and the fitting below - condense, wrap, shrink -
    # is what brings a line back to it. A card that is not tight keeps the
    # smaller ceiling, because there the size is a preference rather than a
    # measurement.
    max_font = max(52, preferred) if tight else max(18, min(34, preferred))
    preferred = clamp(preferred, 8, max_font)
    nlines = max(1, text.count("\n") + 1)
    line_cap = max_lines if max_lines >= 1 else (1 if oneline else 0)

    def lay(size: int, wrap: int | None = None, scale: float = 1.0):
        layout = pango_layout(
            cr, text, size, wrap, align=align, family=family, italic=italic,
            x_scale=scale,
            weight=weight,
            tracking=tracking,
        )
        _apply_line_pitch(layout, line_pitch)
        return layout

    def lines_ok(layout) -> bool:
        if line_cap <= 0:
            return True
        try:
            return int(layout.get_line_count()) <= line_cap
        except Exception:
            return True

    def cover(layout, tw: int, th: int, force_w: int | None = None):
        if tight:
            return layout, src_w, src_h, pad_x, max(0, (src_h - th) // 2)
        w = force_w if force_w is not None else max(src_w, tw + pad_x * 2)
        h = max(th + pad_y * 2, min(src_h, th + pad_y * 2 + max(4, src_h // 8)))
        if keep_breaks or nlines >= 2:
            # At least the height of the text being replaced. Fitting the box to
            # the translation instead is what left the bottom third of a
            # paragraph uncovered: a short translation in a tall source box
            # produced a short card, and the lines it did not reach stayed in
            # English underneath.
            h = max(src_h, th + pad_y * 2)
        else:
            h = max(src_h, th + pad_y * 2) if src_h <= th + pad_y * 2 + 20 else max(th + pad_y * 2, min(src_h, th + pad_y * 2 + 16))
        return layout, w, h, pad_x, pad_y

    if tight:
        pad_x, pad_y = (3, 2) if (fill_width or src_h >= 72) else (2, 1)
        inner_w = max(8, src_w - pad_x * 2)
        need_wrap = (
            keep_breaks
            or nlines >= 2
            or (not oneline and line_cap != 1 and (src_h >= 48 or len(text) >= 48))
            or (src_h >= 72 and line_cap != 1)
        )
        if need_wrap and not fill_width:
            scales = (1.0, 0.90, 0.80, 0.70, 0.62)
            for size in range(preferred, 7, -1):
                for scale in scales:
                    layout = lay(size, inner_w, scale)
                    tw, th = layout.get_pixel_size()
                    if th + pad_y * 2 <= src_h and lines_ok(layout):
                        top = pad_y + (2 if src_h >= 72 else 0)
                        return layout, src_w, src_h, pad_x, top
            layout = lay(8, inner_w, MIN_X_SCALE)
            return layout, src_w, src_h, pad_x, pad_y

        # заголовок: растянуть кегль под ширину оригинала (и не выше бокса)
        if fill_width and oneline:
            lo, hi = 8, max(preferred, src_h - 1)
            best = lay(lo, None)
            while lo <= hi:
                mid = (lo + hi) // 2
                layout = lay(mid, None)
                tw, th = layout.get_pixel_size()
                if tw <= inner_w and th + pad_y * 2 <= src_h:
                    best = layout
                    lo = mid + 1
                else:
                    hi = mid - 1
            tw, th = best.get_pixel_size()
            # если всё ещё узко — последний допустимый уже выбран; центрируем по вертикали
            return best, src_w, src_h, pad_x, max(0, (src_h - th) // 2)

        # One line on a card that is the original's own box: what has to fit
        # in it is the letters, not the line. Pango's line box adds the line
        # gap and room for accents and descenders the text may not have, and
        # it is close to twice the height of a capital; holding it to the card
        # set "SCORE" in a 61-pixel band at two thirds of its size, with the
        # bottoms of the original's letters showing under the card. The ink is
        # then centred, which is where the original's ink was.
        def measure(layout) -> tuple[int, int, int]:
            ink, logical = layout.get_pixel_extents()
            return max(1, logical.width), max(1, ink.height), ink.y

        def placed(layout, tw: int, ih: int, iy: int):
            return layout, max(src_w, tw + pad_x * 2), src_h, pad_x, (src_h - ih) // 2 - iy

        size = preferred
        probe = lay(size, None)
        pw, ph, py = measure(probe)
        while ph + pad_y * 2 > src_h and size > 7:
            # Taller than the card: only a smaller size answers that.
            size -= 1
            probe = lay(size, None)
            pw, ph, py = measure(probe)
        preferred = size
        if pw <= inner_w:
            return placed(probe, pw, ph, py)

        # The order below is the order of what a reader notices least. A card
        # wider than the original over an empty panel is invisible; condensed
        # glyphs are visible only past MIN_X_SCALE; a smaller size than the
        # line it replaces is seen at once, and in a list it is seen as one row
        # set in a different size from the rest. Stepping the size down first
        # made the two steps before it unreachable: every label that did not
        # fit came out smaller, however much room stood beside it.
        #
        # Too wide at the preferred size: grow into the room beside the element
        # before touching the size. The stat column is one label after
        # another down a panel, and every row's Russian label is longer than the
        # English it replaces while the room to its right is empty - so the card
        # was pinned to the original's width, the label was squeezed into it, and
        # the result was a column of 10-pixel type that did not read. Growing is
        # the better answer than shrinking, and the limit comes from the
        # neighbouring elements' own boxes rather than from a constant.
        avail = inner_w
        if grow_w > src_w and oneline and not fill_width:
            avail = max(inner_w, grow_w - pad_x * 2)

        if pw <= avail and ph + pad_y * 2 <= src_h:
            tdetail(f"grow {src_w}->{pw + pad_x * 2} limit={grow_w}")
            return placed(probe, pw, ph, py)

        # Still too wide: condense before shrinking. Condensing keeps the cap
        # height, so the card keeps the original's size while fitting the room
        # it has. Only down to MIN_X_SCALE: past that the glyphs are visibly
        # squeezed, and the size steps down instead - condensed again at each
        # size, so it steps down no further than it must.
        for size in range(preferred, 6, -1):
            layout = lay(size, None) if size != preferred else probe
            tw, th, ty = measure(layout)
            if th + pad_y * 2 > src_h:
                continue
            if tw <= avail:
                return placed(layout, tw, th, ty)
            scale = avail / float(tw)
            if scale >= MIN_X_SCALE:
                scale = (math.floor(scale * 100) / 100) * 0.995
                squeezed = lay(size, None, scale)
                sw, sh, sy = measure(squeezed)
                if sw <= avail and sh + pad_y * 2 <= src_h:
                    tdetail(f"condense size={size} {tw}->{sw} scale={scale:.2f}")
                    return placed(squeezed, sw, sh, sy)

        # No room beside the control (the skip arrow, the next button). Wrap
        # and step the size down until the glyphs stay inside the original box.
        # Drawing them past it paints the translation over the neighbour.
        if avail <= inner_w + 1:
            for size in range(preferred, 7, -1):
                layout = lay(size, inner_w)
                tw, th, ty = measure(layout)
                if tw <= inner_w + 1 and th + pad_y * 2 <= src_h:
                    return placed(layout, tw, th, ty)
        layout = lay(8, inner_w)
        tw, th, ty = measure(layout)
        return layout, src_w, max(src_h, th + 2), pad_x, max(1, (max(src_h, th + 2) - th) // 2) - ty

    tiny_button = src_h <= 14 and src_w <= 70
    if tiny_button:
        max_h = max(src_h + 2, 12)
        for size in range(min(preferred, max(7, src_h)), 6, -1):
            layout = lay(size, None)
            tw, th = layout.get_pixel_size()
            if tw <= inner_w and th + pad_y * 2 <= max_h:
                return layout, max(src_w, tw + pad_x * 2), max_h, pad_x, max(1, (max_h - th) // 2)
        return None

    if keep_breaks or nlines >= 2:
        # The height that matters is the original's own, because the card has to
        # cover the text it replaces - a card that does not reach leaves the
        # English readable underneath it. The old test derived the budget from
        # the number of source lines instead, and a 145-character paragraph with
        # one source line got a budget of a couple of lines, so it shrank until
        # the type was 11px: too small to read and too small to hide anything.
        floor = 12 if src_h >= 40 else 9
        scales = (1.0, 0.90, 0.80, 0.70, 0.62)
        for budget in (src_h, int(src_h * 1.35)):
            for size in range(preferred, floor - 1, -1):
                for scale in scales:
                    layout = lay(size, inner_w, scale)
                    tw, th = layout.get_pixel_size()
                    if th + pad_y * 2 <= budget and lines_ok(layout):
                        return cover(layout, tw, th, src_w)
        # Nothing fits at a readable size. Taller than the text it replaces is
        # the lesser evil - _fit_between_neighbours still keeps it off whatever
        # is next to it - and illegible is neither.
        layout = lay(floor, inner_w, MIN_X_SCALE)
        tw, th = layout.get_pixel_size()
        return cover(layout, tw, th, src_w)

    if oneline or src_h <= max(24, preferred * 1.75):
        for size in range(preferred, 7, -1):
            for scale in (1.0, 0.88, 0.76, 0.62):
                layout = lay(size, None, scale)
                tw, th = layout.get_pixel_size()
                if tw <= inner_w:
                    return cover(layout, tw, th, src_w)
        layout = lay(8, None, MIN_X_SCALE)
        tw, th = layout.get_pixel_size()
        return cover(layout, tw, th, max(src_w, min(tw + pad_x * 2, int(src_w * 1.25) + 16)))

    max_h = max(src_h, int(src_h * 1.4), src_h + 16)
    for size in range(preferred, 7, -1):
        for scale in (1.0, 0.88, 0.76, 0.62):
            layout = lay(size, inner_w, scale)
            tw, th = layout.get_pixel_size()
            if th + pad_y * 2 <= max_h and lines_ok(layout):
                return cover(layout, tw, th, src_w)

    layout = lay(8, inner_w, MIN_X_SCALE)
    tw, th = layout.get_pixel_size()
    return cover(layout, tw, min(th, max_h - pad_y * 2), src_w)


def click_through(win: Gtk.Window) -> None:
    """Пустая input-region: клики/скролл идут в окно под оверлеем, не в перевод."""
    try:
        empty = cairo.Region()
    except Exception:
        return
    try:
        win.set_can_focus(False)
    except Exception:
        pass
    try:
        win.set_input_region(empty)
    except Exception:
        pass
    surf = win.get_surface()
    if surf is None:
        return
    try:
        surf.set_input_region(empty)
    except Exception:
        pass
    try:
        surf.set_opaque_region(empty)
    except Exception:
        pass


def card_quad(cx: float, cy: float, w: float, h: float, angle_deg: float) -> list[tuple[float, float]]:
    """The four corners of a card as it is actually drawn.

    A card on a slanted line is drawn rotated, and its bounding box is then far
    larger than the card: at 15 degrees a line of 30 pixels of type is 100 pixels
    tall in its box. Two cards on neighbouring lines of the same slanted row
    therefore have bounding boxes that overlap through most of their area while
    the cards themselves do not touch, and any test written on bounding boxes
    calls that a collision. It is not one, and acting on it is what moved a card
    four hundred pixels away from the text it was drawn to replace.
    """
    rad = math.radians(angle_deg)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    hw, hh = w * 0.5, h * 0.5
    out: list[tuple[float, float]] = []
    for dx, dy in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)):
        out.append((cx + dx * cos_a - dy * sin_a, cy + dx * sin_a + dy * cos_a))
    return out


COLOR_SPAN_MIN_SHARE = 0.12
