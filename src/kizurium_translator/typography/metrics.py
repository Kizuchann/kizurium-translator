"""Typography: what the original was drawn as.

Распознанная строка говорит, КАКИЕ слова, но
не говорит, какими они набраны, какого цвета каждое слово и как далеко друг от
друга. Всё это видно на экране и нигде не записано в тексте, поэтому
единственное место, где оно существует, - пиксели.

Модуль ничего не знает про cairo и GTK: на выходе высота строки, ширина глифа,
наклон, семейство и раскраска по словам.

Тело перенесено из `live.py` дословно. Первый проход был написан по памяти, и
такой перенос тихо сдвигает границы на несколько процентов, не меняя ни одного
теста.
"""
from __future__ import annotations

import math
import re
from collections import OrderedDict
from dataclasses import dataclass
from statistics import median

import numpy as np
from PIL import Image

from ..core.text import (  # noqa: F401
    _FILE_EXTENSIONS,
    CORE_INK_PERCENTILE,
    RE_KANA,
    RE_LAT,
    _is_real_short_word,
    _reads_as_prose,
    _word_break_token,
    dedupe_paragraph_reads,
    dialogue_looks_incomplete,
    game_ui_score,
    is_subtitle_junk_line,
    looks_like_spoken_line,
    merge_paragraph_tail,
    normalize_subtitle_ocr,
    script_cjk_score,
    strip_watermark_tail,
    tdetail,
    tlog,
)

NEUTRAL_HUE = -1.0


COLOR_MIN_SAT = 0.22


COLOR_MIN_HUE_GAP = 26.0


@dataclass(frozen=True)
class GlyphMetrics:
    """What the glyphs in a box actually measure."""

    height: int = 0
    """Ink height in pixels: the tallest run of ink, not the box height."""

    width: int = 0
    """Ink width in pixels."""

    stroke: float = 0.0
    """Median run of ink across a row, over the ink height: how heavy the type is.

    This is the one property of a typeface that can be read off the original and
    cannot be invented for the translation, and a translation drawn at a fixed
    weight does not match the screen it is on whichever face it picks. Game UI
    leans on very heavy display faces - the ones measured here run from 0.15 to
    0.26 of their own height - and a book-weight rendering of the same word is a
    visibly different object from the one it covers.
    """

    left: int = 0
    """Distance from the left of the box to the first column of ink."""

    top: int = 0
    """Distance from the top of the box to the first row of ink."""

    bottom: int = 0
    """Distance from the top of the box to the last row of ink."""

    aspect: float = 0.0
    """Ink width per unit of ink height. Wide means tracked-out or stretched."""

    tracking: float = 0.0
    """Extra pixels between glyphs beyond what the font gives for free."""

    color: tuple[float, float, float] = (1.0, 1.0, 1.0)
    """The colour the majority of the ink is drawn in."""

    cap: int = 0
    """Baseline to the top of the capitals: the size of the type.

    The full ink height is not, because it depends on the words. "Arks" and
    "Waypoints" are set in one size on one panel, and the descender of the "p"
    and "y" made the second measure a fifth taller. A row of artwork behind the
    box's edge adds to it as well. Neither changes how big the letters are.
    """

    tall: int = 0
    """Rows carrying a real share of the ink, top to bottom: the type without
    the faint artwork above or below it that ``height`` counts."""


@dataclass(frozen=True)
class ColorSpan:
    """A run of neighbouring glyphs drawn in one colour."""

    x1: int
    y1: int
    x2: int
    y2: int
    color: tuple[float, float, float]
    share: float = 0.0
    """How much of the line's ink this span is."""


def _mask_from_array(arr):
    """Ink mask and background level for a greyscale array.

    The background is taken as the median rather than assumed: a light panel and
    a dark one are both common and both have to work, and an assumed black
    background turns every light panel into "all ink".

    Returns None when there is nothing to separate - a flat region has no ink
    and no background, only one colour, and a mask built from it would be all or
    nothing.
    """

    if arr.size == 0:
        return None, None
    med = float(np.median(arr))
    spread = float(np.std(arr))
    if spread < 6.0:
        return None, None
    if med < 128.0:
        mask = arr > (med + max(18.0, spread * 0.35))
    else:
        mask = arr < (med - max(18.0, spread * 0.35))
    return mask, med


def _ink_mask(crop):
    """Rows and columns that hold ink, separated from the background."""

    return _mask_from_array(np.asarray(crop.convert("L"), dtype=np.float32))


def color_hue(color) -> float:
    """Hue of a colour in degrees, or NEUTRAL_HUE when it has none.

    White, black and every grey are the same colour as far as layout is
    concerned, and treating them as different would invent a highlight where the
    screen has none.
    """
    r, g, b = (float(c) for c in color[:3])
    mx, mn = max(r, g, b), min(r, g, b)
    if mx - mn < 1e-6:
        return NEUTRAL_HUE
    sat = (mx - mn) / max(1.0, mx)
    if sat < COLOR_MIN_SAT:
        return NEUTRAL_HUE
    d = mx - mn
    if mx == r:
        h = 60.0 * (((g - b) / d) % 6)
    elif mx == g:
        h = 60.0 * (((b - r) / d) + 2)
    else:
        h = 60.0 * (((r - g) / d) + 4)
    return h


def _hue_gap(a: float, b: float) -> float:
    """Smallest distance between two hues, wrapping round the circle."""
    if a == NEUTRAL_HUE or b == NEUTRAL_HUE:
        return 0.0
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def _metrics_crop(img, box, pad: int = 0):
    """Crop a box for measuring, tolerating a box given the wrong way round.

    Named apart from the older _crop_box: that one is the cropping path for a
    read, where a reversed box is a bug worth seeing, and overwriting it here
    would have changed what the reader does with one.
    """
    x1, y1, x2, y2 = (int(v) for v in box)
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1
    w, h = img.size
    x1, y1 = max(0, x1 - pad), max(0, y1 - pad)
    x2, y2 = min(w, x2 + pad), min(h, y2 + pad)
    if x2 - x1 < 4 or y2 - y1 < 4:
        return None
    return img.crop((x1, y1, x2, y2))


def _dominant_color(crop, mask) -> tuple[float, float, float]:

    rgb = np.asarray(crop.convert("RGB"), dtype=np.float32)
    if mask is None or not mask.any():
        return (1.0, 1.0, 1.0)
    sel = rgb[mask]
    if sel.size == 0:
        return (1.0, 1.0, 1.0)
    med = np.median(sel, axis=0)
    return tuple(float(v) / 255.0 for v in med)  # type: ignore[return-value]


def locate_slant_band(
    region_img: Image.Image | None,
    box: tuple[int, int, int, int],
    angle_deg: float,
    band_h: int,
) -> tuple[float, float]:
    """Where a slanted line's letters actually are: its angle and its offset.

    The card is centred on the recogniser's box, and the box is not centred on
    the letters: on the "SCORE" of a results screen it sat a dozen pixels above
    them, and the bottoms of the letters showed under the card as a dashed line.
    The letters are found by projecting their ink onto the line's normal: at
    the right angle and the right place a band as thick as the line holds the
    most ink. The offset is along the normal, positive below the line, from the
    box's centre; (angle_deg, 0.0) when there is nothing to measure.
    """
    if region_img is None or abs(angle_deg) < 3.0 or band_h < 8:
        return angle_deg, 0.0
    crop = _metrics_crop(region_img, box)
    if crop is None:
        return angle_deg, 0.0
    try:
        rgb = np.asarray(crop.convert("RGB"), dtype=np.float32)
        panels = edge_panels(rgb)
        mask = _panel_mask(rgb, panels) if panels else _ink_mask(crop)[0]
        if mask is None or mask.sum() < 40:
            return angle_deg, 0.0
        h, w = mask.shape
        ys, xs = np.nonzero(mask)
        ys = ys - (h - 1) / 2.0
        xs = xs - (w - 1) / 2.0
        reach = band_h * 1.3
        bins = np.arange(-reach, reach + 1.0, 1.0)
        k = int(band_h)
        if len(bins) - 1 < k:
            return angle_deg, 0.0
        best = (-1.0, angle_deg, 0.0)
        for d in np.arange(-6.0, 6.01, 0.5):
            rad = math.radians(angle_deg + d)
            du = ys * math.cos(rad) - xs * math.sin(rad)
            hist, _ = np.histogram(du, bins=bins)
            sums = np.convolve(hist, np.ones(k), mode="valid")
            top = float(sums.max())
            # A wrong angle costs ink at both ends of the line; ties go to the
            # recogniser's own angle.
            if top > best[0] * 1.002 or (top >= best[0] and abs(d) < abs(best[1] - angle_deg)):
                plateau = np.where(sums >= top * 0.98)[0]
                centre = -reach + (plateau[0] + plateau[-1]) / 2.0 + k / 2.0
                best = (top, angle_deg + float(d), float(centre))
        if best[0] <= 0 or abs(best[2]) > band_h * 0.5:
            return angle_deg, 0.0
        return best[1], best[2]
    except Exception:  # noqa: BLE001
        return angle_deg, 0.0


def slant_room_ahead(
    region_img: Image.Image | None,
    centre: tuple[float, float],
    angle_deg: float,
    band_h: int,
    half_len: float,
    limit: int = 400,
) -> int:
    """Clear run along a slanted line past its end, in pixels.

    The room beside a label is measured across the screen, and a line at
    twenty degrees leaves that row within a few letters: the room "beside
    SCORE" was the score itself. This walks the line's own band forward from
    its end and stops at the first column holding something sharp - a letter,
    a digit, a panel's edge. Blurred artwork behind a label has no sharp edges
    and is room; the same test as the horizontal one.
    """
    if region_img is None or band_h < 6:
        return 0
    try:
        gray = np.asarray(region_img.convert("L"), dtype=np.float32)
        H, W = gray.shape
        rad = math.radians(angle_deg)
        c, s = math.cos(rad), math.sin(rad)
        half = band_h / 2.0
        ts = np.arange(0, limit + 1, dtype=np.float32) + half_len
        ss = np.arange(-half, half + 1.0, 1.0, dtype=np.float32)
        xs = centre[0] + ts[None, :] * c - ss[:, None] * s
        ys = centre[1] + ts[None, :] * s + ss[:, None] * c
        inside = (xs >= 0) & (xs <= W - 1) & (ys >= 0) & (ys <= H - 1)
        cols_in = inside.all(axis=0)
        n = int(np.argmin(cols_in)) if not cols_in.all() else len(ts)
        if n < 2:
            return 0
        strip = gray[
            np.clip(np.rint(ys[:, :n]), 0, H - 1).astype(int),
            np.clip(np.rint(xs[:, :n]), 0, W - 1).astype(int),
        ]
        sharp = np.zeros(n, dtype=np.float32)
        sharp[1:] = np.abs(np.diff(strip, axis=1)).max(axis=0)
        sharp = np.maximum(sharp, np.abs(np.diff(strip, axis=0)).max(axis=0))
        # Sampling a hard edge at an angle steps it pixel by pixel, which reads
        # as sharp everywhere along a panel border running with the line; two
        # sharp columns in a row is content, one is a stair.
        hit = sharp >= 40.0
        both = hit[:-1] & hit[1:]
        if both.any():
            return int(np.argmax(both))
        return int(n - 1)
    except Exception:  # noqa: BLE001
        return 0


def measure_slant_band(
    region_img: Image.Image | None,
    box: tuple[int, int, int, int],
    angle_deg: float,
) -> int:
    """How thick a slanted line is, measured across the line rather than down it.

    A slanted line's bounding box is tall for a reason that has nothing to do with
    the type: the line climbs, so the box is as tall as the climb plus the letters.
    Recovering the letters from the angle alone assumes the box is exactly those
    two things, and it is not - the recogniser pads its quadrilateral, and where
    two lines of a paragraph are close the padding on one is the other's gap.
    Arithmetic cannot tell the two apart, because both are "extra" and both are in
    the same place.

    Measured, it can. The question is not how tall the box is but how thick the
    stripe of ink is inside it, and that has a boundary a box does not: between
    two lines there is nothing at all. So the ink is projected onto the line's own
    normal and the stripe grown outward from wherever it is thickest for as long
    as it stays thick. A gap ends the growth, which is exactly the thing the
    arithmetic could not see; a line with nothing above it grows to the edge of
    the box and stops, which is correct rather than a failure.

    Returns 0 when there is nothing to measure, and the caller decides what to
    do about it rather than this function guessing.
    """
    if region_img is None or abs(angle_deg) < 1.0:
        return 0
    crop = _metrics_crop(region_img, box)
    if crop is None:
        return 0
    try:
        import numpy as np

        mask, _med = _ink_mask(crop)
        if mask is None or not mask.any():
            return 0
        h, w = mask.shape
        ys, xs = np.nonzero(mask)
        rad = math.radians(angle_deg)
        sin_a, cos_a = math.sin(rad), math.cos(rad)
        # Signed distance from the box's centre line, along the line's normal.
        # Positive is below the line as it runs left to right.
        du = (ys - (h - 1) / 2.0) * cos_a - (xs - (w - 1) / 2.0) * sin_a
        limit = int(math.ceil(max(h, w)))
        edges = np.arange(limit + 2, dtype=np.float32) - (limit + 1) / 2.0
        hist, _e = np.histogram(du, bins=edges)
        total = float(hist.sum())
        if total < 24:
            return 0
        # A profile of a letter band is thick through the middle and thin at the
        # ascender and descender ends, so the peak is searched for rather than
        # starting at the centre, which lands wherever the padding put it.
        peak = int(hist.argmax())
        if hist[peak] < 8:
            return 0
        floor = max(2.0, hist[peak] * 0.10)

        def run(direction: int) -> int:
            edge = peak
            last = peak
            while edge + direction >= 0 and edge + direction < hist.size:
                if hist[edge + direction] < floor:
                    break
                edge += direction
                last = edge
            return abs(last - peak)

        depth = run(1) + run(-1) + 1
        if depth < 6 or depth > limit:
            return 0
        return depth
    except Exception:
        return 0


def ink_line_count(region_img: Image.Image | None, box: tuple[int, int, int, int]) -> int:
    """How many lines of text a box holds, counted from the gaps between them.

    The height of a box is not a line count: read against a fixed idea of how
    tall a line is - true of interface text and nothing else - a character name at
    143 pixels came out as six lines, so the card was laid out for a paragraph
    and ended as a large empty panel. A box says how many lines it holds by
    having gaps in it; one line of type, however large, has none, because its ink
    is a solid band from the tallest ascender to the deepest descender.

    Two pixels is the threshold for calling a blank row a gap - one blank row is
    what tightly-leaded type looks like between the feet of one line and the tops
    of the next, and counting those reads every paragraph as a single run of
    letters. A band thinner than three pixels is not a line at all: it is that
    gap leaking through, or a speck of artwork crossing the box. Counting it read a
    three-line paragraph as five, and five lines in a three-line card is type
    set tighter than the original.
    """
    if region_img is None:
        return 1
    crop = _metrics_crop(region_img, box)
    if crop is None or crop.height < 6:
        return 1
    try:
        import numpy as np

        mask, _med = _ink_mask(crop)
        if mask is None or not mask.any():
            return 1
        rows = mask.any(axis=1)
        idx = np.nonzero(rows)[0]
        first, last = int(idx[0]), int(idx[-1])
        inner = rows[first : last + 1]
        # Two blank rows is a break between lines; one is the white between the
        # feet of one line and the tops of the next. That is the contract, and
        # it is why the ink is cut on runs of blank rows and not on height.
        segments: list[int] = []
        current = 0
        blanks = 0
        for on in inner:
            if on:
                current += 1
                blanks = 0
            else:
                blanks += 1
                if blanks == 2 and current:
                    segments.append(current)
                    current = 0
        if current:
            segments.append(current)
        # A segment a pixel or two tall is not a line of type. It is a break
        # that leaked through, or a speck of artwork crossing the box; counting
        # it read a four-line paragraph as six, and six lines in a four-line card
        # is type set tighter than the original.
        return sum(1 for h in segments if h >= 3) or 1
    except Exception:
        return 1


_MEASURE_CACHE_MAX = 512
"""How many measurements one frame may hold.

A cycle measures the same boxes repeatedly: `make_block` measures once, then
again after each extension of a name plate's edges, and the overlay redraws the
same blocks on every cycle while the scene is stable. Measured on a dense
screen, one paragraph box costs 9.8 ms per measurement - the most expensive
thing in the card path - and the same answer is asked for four times.
"""

_MEASURE_CACHE: "OrderedDict[tuple, GlyphMetrics]" = OrderedDict()

_LIGHT_CACHE: "OrderedDict[tuple, bool]" = OrderedDict()


def clear_measurement_cache() -> None:
    """Forget measured pixels. Call whenever the frame changes underneath us.

    The key is the identity of the image object, so a new frame is a new key
    and cannot read an old answer; this only bounds the dict.
    """
    _MEASURE_CACHE.clear()
    _LIGHT_CACHE.clear()


def measure_glyph_metrics(
    region_img: Image.Image | None, box: tuple[int, int, int, int]
) -> GlyphMetrics:
    """Measure the ink in a box: its height, its width, and its tracking.

    The engine's line height is its own estimate and is used as the ceiling now;
    what the glyphs actually occupy is measured, because the two differ by a
    factor of nearly two whenever the recogniser merged a few rows into one box,
    and because a line of tracked-out capitals and a tight lowercase line are
    the same size in the request while being nothing alike on screen.

    The card path must not recompute font heuristics from scratch on
    every draw. The same box in the same frame always measures the same, so the
    answer is kept per frame and thrown away with the frame.
    """
    if region_img is None:
        return GlyphMetrics()
    key = (id(region_img), box)
    cached = _MEASURE_CACHE.get(key)
    if cached is not None:
        _MEASURE_CACHE.move_to_end(key)
        return cached
    out = _measure_glyph_metrics_uncached(region_img, box)
    _MEASURE_CACHE[key] = out
    if len(_MEASURE_CACHE) > _MEASURE_CACHE_MAX:
        _MEASURE_CACHE.popitem(last=False)
    return out


def _measure_glyph_metrics_uncached(
    region_img: Image.Image | None, box: tuple[int, int, int, int]
) -> GlyphMetrics:
    if region_img is None:
        return GlyphMetrics()
    crop = _metrics_crop(region_img, box)
    if crop is None:
        return GlyphMetrics()
    try:
        import numpy as np

        def dense_span(m) -> int:
            if m is None or not m.any():
                return 0
            pr = m.sum(axis=1)
            idx = np.where(pr >= pr.max() * 0.2)[0]
            return int(idx[-1] - idx[0] + 1)

        mask, _med = _ink_mask(crop)
        rgb = np.asarray(crop.convert("RGB"), dtype=np.float32)
        panels = edge_panels(rgb)
        alt = _panel_mask(rgb, panels) if panels else None
        # On a bright saturated panel the luminance gate keeps the panel and
        # drops light type: "Wonderful Pain" on magenta measured as magenta,
        # and the card was cut to half its length. Against the panel colour
        # the letters are what is left. Prefer the mask whose ink stands
        # further from the panel that fills the border - a second border
        # colour is often the letters themselves (SCORE's purple).
        if alt is not None and alt.any() and panels:
            d_ink, d_alt = dense_span(mask), dense_span(alt)
            if d_alt >= 8 and d_alt >= d_ink * 0.8:
                pr, pg, pb = (float(c) * 255.0 for c in panels[0][:3])

                def far(m) -> float:
                    if m is None or not m.any():
                        return -1.0
                    col = _dominant_color(crop, m)
                    ir, ig, ib = (float(c) * 255.0 for c in col[:3])
                    return (ir - pr) ** 2 + (ig - pg) ** 2 + (ib - pb) ** 2

                # More coverage against the panel, or a colour that stands off
                # it further: white-on-magenta had equal "far" scores because
                # the fringe filled the mask, but the panel mask held three
                # thousand bright letter pixels the luminance gate dropped.
                bright_alt = int(
                    (rgb[alt].mean(axis=1) > 200).sum() if alt is not None else 0
                )
                bright_ink = int(
                    (rgb[mask].mean(axis=1) > 200).sum()
                    if mask is not None and mask.any()
                    else 0
                )
                if far(alt) > far(mask) * 1.05 or (
                    bright_alt > bright_ink + 200 and int(alt.sum()) > int(mask.sum() or 0)
                ):
                    mask = alt
        if mask is None or not mask.any():
            return GlyphMetrics()
        rows = np.where(mask.any(axis=1))[0]
        cols = np.where(mask.any(axis=0))[0]
        if rows.size == 0 or cols.size == 0:
            return GlyphMetrics()
        top, bottom = int(rows[0]), int(rows[-1])
        left, right = int(cols[0]), int(cols[-1])
        height = bottom - top + 1
        width = right - left + 1

        # Tracking: the gaps between runs of ink. The median is used rather than
        # the mean because a line has word spaces in it too, and the letter
        # gaps outnumber them on any line of a few words or more. The bound is
        # the line height rather than a fraction of it: tracked-out capitals put
        # real air between letters - more than a third of the cap height - and
        # the old bound threw exactly that away, which is the case the measure
        # exists for.
        col_has_ink = mask.any(axis=0)
        runs: list[tuple[int, int]] = []
        start = None
        for x, on in enumerate(col_has_ink):
            if on and start is None:
                start = x
            elif not on and start is not None:
                runs.append((start, x))
                start = None
        if start is not None:
            runs.append((start, len(col_has_ink)))
        gaps: list[int] = []
        for i in range(len(runs) - 1):
            gap = runs[i + 1][0] - runs[i][1]
            if 0 < gap <= max(4, height):
                gaps.append(gap)
        if gaps:
            gaps.sort()
            tracking = float(gaps[len(gaps) // 2])
        else:
            tracking = 0.0
        aspect = width / float(max(1, height))
        # How heavy the type is: the median length of an unbroken run of ink in
        # each row of the band, over the band's height. The median rather than the
        # mean, for the reason tracking is - a line has word spaces in it too.
        stroke_runs: list[int] = []
        for y in range(top, bottom + 1):
            row = mask[y]
            run = 0
            for on in row:
                if on:
                    run += 1
                else:
                    if run >= 2:
                        stroke_runs.append(run)
                    run = 0
            if run >= 2:
                stroke_runs.append(run)
        stroke = (float(median(stroke_runs)) / height) if stroke_runs and height else 0.0
        # The type sits between the baseline and the top of the capitals. The
        # baseline is the last row of the dense body of the letters - a
        # descender is a few strokes, not a row of them - and the capitals'
        # top is the first row that carries a real share of the ink, which
        # leaves out a speck of artwork the box happened to clip.
        per_row = mask.sum(axis=1)
        peak = float(per_row.max())
        cap = 0
        tall_h = 0
        if peak > 0:
            tall = np.where(per_row >= peak * 0.2)[0]
            body = np.where(per_row >= peak * 0.45)[0]
            if tall.size:
                tall_h = int(tall[-1] - tall[0] + 1)
            if tall.size and body.size and body[-1] >= tall[0]:
                cap = int(body[-1] - tall[0] + 1)
            if body.size:
                # In a lowercase line the rows above the x-height hold only the
                # few strokes of "D", "t", "b" and fall under any share of the
                # peak, the same as faint artwork does. A stroke rising out of
                # the body of the letters is joined to it and artwork is not,
                # so the top of the capitals is where those strokes end.
                b_top, base = int(body[0]), int(body[-1])
                rise = max(1, int((base - b_top) * 0.15))
                tops = []
                for c in range(mask.shape[1]):
                    hits = np.nonzero(mask[b_top : base + 1, c])[0]
                    if not hits.size:
                        continue
                    r = b_top + int(hits[0])
                    while r > 0 and mask[r - 1, c]:
                        r -= 1
                    if r < b_top - rise:
                        tops.append(r)
                if len(tops) >= 2:
                    cap = max(cap, int(base - float(np.median(tops)) + 1))
        # The colour of the type, not of the AA fringe into the panel: on white
        # letters over magenta the median of the mask was still magenta.
        color = (
            _run_color(np.asarray(crop.convert("RGB"), dtype=np.float32)[mask], panels)
            if panels
            else _dominant_color(crop, mask)
        )
        return GlyphMetrics(
            cap=cap,
            tall=tall_h,
            height=height,
            width=width,
            left=left,
            top=top,
            bottom=bottom,
            aspect=aspect,
            tracking=tracking,
            stroke=stroke,
            color=color,
        )
    except Exception:  # noqa: BLE001
        return GlyphMetrics()


def extend_trailing_glyphs(
    region_img: Image.Image | None,
    box: tuple[int, int, int, int],
    *,
    color: tuple[float, float, float],
    band_top: int,
    band_h: int,
) -> tuple[int, int, int, int]:
    """Grow a name box rightwards through letters OCR stopped before.

    ``Hakurei Reimu`` was read as ``Hakurei Reim``: the last letter sits on
    the character art and the box ends in the middle of the word. Walk the
    letter band in the ink colour and stop at a gap, before the sprite.
    """
    if region_img is None or band_h < 8:
        return box
    import numpy as np

    x1, y1, x2, y2 = (int(v) for v in box)
    y_a = max(0, y1 + int(band_top))
    y_b = min(region_img.height, y_a + int(band_h))
    if y_b - y_a < 6 or x2 >= region_img.width - 2:
        return box
    rgb = np.asarray(region_img.convert("RGB"), dtype=np.float32)
    target = np.array(color[:3], dtype=np.float32) * 255.0
    gap_limit = max(14, int(band_h * 0.28))
    x = x2
    gap = 0
    limit = min(region_img.width - 1, x2 + int(band_h * 4))
    while x < limit:
        col = rgb[y_a:y_b, x]
        dist = np.sqrt(((col - target) ** 2).sum(axis=1))
        if int((dist < 48.0).sum()) >= max(3, band_h // 8):
            gap = 0
            x += 1
            continue
        gap += 1
        if gap >= gap_limit:
            break
        x += 1
    new_x2 = x - gap
    if new_x2 <= x2 + 4:
        return box
    return (x1, y1, new_x2, y2)


def extend_leading_glyphs(
    region_img: Image.Image | None,
    box: tuple[int, int, int, int],
    *,
    color: tuple[float, float, float],
    band_top: int,
    band_h: int,
) -> tuple[int, int, int, int]:
    """Grow a line left through dots OCR dropped (``..Umm`` read as ``Umm``)."""
    if region_img is None or band_h < 8:
        return box
    import numpy as np

    x1, y1, x2, y2 = (int(v) for v in box)
    y_a = max(0, y1 + int(band_top))
    y_b = min(region_img.height, y_a + int(band_h))
    if y_b - y_a < 6 or x1 <= 2:
        return box
    rgb = np.asarray(region_img.convert("RGB"), dtype=np.float32)
    target = np.array(color[:3], dtype=np.float32) * 255.0
    gap_limit = max(10, int(band_h * 0.22))
    x = x1 - 1
    gap = 0
    limit = max(0, x1 - int(band_h * 2.2))
    hit = False
    while x >= limit:
        col = rgb[y_a:y_b, x]
        dist = np.sqrt(((col - target) ** 2).sum(axis=1))
        if int((dist < 48.0).sum()) >= max(2, band_h // 10):
            gap = 0
            hit = True
            x -= 1
            continue
        gap += 1
        if gap >= gap_limit:
            break
        x -= 1
    new_x1 = x + gap + 1
    if not hit or new_x1 >= x1 - 2:
        return box
    return (new_x1, y1, x2, y2)


def edge_panels(rgb, ring: int = 2, share: float = 0.15) -> list[tuple[float, float, float]]:
    """Colours that fill a real share of a crop's border.

    Letters do not reach the edge of the box they were found in; what does is
    whatever they are painted on, and on a game screen that is often more than
    one thing - a panel and a stripe of artwork crossing it behind the line.
    """
    import numpy as np

    h, w = rgb.shape[:2]
    if h <= ring * 2 or w <= ring * 2:
        return []
    border = np.concatenate(
        (
            rgb[:ring].reshape(-1, 3),
            rgb[-ring:].reshape(-1, 3),
            rgb[ring:-ring, :ring].reshape(-1, 3),
            rgb[ring:-ring, -ring:].reshape(-1, 3),
        )
    )
    keys = (border.astype(np.int32) >> 4) @ np.array([256, 16, 1])
    uniq, counts = np.unique(keys, return_counts=True)
    out = []
    for idx in np.argsort(-counts)[:4]:
        if counts[idx] < len(border) * share:
            break
        pix = border[keys == uniq[idx]]
        out.append(tuple(float(v) / 255.0 for v in np.median(pix, axis=0)))
    return out


def _panel_mask(rgb, panels):
    """Pixels that are none of the panels.

    Brighter-than-the-median is the ink only when the ink is lighter than the
    panel. Dark letters, or a light stripe crossing the panel behind them,
    invert that: the stripe is taken and the letters are not.
    """
    import numpy as np

    near = np.full(rgb.shape[:2], np.inf, dtype=np.float32)
    for panel in panels:
        pr, pg, pb = (float(c) * 255.0 for c in panel[:3])
        dist = np.sqrt(
            (rgb[..., 0] - pr) ** 2 + (rgb[..., 1] - pg) ** 2 + (rgb[..., 2] - pb) ** 2
        )
        near = np.minimum(near, dist)
    return near > 56.0


def _run_color(sel, panels):
    """The colour of a run of ink: the largest colour in it that stands off the panels."""
    import numpy as np

    if not panels or len(sel) < 8:
        return tuple(float(v) / 255.0 for v in np.median(sel, axis=0))
    keys = (sel.astype(np.int32) >> 4) @ np.array([256, 16, 1])
    uniq, counts = np.unique(keys, return_counts=True)
    # The colour the run is read by, which is the one furthest from what it
    # is painted on - not the most common one, which on thin type is the
    # blend of the letters into the panel.
    best = None
    best_ratio = 1.3
    for idx in np.argsort(-counts)[:6]:
        if counts[idx] < max(4, len(sel) * 0.15):
            continue
        pix = sel[keys == uniq[idx]]
        col = tuple(float(v) / 255.0 for v in np.median(pix, axis=0))
        lum = _rel_lum(col)
        ratio = min(
            (max(lum, _rel_lum(p)) + 0.05) / (min(lum, _rel_lum(p)) + 0.05) for p in panels
        )
        if ratio > best_ratio:
            best, best_ratio = col, ratio
    if best is not None:
        return best
    return tuple(float(v) / 255.0 for v in np.median(sel, axis=0))


def _split_by_colour(rgb, mask, run, panels, min_len: int = 0):
    """Cut a run of ink where its colour changes for longer than a letter's edge.

    Words in two colours are often joined by an outline or a shadow, so no
    empty column separates them and the run is the whole line.
    """
    x1, x2 = run
    h = mask.shape[0]
    min_len = min_len or max(8, h // 2)
    cols = []
    for x in range(x1, x2):
        sel = rgb[:, x][mask[:, x]]
        cols.append(_run_color(sel, panels) if len(sel) >= 2 else None)
    groups: list[list] = []
    for i, col in enumerate(cols):
        if col is None:
            if groups:
                groups[-1][1] = x1 + i + 1
            continue
        if groups and groups[-1][2] is not None:
            ref = groups[-1][2]
            if sum((a - b) ** 2 for a, b in zip(ref, col)) < 0.09:
                groups[-1][1] = x1 + i + 1
                continue
        groups.append([x1 + i, x1 + i + 1, col])
    merged: list[list] = []
    for g in groups:
        if merged and (g[1] - g[0] < min_len):
            merged[-1][1] = g[1]
            continue
        if merged and merged[-1][1] - merged[-1][0] < min_len:
            merged[-1] = [merged[-1][0], g[1], g[2]]
            continue
        if merged and sum((a - b) ** 2 for a, b in zip(merged[-1][2], g[2])) < 0.09:
            merged[-1][1] = g[1]
            continue
        merged.append(list(g))
    return [(g[0], g[1]) for g in merged] or [run]


def _rel_lum(c) -> float:
    def ch(v: float) -> float:
        v = float(v)
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2])


def measure_word_colors(
    region_img: Image.Image | None,
    box: tuple[int, int, int, int],
    panels: list | None = None,
) -> list[ColorSpan]:
    """Split a line into runs of neighbouring glyphs by the colour they are in.

    A game line is often one colour with the speaker, a number or a keyword in
    another, and the whole-box average loses that: it returns one colour and the
    highlight is gone. Here the line is split into runs of ink and runs that are
    near enough in hue to be the same colour are joined, so a highlight that is
    genuinely a different colour survives as its own span.
    """
    if region_img is None:
        return []
    crop = _metrics_crop(region_img, box)
    if crop is None:
        return []
    try:
        import numpy as np

        rgb = np.asarray(crop.convert("RGB"), dtype=np.float32)
        if panels:
            mask = _panel_mask(rgb, panels)
        else:
            mask, _med = _ink_mask(crop)
        if mask is None or not mask.any():
            return []

        col_has_ink = mask.any(axis=0)
        runs: list[tuple[int, int]] = []
        start = None
        for x, on in enumerate(col_has_ink):
            if on and start is None:
                start = x
            elif not on and start is not None:
                runs.append((start, x))
                start = None
        if start is not None:
            runs.append((start, len(col_has_ink)))
        if not runs:
            return []

        if panels:
            runs = [piece for run in runs for piece in _split_by_colour(rgb, mask, run, panels)]
        total_ink = float(mask.sum()) or 1.0
        spans: list[ColorSpan] = []
        for x1, x2 in runs:
            col_mask = mask[:, x1:x2]
            if not col_mask.any():
                continue
            rows = np.where(col_mask.any(axis=1))[0]
            y1, y2 = int(rows[0]), int(rows[-1])
            sel = rgb[:, x1:x2][col_mask]
            if sel.size == 0:
                continue
            color = _run_color(sel, panels)
            spans.append(
                ColorSpan(
                    x1=x1,
                    y1=y1,
                    x2=x2,
                    y2=y2,
                    color=color,  # type: ignore[arg-type]
                    share=float(col_mask.sum()) / total_ink,
                )
            )

        # Join neighbours that are the same colour to the eye. A gap wider than
        # a space is a word boundary even when both words are the same colour,
        # and a highlight is only a highlight if it is not the whole line.
        joined: list[ColorSpan] = []
        for span in spans:
            if joined:
                prev = joined[-1]
                gap = span.x1 - prev.x2
                if panels:
                    # White and dark red are both "no hue gap" to a hue test,
                    # because white has no hue; to the eye they are two colours.
                    same = sum((a - b) ** 2 for a, b in zip(prev.color, span.color)) < 0.09
                else:
                    same = (
                        _hue_gap(color_hue(prev.color), color_hue(span.color))
                        < COLOR_MIN_HUE_GAP
                    )
                if same and gap <= max(6, (span.y2 - span.y1) // 2):
                    joined[-1] = ColorSpan(
                        x1=prev.x1,
                        y1=min(prev.y1, span.y1),
                        x2=span.x2,
                        y2=max(prev.y2, span.y2),
                        color=prev.color,
                        share=prev.share + span.share,
                    )
                    continue
            joined.append(span)
        return joined
    except Exception:  # noqa: BLE001
        return []


def drop_false_spaces(region_img: Image.Image | None, box, text: str) -> str:
    """Removes spaces the recogniser put inside a word.

    RapidOCR reads "Safe Areas" as "Safe Are as" and "Quest Area Details" as
    "Quest Are a Details", and the translator turns the pieces into "Безопасны
    как". The pixels say where the words are: between words the type leaves a
    gap of half its capital height, between letters a pixel or three. When the
    text has more spaces than the line has such gaps, the spaces are matched to
    the gaps in reading order, by where along the line each one falls, and the
    ones left over were never there.

    A line on which no gap can be found is left alone: that is a measurement
    that failed - artwork filling the gaps - not a line with no words in it.
    """
    t = str(text or "")
    if region_img is None or "\n" in t or " " not in t:
        return t
    if not re.fullmatch(r"[A-Za-z][A-Za-z' ]*[A-Za-z]", t):
        return t
    try:
        import numpy as np

        crop = _metrics_crop(region_img, box)
        if crop is None or crop.height > 80:
            return t
        mask, _ = _ink_mask(crop)
        if mask is None:
            return t
        per_row = mask.sum(axis=1)
        peak = float(per_row.max())
        if peak <= 0:
            return t
        rows = np.where(per_row >= peak * 0.2)[0]
        cap = int(rows[-1] - rows[0] + 1)
        # Two lines in one box overlay each other's gaps; only one line is read.
        if cap < 8 or cap > rows.size + 2:
            return t
        cols = mask[rows[0] : rows[-1] + 1].any(axis=0)
        ink = np.where(cols)[0]
        if ink.size < 4:
            return t
        left, right = int(ink[0]), int(ink[-1])
        runs: list[tuple[int, int]] = []
        start = None
        for x in range(left, right + 1):
            if not cols[x] and start is None:
                start = x
            elif cols[x] and start is not None:
                runs.append((start, x))
                start = None
        if not runs:
            return t
        # A word space is wide against the line's own letter spacing, not
        # against a constant: a condensed face sets its words closer together.
        typical = float(median(b - a for a, b in runs))
        bar = max(3.0, cap * 0.25, typical * 2.0)
        gaps = [(a + b) / 2.0 for a, b in runs if b - a >= bar]
        spaces = [i for i, c in enumerate(t) if c == " "]
        if not gaps or len(gaps) >= len(spaces):
            return t
        span = float(max(1, right - left))
        at = [left + (i + 0.5) / len(t) * span for i in spaces]
        # Order-preserving match of every gap to one space, least total
        # distance: cost[i][j] = best with the first i spaces and j gaps.
        n, m = len(at), len(gaps)
        inf = float("inf")
        cost = [[inf] * (m + 1) for _ in range(n + 1)]
        take = [[False] * (m + 1) for _ in range(n + 1)]
        cost[0][0] = 0.0
        for i in range(1, n + 1):
            for j in range(0, min(i, m) + 1):
                skip = cost[i - 1][j]
                use = cost[i - 1][j - 1] + abs(at[i - 1] - gaps[j - 1]) if j else inf
                if use < skip:
                    cost[i][j], take[i][j] = use, True
                else:
                    cost[i][j] = skip
        kept: set[int] = set()
        i, j = n, m
        while i > 0:
            if take[i][j]:
                kept.add(spaces[i - 1])
                j -= 1
            i -= 1
        out = "".join(c for k, c in enumerate(t) if c != " " or k in kept)
        if out != t:
            tdetail(f"false-space {t!r} -> {out!r} gaps={len(gaps)}")
        return out
    except Exception:  # noqa: BLE001
        return t


def edge_icon_trim(region_img: Image.Image | None, box, text: str) -> tuple[int, int]:
    """Pixels to take off either end of a box for an icon read along with it.

    The recogniser's box for "↵ CONTINUE" starts at the arrow, and a card cut to
    the box paints the arrow out. The pixels have one more word-gap-separated
    cluster than the text has words. Which end it is on is decided by the
    words: with the extra cluster dropped from the right end, the arrow would
    have to be the whole of "CONTINUE" at an eighth of a capital per letter,
    and no face is that narrow. Only an unambiguous answer is taken.
    Returns (left, right) in pixels from the box's edges.
    """
    t = str(text or "").strip()
    if region_img is None or not t or "\n" in t:
        return 0, 0
    words = t.split()
    try:
        import numpy as np

        crop = _metrics_crop(region_img, box, pad=0)
        if crop is None or crop.height > 90:
            return 0, 0
        rgb = np.asarray(crop.convert("RGB"), dtype=np.float32)
        panels = edge_panels(rgb)
        mask = _panel_mask(rgb, panels) if panels else None
        if mask is None or not mask.any():
            mask, _ = _ink_mask(crop)
        if mask is None:
            return 0, 0
        per_row = mask.sum(axis=1)
        peak = float(per_row.max())
        if peak <= 0:
            return 0, 0
        rows = np.where(per_row >= peak * 0.2)[0]
        cap = int(rows[-1] - rows[0] + 1)
        if cap < 8 or cap > rows.size + 2:
            return 0, 0
        cols = mask[rows[0] : rows[-1] + 1].any(axis=0)
        ink = np.where(cols)[0]
        if ink.size < 4:
            return 0, 0
        left, right = int(ink[0]), int(ink[-1]) + 1
        gaps: list[tuple[int, int]] = []
        start = None
        for x in range(left, right):
            if not cols[x] and start is None:
                start = x
            elif cols[x] and start is not None:
                gaps.append((start, x))
                start = None
        if not gaps:
            return 0, 0
        typical = float(median(b - a for a, b in gaps))
        bar = max(4.0, cap * 0.15, typical * 2.0)
        wide = [(a, b) for a, b in gaps if b - a >= bar]
        if len(wide) != len(words):
            return 0, 0
        edges = [left] + [v for g in wide for v in g] + [right]
        clusters = [(edges[i], edges[i + 1]) for i in range(0, len(edges), 2)]

        def plausible(cl: list[tuple[int, int]]) -> bool:
            for (a, b), word in zip(cl, words):
                n = max(1, len(word))
                per = (b - a) / float(n)
                if not (cap * 0.3 <= per <= cap * 1.3):
                    return False
            return True

        icon_ok = lambda c: (c[1] - c[0]) <= cap * 1.6  # noqa: E731
        lead = icon_ok(clusters[0]) and plausible(clusters[1:])
        trail = icon_ok(clusters[-1]) and plausible(clusters[:-1])
        if lead == trail:
            return 0, 0
        if lead:
            cut = clusters[1][0] - 2
            tdetail(f"edge-icon lead {t!r} cut={cut}")
            return max(0, cut), 0
        cut = crop.width - (clusters[-2][1] + 2)
        tdetail(f"edge-icon trail {t!r} cut={cut}")
        return 0, max(0, cut)
    except Exception:  # noqa: BLE001
        return 0, 0


def looks_like_symbol_field(
    region_img: Image.Image | None,
    box: tuple[int, int, int, int],
    text: str,
) -> bool:
    """A box of icons the recogniser read as a handful of letters.

    A line of type is a band of strokes with letter-sized gaps. A grid of
    weapon icons, key badges or map pins is the same recogniser output — a
    box and a few characters — and a card painted over it covers the pictures.
    The difference is in the gaps: icon cells are much further apart than
    letters, and the text that came back has no word in it, only scraps.
    Digits are left alone: a row of counts is a row of counts.
    """
    if region_img is None:
        return False
    raw = str(text or "")
    letters = [c for c in raw if c.isalpha()]
    digits = sum(1 for c in raw if c.isdigit())
    if digits >= len(letters):
        return False
    words = re.findall(r"[A-Za-zА-Яа-яЁё]{3,}", raw)
    if any(len(w) >= 5 for w in words):
        return False
    crop = _metrics_crop(region_img, box)
    if crop is None or crop.width < 48 or crop.height < 16:
        return False
    if crop.width < crop.height * 2.4 and len(letters) >= 6:
        return False
    try:
        mask, _med = _ink_mask(crop)
        if mask is None or not mask.any():
            return False
        cols = mask.any(axis=0)
        runs: list[int] = []
        gaps: list[int] = []
        run = 0
        gap = 0
        seen = False
        for on in cols:
            if on:
                if seen and gap:
                    gaps.append(gap)
                gap = 0
                run += 1
                seen = True
            else:
                if run:
                    runs.append(run)
                    run = 0
                if seen:
                    gap += 1
        if run:
            runs.append(run)
        if len(runs) < 6 or len(gaps) < 4:
            return False
        gaps.sort()
        runs.sort()
        mid_gap = gaps[len(gaps) // 2]
        mid_run = runs[len(runs) // 2]
        # Letter spacing is a fraction of the cap height. A cell in an icon
        # grid is a gap on the order of the cap height itself, repeated.
        return mid_gap >= max(10, int(crop.height * 0.7)) and mid_run <= max(6, crop.height // 3)
    except Exception:  # noqa: BLE001
        return False


# Below this ink height a glyph has too few rows to measure a third of, and the
# number stops being a slant. Measured on 27 faces at kегль 12…64: from 35px the
# italic reads +0.14 and the worst upright +0.01, a gap of 0.13. At 14px the
# same italic reads -0.41 and the worst upright +0.07 - the sign is inverted and
# the gap is negative. Anything smaller is returned as "no measurement", which is
# what the caller already treats as "no italic".
_SLANT_MIN_INK_H = 34

# No face in the package reads above 0.16, and the upright ones stay under 0.02.
# Anything past this is a broken mask rather than a steep italic.
_SLANT_MAX = 0.45

# The line between "slanted" and "upright", for `detect_overlay_typeface`.
#
# Measured on 27 faces at ink height 34px and above, where the measurement is
# stable: the only italic in the package reads +0.143, and the upright faces span
# -0.034…+0.010. 0.05 sits six times above the worst upright and well under the
# italic, which is as close to the middle of the gap as the evidence supports.
#
# It used to be 0.16 and 0.12, both above the italic - the thresholds were set
# against a measurement that returned 0.0 for every input, so no threshold could
# have been checked against a face.
_SLANT_ITALIC = 0.05


def estimate_italic_slant(region_img: Image.Image | None, box: tuple[int, int, int, int]) -> float:
    """Наклон глифов как dx/dy: смещение верхней трети буквы относительно нижней.

    Курсив даёт ~0.14, прямой — около нуля с небольшим отрицательным смещением.
    Замер на 27 гарнитурах пакета: PTSerif-Italic +0.143 против -0.034…+0.010 у
    остальных, то есть зазор порядка 0.13 при пороге где-то посередине.

    Измеряется по каждой букве отдельно, а не по всей строке сразу. Строка без
    разделения на буквы даёт мусор: верхние строки содержат верхушки *одних*
    букв, нижние — низы *других*, и разница центроидов говорит о том, какие
    буквы попали в полосу, а не о наклоне.

    Возвращает 0.0 на высоте чернил меньше `_SLANT_MIN_INK_H`: на мелком тексте
    знак наклона выворачивается, и 0.0 здесь означает «измерять нечего», а не
    «прямой наклон».
    """
    if region_img is None:
        return 0.0
    try:
        import numpy as np

        x1, y1, x2, y2 = box
        w, h = region_img.size
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, max(x1 + 4, x2)), min(h, max(y1 + 4, y2))
        crop = region_img.crop((x1, y1, x2, y2)).convert("L")
        if crop.width < 14 or crop.height < 12:
            return 0.0
        arr = np.asarray(crop, dtype=np.float32)
        mask, _ = _mask_from_array(arr)
        if mask is None:
            return 0.0

        # The check is on the span of the ink, not on the row count: two letters
        # give 8 rows and one word gives 40, and neither has a third of itself to
        # measure.
        rows_all = np.nonzero(mask.any(axis=1))[0]
        if rows_all.size == 0 or int(rows_all[-1] - rows_all[0]) < _SLANT_MIN_INK_H:
            return 0.0

        # Буквы делятся по колонкам без чернил. Разрыв между буквами — это пустая
        # колонка, а внутри буквы пустых колонок почти нет.
        has_ink = mask.any(axis=0)
        spans: list[tuple[int, int]] = []
        start: int | None = None
        for x, v in enumerate(has_ink):
            if v and start is None:
                start = x
            elif not v and start is not None:
                spans.append((start, x))
                start = None
        if start is not None:
            spans.append((start, len(has_ink)))

        num = 0.0
        den = 0.0
        for x0, x1_ in spans:
            if x1_ - x0 < 3:
                continue
            sub = mask[:, x0:x1_]
            rows = np.nonzero(sub.any(axis=1))[0]
            if rows.size < 8:
                continue
            y0, y1_ = int(rows[0]), int(rows[-1])
            glyph_h = y1_ - y0
            if glyph_h < 7:
                continue
            third = max(2, glyph_h // 3)
            low = np.nonzero(sub[y1_ - third : y1_ + 1].any(axis=0))[0]
            high = np.nonzero(sub[y0 : y0 + third].any(axis=0))[0]
            if low.size < 2 or high.size < 2:
                continue
            # Расстояние между центроидами, приведённое к полной высоте буквы:
            # треть против трети — это треть высоты, а не вся высота.
            num += (high.mean() - low.mean()) * 1.5
            den += glyph_h
        if den <= 0:
            return 0.0
        slant = float(num / den)
        # Заглушка: маска не нашла чернил в части строки, и усреднение по
        # выжившим буквам сдвинуто. На длинной строке это шум, а не наклон.
        if abs(slant) > _SLANT_MAX:
            return 0.0
        return slant
    except Exception:
        return 0.0


def ink_is_light(region_img: Image.Image | None, box: tuple[int, int, int, int]) -> bool:
    if region_img is None:
        return False
    key = (id(region_img), box)
    cached = _LIGHT_CACHE.get(key)
    if cached is not None:
        _LIGHT_CACHE.move_to_end(key)
        return cached
    out = _ink_is_light_uncached(region_img, box)
    _LIGHT_CACHE[key] = out
    if len(_LIGHT_CACHE) > _MEASURE_CACHE_MAX:
        _LIGHT_CACHE.popitem(last=False)
    return out


def _ink_is_light_uncached(
    region_img: Image.Image | None, box: tuple[int, int, int, int]
) -> bool:
    """Whether the ink is lighter than the field it sits on.

    The background is the median of the box, the same way the ink mask works: a
    dark field means light text.

    It used to take the top quartile of brightness and call that the text. That
    answer depends on the text filling more than a quarter of the box - on the
    committed corpus, "Handgloves 0123" in NotoSans at 28px covers 26% of its
    own box, so the quartile lands in the field and a light line on a dark panel
    comes back dark. The box was also resized to 48x16 first, and the default
    filter averages a two-pixel stroke away: the quartile fell from 196 to 137
    against a threshold of 150.
    """

    if region_img is None:
        return False
    try:
        x1, y1, x2, y2 = box
        w, h = region_img.size
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, max(x1 + 2, x2)), min(h, max(y1 + 2, y2))
        crop = region_img.crop((x1, y1, x2, y2)).convert("L")
        mask, med = _mask_from_array(np.asarray(crop, dtype=np.float32))
        if mask is None:
            return False
        return float(med) < 128.0
    except Exception:
        return False


def sample_outline_color(
    region_img: Image.Image | None, box: tuple[int, int, int, int]
) -> tuple[float, float, float, float] | None:
    """Цвет обводки глифа (синий glow у заголовков и т.п.)."""
    if region_img is None:
        return None
    try:
        x1, y1, x2, y2 = box
        w, h = region_img.size
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, max(x1 + 4, x2)), min(h, max(y1 + 4, y2))
        crop = region_img.crop((x1, y1, x2, y2)).resize((64, 24), Image.Resampling.BILINEAR)
        px = list(crop.get_flattened_data())
        if not px:
            return None

        def lum(c):
            return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]

        def sat(c):
            mx, mn = max(c[0], c[1], c[2]), min(c[0], c[1], c[2])
            return (mx - mn) / 255.0

        # обводка: средняя яркость + заметный цвет (не чисто белый/чёрный фон)
        cands = [
            c
            for c in px
            if 40 <= lum(c) <= 200 and sat(c) >= 0.12
        ]
        if len(cands) < 4:
            return None
        cands = sorted(cands, key=lambda c: sat(c) * (1.0 - abs(lum(c) - 110) / 180.0), reverse=True)
        take = cands[: max(6, len(cands) // 5)]
        r = sum(c[0] for c in take) / len(take) / 255.0
        g = sum(c[1] for c in take) / len(take) / 255.0
        b = sum(c[2] for c in take) / len(take) / 255.0
        return (r, g, b, 0.95)
    except Exception:
        return None


def detect_overlay_typeface(
    region_img: Image.Image | None,
    box: tuple[int, int, int, int],
    *,
    angle: float,
    raw_w: int,
    raw_h: int,
    src_text: str,
    kind: str,
) -> tuple[str, bool, bool]:
    """family, italic, display_title — только для стилизованных заголовков на арте, НЕ для IDE/сайтов."""
    words = [w for w in re.split(r"\s+", (src_text or "").strip()) if w]
    title_case = bool(words) and sum(1 for w in words if w[:1].isupper()) >= max(1, len(words) // 2)
    light = ink_is_light(region_img, box)
    slant = estimate_italic_slant(region_img, box)

    # UI/меню/IDE — всегда Sans без «арт»-обводки
    if kind in ("ui", "chip", "menu", "dialogue", "dialogue-line", "body"):
        # исключение: крупный светлый italic-заголовок поверх арта
        art = (
            light
            and raw_h >= 36
            and raw_w >= max(140, raw_h * 4)
            and len(words) <= 6
            and (abs(slant) >= _SLANT_ITALIC or abs(angle) >= 4.0)
        )
        if not art:
            return "Sans", False, False

    display = (
        light
        and raw_h >= 32
        and raw_w >= max(120, raw_h * 3.5)
        and len(words) <= 8
        and not bool(RE_JPN.search(src_text or ""))
        and (abs(slant) >= _SLANT_ITALIC or abs(angle) >= 3.0 or (raw_h >= 44 and title_case))
    )

    italic = bool(display and (abs(slant) >= _SLANT_ITALIC or abs(angle) >= 3.0 or raw_h >= 40))
    family = "Liberation Serif" if display else "Sans"
    return family, italic, display


RE_JPN = re.compile("[぀-ヿ㐀-鿿]")


def is_latin_label(text: str) -> bool:
    t = text.strip()
    if not t:
        return False
    lat = len(RE_LAT.findall(t))
    jp = len(RE_JPN.findall(t))
    return lat >= 2 and jp == 0


def is_ink_band_garbage(text: str) -> bool:
    """Склейки ink-band: graduallyrestored… / rdskill / SwordSkill без пробелов.

    НЕ применять к японскому: в JP нет пробелов, иначе режем все нормальные фразы.
    """
    t = re.sub(r"\s+", " ", (text or "").strip())
    if not t:
        return True
    # японский UI/диалог — не «склейка без пробелов»
    if len(RE_JPN.findall(t)) >= 2:
        return False
    compact = re.sub(r"\s+", "", t)
    # A genuine glue has no spaces at all. The old test was "spaces <= len/18",
    # which conflated having no spaces with having few: "Popular repositories"
    # is 19 characters with one space, and one is exactly 19//18, so a heading
    # read at 100% confidence was thrown away as a run-together. Measured on a
    # GitHub page, ten of twenty-three ordinary two-word headings were dropped -
    # Repository settings, Organization settings, Security advisories, Language
    # statistics. The space is the evidence against a glue, not for it.
    if len(compact) >= 16 and " " not in t:
        return True
    # обрубок заголовка, прилипший к описанию
    if re.search(r"(?<![a-z ])(?:rd|ord|word|s)?skill\b", t, re.I) and not re.search(
        r"\b(sword|support|combination|skill)\s+skill\b|\bskills?\b", t, re.I
    ):
        if " " not in t or re.search(r"[a-z]{4,}skill\b", t, re.I):
            return True
    if re.fullmatch(r"[A-Za-z]{0,3}skill", t, re.I):
        return True
    # слова без гласных / каша
    words = re.findall(r"[A-Za-z]{4,}", t)
    if words and sum(1 for w in words if not re.search(r"[aeiouy]", w, re.I)) >= max(2, len(words) // 2):
        return True
    # A line that is mostly one- and two-letter fragments is a merger, not text.
    # Every other check here looks at the long words, and a merger produces
    # almost none: "on o 'ses o sdn o ns- ee n 2 weeks ago" was three columns of
    # a table read as one line, and it had exactly two real words among thirteen
    # fragments. It was translated and shown to the user as gibberish, because
    # a filter that only reads the long words sees a perfectly good sentence.
    tokens = re.findall(r"[A-Za-zА-Яа-яЁё']+", t)
    if len(tokens) >= 5:
        stubs = sum(1 for w in tokens if len(w) <= 2)
        # Three fifths, not half. A merger lands around 64% fragments and a real
        # sentence around 50% - "Press E to interact with the do or" is half
        # short words and is a perfectly good line a user would want translated,
        # so the threshold has to sit above it and below the merger rather than
        # in between the two by arithmetic accident.
        if stubs * 5 >= len(tokens) * 3 and stubs >= 4:
            return True
    return False


def reread_core_ink(
    region_img: Image.Image | None,
    box: tuple[int, int, int, int],
    engine,
) -> list[tuple[str, float]]:
    """Read a box from the core of its ink, and say how sure the engine is.

    Display type with a thick outline is letters *and* the border round them, so
    the recogniser is looking at a blob with holes rather than a word. It does not
    fail loudly on that - it returns a word at a middling score, and nothing
    downstream can tell a guess from a reading.

    Taking the border off before reading fixes it, and the way to know what the
    border is without naming any colour is distance: the background is the most
    common colour in the box, the fill of an outlined letter is the colour
    furthest from it, and the border is in between because it is a stroke. Keep
    only the far end of that order and the letter is left solid.

    The detector runs on the result as well as the recogniser, because the box of
    a slanted line is mostly background and handing that straight to a recogniser
    - which expects a line already cut out - reads the letters scattered across
    the middle of it.

    Returns what was read and what it scored, so the caller can decide against
    what it already has. That decision is the reason this is safe rather than a
    second opinion trusted blindly: on ordinary text the core reading scores lower
    and is thrown away, so the extra work changes the outcome only where the first
    reading was weak.
    """
    if region_img is None or engine is None:
        return []
    crop = _metrics_crop(region_img, box)
    if crop is None or crop.width < 8 or crop.height < 8:
        return []
    try:
        import numpy as np

        px = np.asarray(crop.convert("RGB"), dtype=np.float32) / 255.0
        if px.ndim != 3 or px.shape[2] < 3:
            return []
        keys = (px * 8.0).astype(np.int16).reshape(-1, 3)
        values, counts = np.unique(keys, axis=0, return_counts=True)
        bg = (values[int(np.argmax(counts))] + 1) / 8.0
        dist = np.abs(px - bg).sum(axis=2)
        # The floor is what keeps a flat box from becoming a rectangle of ink:
        # nothing on a screen is a third of the whole colour range away from its
        # own background, so below that there is nothing to read either way.
        cut = max(float(np.percentile(dist, CORE_INK_PERCENTILE)), 0.35)
        core = Image.fromarray(
            np.where(dist >= cut, 0, 255).astype(np.uint8), mode="L"
        ).convert("RGB")
        out = engine(np.asarray(core))
        txts = getattr(out, "txts", None)
        scores = getattr(out, "scores", None)
        if not txts:
            return []
        read: list[tuple[str, float]] = []
        for i, t in enumerate(txts):
            text = str(t).strip()
            if not text:
                continue
            score = float(scores[i]) if scores is not None and i < len(scores) else 0.0
            score = score * 100.0 if score <= 1.0 else score
            read.append((text, score))
        return read
    except Exception as exc:  # noqa: BLE001 - a second look is never fatal
        tdetail(f"core-reread failed {type(exc).__name__}: {exc}")
        return []


def looks_like_game_ui(lines: list[dict]) -> bool:
    """Статы/инвентарь / VN-меню — не видео-субтитры."""
    if len(lines) < 3:
        return False
    # CJK/кану Rapid часто читает криво — это не EN HUD
    if script_cjk_score(lines) >= 6:
        return False
    ui_hits, short, spoken = game_ui_score(lines)
    if spoken >= 1 and ui_hits < 3:
        return False
    if ui_hits >= 3:
        return True
    # SoftMoney / Ren'Py: куча коротких пунктов меню без HP/STR
    prompts = sum(1 for p in lines if looks_like_ui_prompt(str(p.get("text", ""))))
    if prompts >= 4:
        return True
    if short >= 5 and prompts >= 3:
        return True
    return False


def vn_lines_should_merge(prev: dict, nxt: dict) -> bool:
    """Склеивать только соседние строки одного пузыря, не меню+диалог."""
    ta = str(prev.get("text", "")).strip()
    tb = str(nxt.get("text", "")).strip()
    if looks_like_ui_prompt(ta) or looks_like_ui_prompt(tb):
        return False
    # name plate left of a wide gap must stay separate from the body.
    from ..layout.name_gap import should_refuse_merge_for_name_gap

    if should_refuse_merge_for_name_gap(prev, nxt):
        return False
    # два коротких законченных лейбла — разные кнопки
    if len(ta) <= 24 and len(tb) <= 24 and not dialogue_looks_incomplete(ta):
        return False
    # Кнопки управления (AUTO, OFF, SKIP) и похожие — не склеивать
    if (ta.upper() in {"AUTO", "OFF", "SKIP", "PAUSE", "PLAY"} or
        tb.upper() in {"AUTO", "OFF", "SKIP", "PAUSE", "PLAY"}):
        return False
    # Короткие UI-элементы рядом — не склеивать
    if len(ta) <= 12 and len(tb) <= 12 and not dialogue_looks_incomplete(ta) and not dialogue_looks_incomplete(tb):
        return False
    # A bubble has a speaker. A tip, a codex entry and a paragraph do not, and
    # merging those into a bubble is what turned a four-line game hint into one
    # card, joined mid-word: "Safe Areas" came out as "Safe Are as" because the
    # lines were stacked with nothing saying they belonged together. The
    # evidence is a speaker prefix on either line, or a line that plainly stops
    # mid-sentence, and both are things a paragraph never has.
    # Density, which is what the tests above could not see. A spoken line is
    # three to six words; a codex entry or a paragraph is seven or more, and a
    # line of ten words with no capital in it is a sentence, not a half-spoken
    # bubble. Measured on the reported hint: 10, 7 and 9 words, of which 3, 0 and
    # 0 start with a capital. A bubble is short and starts with a name.
    # The first line of a bubble names the speaker, and that decides the rest of
    # it. "believe me, we all did at some point" is nine words with no capital
    # and reads as prose on its own, but under a line that starts with a
    # speaker's name it is
    # the second half of what she said, and refusing to join them is how a
    # dialogue ends up as two cards - one of them half a sentence.
    speaker = looks_like_spoken_line(ta) or looks_like_spoken_line(tb)
    if not speaker:
        for line in (ta, tb):
            if _reads_as_prose(line):
                return False
    if not (
        speaker
        or dialogue_looks_incomplete(ta)
        or dialogue_looks_incomplete(tb)
    ):
        return False
    ax1, ay1, ax2, ay2 = prev["box"]
    bx1, by1, bx2, by2 = nxt["box"]
    # разные колонки (меню слева, попап по центру)
    if abs(ax1 - bx1) > 140 and abs(((ax1 + ax2) / 2) - ((bx1 + bx2) / 2)) > 180:
        return False
    gap = int(by1 - ay2)
    return gap <= max(28, int(prev.get("line_height", 16) * 1.6))


def looks_like_ui_prompt(text: str) -> bool:
    """Короткие подписи кнопок (Zoom In / Location / Confirm) — не склеивать в одну строку."""
    t = unglue_english(re.sub(r"\s+", " ", (text or "").strip()))
    if len(t) < 2 or len(t) > 32:
        return False
    if looks_like_spoken_line(t):
        return False
    # продолжение абзаца / lowercase — не кнопка
    if t[:1].islower() and len(t) >= 8:
        return False
    if re.search(
        r"\b(damage|enemies|character|restored|gradually|assistance|surrounding|"
        r"period|higher|deals|creates|zone)\b",
        t,
        re.I,
    ):
        return False
    words = [w for w in re.findall(r"[A-Za-z']+", t)]
    return 1 <= len(words) <= 4


def ui_text_fingerprint(text: str) -> str:
    """ZoomIn ≈ Zoom In, wayponts ≈ waypoints."""
    t = unglue_english(re.sub(r"\s+", " ", (text or "").strip())).casefold()
    t = re.sub(r"[^a-z0-9]+", "", t)
    # частые OCR-опечатки в UI
    for a, b in (
        ("wayponts", "waypoints"),
        ("cheskpoin", "checkpoint"),
        ("chekpoirt", "checkpoint"),
        ("chekpoint", "checkpoint"),
        ("zo0min", "zoomin"),
        ("resst", "reset"),
        ("visw", "view"),
        ("rernove", "remove"),
        ("bamnve", "remove"),
    ):
        t = t.replace(a, b)
    return t


def dedupe_near_ui_lines(lines: list[dict]) -> list[dict]:
    """Убирает дубли OCR и обрывки (che ⊂ Checkpoint, ocation ⊂ Location)."""
    lines = merge_paragraph_tail(dedupe_paragraph_reads(lines))
    if not lines:
        return []
    ordered = sorted(lines, key=lambda p: (-len(str(p.get("text", ""))), p["box"][1], p["box"][0]))
    kept: list[dict] = []
    for p in ordered:
        t = unglue_english(str(p.get("text", "")).strip())
        if not t:
            continue
        # Short junk, but not short words. The old rule dropped anything of
        # three letters or fewer with three latin letters, which is `bin`,
        # `src`, `nix`, `git`, `env`, `dev`, `app`, `web`, `OK`, `NEW` - all of
        # them real text a user would want translated, and all of them lost. What
        # separates "che" from "bin" is not the length: it is that a misread
        # fragment is not a word anybody uses, and the read of a real short word
        # is confident where a fragment of a scan is not. A single letter, a
        # lone digit and a run of the same character are still dropped, because
        # those never are words.
        latin_len = len(RE_LAT.findall(t))
        if latin_len <= 3 and len(RE_KANA.findall(t)) == 0 and len(t) <= 4:
            if not _is_real_short_word(t, p):
                continue
        if re.fullmatch(r"lori|tori|iori", t, re.I):
            continue
        if re.fullmatch(r"[?¿!.,:;]+.*", t) and len(RE_LAT.findall(t)) < 6:
            t = re.sub(r"^[?¿!.,:;]+", "", t).strip()
            if len(RE_LAT.findall(t)) < 4:
                continue
        fp = ui_text_fingerprint(t)
        # ui_text_fingerprint is a Latin heuristic: it collapses pure kana to
        # nothing, which used to delete Japanese buttons.
        if len(fp) < 2 and RE_KANA.search(t):
            fp = re.sub(r"\s+", "", t)
        if len(fp) < 2:
            continue
        cy = (p["box"][1] + p["box"][3]) * 0.5
        cx = (p["box"][0] + p["box"][2]) * 0.5
        drop = False
        for q in kept:
            qt = str(q.get("text", ""))
            qfp = ui_text_fingerprint(qt)
            qcy = (q["box"][1] + q["box"][3]) * 0.5
            qcx = (q["box"][0] + q["box"][2]) * 0.5
            near = abs(cy - qcy) < 28 and abs(cx - qcx) < 160
            same_row = abs(cy - qcy) < 22
            if fp == qfp and (near or same_row):
                # Identical text is not automatically a duplicate. A file
                # listing says "12 hours ago" in every row, and a commit graph
                # says "3 weeks ago" in half of them: those are separate
                # elements that happen to read the same, and dropping them left
                # a page with a column of nothing. What makes two reads
                # duplicates is that they describe the same place on screen -
                # so the test is their boxes, not their text.
                x1, y1, x2, y2 = p["box"]
                qx1, qy1, qx2, qy2 = q["box"]
                same_place = (
                    abs(y1 - qy1) <= max(6, (y2 - y1) // 2)
                    and abs(x1 - qx1) <= max(24, (x2 - x1) // 2)
                )
                if not same_place:
                    continue
                drop = True
                break
            # короткий фрагмент внутри более длинной подписи рядом
            if near and len(fp) < len(qfp) and fp in qfp and len(fp) <= 10:
                drop = True
                break
            if near and len(qfp) < len(fp) and qfp in fp and len(qfp) <= 10:
                kept.remove(q)
                break
        if drop:
            continue
        copy = dict(p)
        copy["text"] = t
        kept.append(copy)
    kept.sort(key=lambda p: (p["box"][1], p["box"][0]))
    return kept


def unglue_english(text: str) -> str:
    """Общий OCR-unglue: camelCase, aB, word-break по словарю — без точечных фраз."""
    t = re.sub(r"\s+", " ", (text or "").strip())
    if not t:
        return t
    # японский / CJK — не трогаем (иначе 377.8MB → «377.8 MB» и ломает эвристики)
    if len(RE_JPN.findall(t)) >= 2:
        return t
    before = t
    # A file extension is one token, whatever the recogniser did with the dot.
    # RapidOCR reads `CHANGELOG.md` as `CHANGELOG. md` with a space, and the
    # backend then translates the two halves separately: it read `md` as the
    # US state and produced "ИЗМЕНЕНИЯ. Мэриленд" on a repository page, on
    # LICENSE.md and on README.md alike. The dot belongs to the name, so it is
    # put back before anything else looks at the text.
    t = re.sub(r"\.\s+(?=(?:%s)\b)" % "|".join(sorted(_FILE_EXTENSIONS)), ".", t)
    # camelCase / lowerUpper
    t = re.sub(r"([a-z])([A-Z])", r"\1 \2", t)
    t = re.sub(r"([A-Z]{2,})([a-z]{2,})", r"\1 \2", t)  # HPis → HP is
    t = re.sub(r"([A-Za-z])(\d)", r"\1 \2", t)
    t = re.sub(r"(\d)([A-Za-z])", r"\1 \2", t)
    # Частые glued-токены RapidOCR: Andyou? / nightmare-onethat / neverhopetoafford.
    def _break_piece(piece: str) -> str:
        m = re.fullmatch(r"([^A-Za-z']*)([A-Za-z']+)([^A-Za-z']*)", piece)
        if not m:
            return piece
        pre, core, post = m.groups()
        if len(core) >= 4:
            bare = core.replace("'", "")
            broken = _word_break_token(bare if bare.isalpha() else core)
            if "'" in core and broken.lower().startswith("ones "):
                broken = "One's " + broken[5:]
            core = broken
        # Specific OCR artifacts from game UI
        core = core.replace("laueddnu", "never change")
        core = core.replace("lauddu", "never change")
        return pre + core + post

    parts = []
    for tok in t.split(" "):
        if re.search(r"[-—]", tok):
            chunks = re.split(r"([-—])", tok)
            parts.append("".join(_break_piece(ch) for ch in chunks))
        else:
            parts.append(_break_piece(tok))
    t = re.sub(r"\s{2,}", " ", " ".join(parts)).strip()
    if t != before:
        tdetail(f"unglue '{before[:52]}' -> '{t[:52]}'")
    return t


def merge_subtitle_cluster(cluster: list[dict], rw: int = 0, rh: int = 0) -> dict:
    if not cluster:
        return {
            "text": "",
            "box": (0, 0, 8, 8),
            "line_height": 14,
            "conf": 0,
            "wrap": True,
            "engine": "rapidocr",
            "kind": "dialogue",
            "angle": 0.0,
            "line_boxes": [],
        }
    if rw <= 0:
        rw = max(int(p["box"][2]) for p in cluster) + 80
    if rh <= 0:
        rh = max(int(p["box"][3]) for p in cluster) + 40
    cleaned: list[dict] = []
    for p in cluster:
        t = str(p.get("text", "")).strip()
        if is_subtitle_junk_line(t, p.get("box"), rw, rh):
            bx = p.get("box", (0, 0, 0, 0))
            tlog(
                f"sub-drop-junk '{t[:36]}' "
                f"box={bx[2]-bx[0]}x{bx[3]-bx[1]} y={bx[1]}"
            )
            continue
        cleaned.append(p)
    # якорь (Name:) всегда оставляем
    if not cleaned:
        cleaned = [cluster[0]]
    cluster = sorted(cleaned, key=lambda p: (p["box"][1], p["box"][0]))
    parts = []
    line_boxes: list[dict] = []
    for p in cluster:
        raw = normalize_subtitle_ocr(str(p.get("text", "")).strip())
        raw = unglue_english(raw)
        raw = strip_watermark_tail(raw)
        if raw and not is_subtitle_junk_line(raw, p.get("box"), rw, rh):
            parts.append(raw)
            line_boxes.append(
                {
                    "box": tuple(int(v) for v in p["box"]),
                    "text": raw,
                    "line_height": max(10, int(p.get("line_height", 18))),
                    "angle": float(p.get("angle", 0) or 0),
                    "conf": float(p.get("conf", 0) or 0),
                }
            )
    if not parts:
        raw0 = unglue_english(normalize_subtitle_ocr(str(cluster[0].get("text", ""))))
        parts = [raw0 or "?"]
        line_boxes = [
            {
                "box": tuple(int(v) for v in cluster[0]["box"]),
                "text": parts[0],
                "line_height": max(10, int(cluster[0].get("line_height", 18))),
                "angle": float(cluster[0].get("angle", 0) or 0),
                "conf": float(cluster[0].get("conf", 0) or 0),
            }
        ]
    # вертикальный стек (2 строки letterbox) — сохраняем \n для cover-высоты
    if len(line_boxes) >= 2:
        cy = [(p["box"][1] + p["box"][3]) / 2 for p in line_boxes]
        vertical = max(cy) - min(cy) > max(
            10, median(p.get("line_height", 18) for p in line_boxes) * 0.55
        )
    else:
        vertical = False
    text = "\n".join(parts) if vertical else " ".join(parts)
    text = strip_watermark_tail(normalize_subtitle_ocr(text.replace("\n", " ")) if not vertical else text)
    if vertical:
        text = "\n".join(
            strip_watermark_tail(normalize_subtitle_ocr(unglue_english(x)))
            for x in text.split("\n")
            if x.strip()
        )
    x1 = min(p["box"][0] for p in line_boxes)
    y1 = min(p["box"][1] for p in line_boxes)
    x2 = max(p["box"][2] for p in line_boxes)
    y2 = max(p["box"][3] for p in line_boxes)
    lh = max(10, int(median(p.get("line_height", 18) for p in line_boxes)))
    # Бокс — объединение боксов его же строк, и он таким и остаётся. Ужимать его
    # по высоте нельзя: получившийся прямоугольник перестаёт содержать то, что
    # должен закрывать, и карточка рисуется там, где текста нет, — описание внизу
    # кадра уезжало к верхней кромке именно из-за такого ужимания.
    span_w = max(8, x2 - x1)
    # Absolute 1800/±900 assumed FHD; on ultrawide/4K that cropped real dialogue.
    frame_w = int(rw) if rw else max(span_w, x2)
    max_span = max(800, int(frame_w * 0.94))
    half = max(400, int(frame_w * 0.47))
    if span_w > max_span:
        cx = (x1 + x2) / 2
        x1, x2 = int(cx - half), int(cx + half)
    ang = 0.0
    try:
        angs = [float(p.get("angle", 0) or 0) for p in line_boxes]
        ang = median(angs) if angs else 0.0
    except Exception:
        ang = 0.0
    keep_lines = line_boxes if vertical and len(line_boxes) >= 2 else []
    tlog(
        f"sub-merge n={len(line_boxes)} vert={int(vertical)} "
        f"line_boxes={len(keep_lines)} box={x2-x1}x{y2-y1} chars={len(text)} "
        f"preview={text[:70]!r}"
    )
    if keep_lines:
        for i, lb in enumerate(keep_lines):
            bx1, by1, bx2, by2 = lb["box"]
            tlog(
                f"sub-line[{i}] y={by1}-{by2} w={bx2-bx1} "
                f"chars={len(lb['text'])}"
            )
            tdetail(f"sub-line[{i}] text={lb['text'][:48]!r}")
    return {
        "text": text,
        "box": (int(x1), int(y1), int(x2), int(y2)),
        "line_height": lh,
        "conf": sum(float(p.get("conf", 0)) for p in line_boxes) / max(1, len(line_boxes)),
        "wrap": True,
        "engine": "rapidocr",
        "kind": "dialogue",
        "angle": float(ang),
        "line_boxes": keep_lines,
    }


def _merge_vn_band_cluster(cluster: list[dict]) -> dict:
    """Склеивает 2–3 OCR-строки одного VN-пузыря в блок с переносами."""
    if len(cluster) == 1:
        copy = dict(cluster[0])
        t0 = unglue_english(normalize_subtitle_ocr(str(copy.get("text", "")).strip()))
        copy["text"] = t0
        # длинный пузырь / перенос — dialogue (cover + размер), не ui-чип
        copy["kind"] = "dialogue" if (len(t0) >= 28 or copy.get("wrap")) else "ui"
        copy["wrap"] = True
        return copy
    texts = [unglue_english(normalize_subtitle_ocr(str(p.get("text", "")).strip())) for p in cluster]
    x1 = min(p["box"][0] for p in cluster)
    y1 = min(p["box"][1] for p in cluster)
    x2 = max(p["box"][2] for p in cluster)
    y2 = max(p["box"][3] for p in cluster)
    return {
        "text": "\n".join(t for t in texts if t),
        "box": (x1, y1, x2, y2),
        "line_height": max(int(p.get("line_height", 16)) for p in cluster),
        "kind": "dialogue",
        "wrap": True,
        "engine": cluster[0].get("engine", "rapidocr"),
        "line_boxes": [
            {
                "text": t,
                "box": p["box"],
                "line_height": p.get("line_height", 16),
            }
            for p, t in zip(cluster, texts)
            if t
        ],
        "conf": min(float(p.get("conf", 60)) for p in cluster),
    }
