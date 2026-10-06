"""name/dialogue horizontal gap (name-plate layouts).

```text
speaker left | large gap | body right
```

Starting threshold:

```text
gap > max(1.5 * median glyph height, 16 px)
```

Gap alone is never enough: also require vertical overlap, left-side name-like
text, and right-side dialogue-like text. Multi-word names are allowed.
"""
from __future__ import annotations

from ..core.text import looks_like_spoken_line
from ..typography.metrics import looks_like_ui_prompt

NAME_DIALOGUE_GAP_MIN_PX = 16
NAME_DIALOGUE_GAP_GLYPH_MULT = 1.5


def name_dialogue_gap_threshold(median_glyph_height: float) -> float:
    """Minimum empty space (px) that may separate a speaker from its body."""
    from ..profile import speaker_gap_params

    min_px, glyph_mult = speaker_gap_params()
    h = max(1.0, float(median_glyph_height or 0.0))
    return max(float(min_px), float(glyph_mult) * h)


def horizontal_gap_px(
    left: tuple[int, int, int, int],
    right: tuple[int, int, int, int],
) -> int:
    """Empty pixels between two boxes on the x-axis (0 if they touch/overlap)."""
    lx1, _, lx2, _ = (int(v) for v in left)
    rx1, _, rx2, _ = (int(v) for v in right)
    if lx2 <= rx1:
        return max(0, rx1 - lx2)
    if rx2 <= lx1:
        return max(0, lx1 - rx2)
    return 0


def vertical_overlap_ratio(
    a: tuple[int, int, int, int],
    b: tuple[int, int, int, int],
) -> float:
    """Share of the shorter box's height that overlaps the other on y."""
    _, ay1, _, ay2 = (int(v) for v in a)
    _, by1, _, by2 = (int(v) for v in b)
    overlap = max(0, min(ay2, by2) - max(ay1, by1))
    shorter = max(1, min(ay2 - ay1, by2 - by1))
    return float(overlap) / float(shorter)


def _looks_like_name_plate(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return False
    # Multi-word names are allowed. Cap length so a sentence fails.
    if len(t) > 40:
        return False
    words = [w for w in t.replace("'", "").split() if w]
    if not words or len(words) > 5:
        return False
    # A long spoken sentence is not a name plate even if capitalized.
    if looks_like_spoken_line(t) and len(words) >= 4:
        return False
    # Prefer capitalized name tokens; allow ALL-CAPS plates.
    caps = sum(1 for w in words if w[:1].isupper() or w.isupper())
    if caps < max(1, len(words) - 1):
        return False
    # looks_like_ui_prompt treats many short labels as buttons — exclude those
    # that are clearly proper-name shaped (Title Case / ALL CAPS, no verbs).
    if looks_like_ui_prompt(t):
        verbish = any(
            w.casefold()
            in {
                "go",
                "open",
                "close",
                "confirm",
                "cancel",
                "start",
                "stop",
                "skip",
                "auto",
                "map",
                "zoom",
                "back",
                "next",
                "ok",
            }
            for w in words
        )
        if verbish or not all(w[:1].isupper() or w.isupper() for w in words):
            return False
    return True


def is_single_line_block(par: dict) -> bool:
    """Whether a block holds one line of text, which is what a name plate is.

    A name plate is a name: one line, one row. A long quest description was
    handed over as one 1012x145 box holding five rows of 30px leading, it sat
    just above another block, and the shape of the pair made it a "speaker". The card then took `kind=name`, `oneline=True` and sized its
    type from the 145-pixel box: 134-pixel letters on a menu line, and a panel
    that reached 300 pixels to the left of the text it was supposed to replace.

    Two things say "not one line", and neither is a game name: rows the
    recogniser measured, and a box taller than one line's own height.
    """
    rows = [r for r in (par.get("line_boxes") or []) if r.get("box")]
    if len(rows) >= 2:
        return False
    box = tuple(int(v) for v in (par.get("box") or (0, 0, 0, 0)))
    if box[3] <= box[1]:
        return True
    lh = int(par.get("line_height") or 0)
    if lh > 0 and (box[3] - box[1]) >= lh * 2.2:
        return False
    return True


def is_name_dialogue_side_pair(
    left: dict,
    right: dict,
    *,
    median_glyph_height: float | None = None,
) -> bool:
    """True when left looks like a speaker plate and right like its body."""
    if not is_single_line_block(left):
        return False
    lb = tuple(int(v) for v in left.get("box", (0, 0, 0, 0)))
    rb = tuple(int(v) for v in right.get("box", (0, 0, 0, 0)))
    if lb[2] <= lb[0] or rb[2] <= rb[0]:
        return False
    # Left must actually be on the left.
    if lb[0] > rb[0]:
        return False
    glyph_h = float(
        median_glyph_height
        if median_glyph_height is not None
        else max(
            int(left.get("line_height") or 0),
            int(right.get("line_height") or 0),
            lb[3] - lb[1],
            12,
        )
    )
    gap = horizontal_gap_px(lb, rb)
    if gap <= name_dialogue_gap_threshold(glyph_h):
        return False
    if vertical_overlap_ratio(lb, rb) < 0.35:
        return False
    left_text = str(left.get("text") or "").strip()
    right_text = str(right.get("text") or "").strip()
    if not _looks_like_name_plate(left_text):
        return False
    if not (
        looks_like_spoken_line(right_text)
        or len(right_text) >= 28
        or str(right.get("kind") or "") in ("dialogue", "body", "subtitle")
    ):
        return False
    # Body should be wider than the name plate in this layout family.
    if (rb[2] - rb[0]) < (lb[2] - lb[0]) * 1.1:
        return False
    return True


def is_name_above_body(
    upper: dict,
    lower: dict,
    *,
    median_glyph_height: float | None = None,
) -> bool:
    """Name plate sitting on a dialogue box, close in y, not a wide side gap.

    Family A (novel strip / text-heavy page): the speaker and the body stay
    separate even when the plate almost touches the first line.
    """
    if not is_single_line_block(upper):
        return False
    ub = tuple(int(v) for v in upper.get("box", (0, 0, 0, 0)))
    lb = tuple(int(v) for v in lower.get("box", (0, 0, 0, 0)))
    if ub[2] <= ub[0] or lb[2] <= lb[0] or ub[3] <= ub[1] or lb[3] <= lb[1]:
        return False
    # Upper must sit above, allowing a few pixels of overlap on the box edge.
    if ub[3] > lb[1] + 4:
        return False
    glyph_h = float(
        median_glyph_height
        if median_glyph_height is not None
        else max(
            int(upper.get("line_height") or 0),
            int(lower.get("line_height") or 0),
            ub[3] - ub[1],
            12,
        )
    )
    gap = lb[1] - ub[3]
    if gap > max(40.0, 1.6 * glyph_h):
        return False
    name_w = ub[2] - ub[0]
    # Name shares the body's column: inside it, or overlapping it.
    inside = ub[0] >= lb[0] - 24 and ub[2] <= lb[2] + 32
    overlap = min(ub[2], lb[2]) - max(ub[0], lb[0])
    if not inside and overlap < max(12, int(name_w * 0.4)):
        return False
    name = str(upper.get("text") or "").strip()
    body = str(lower.get("text") or "").strip()
    if not _looks_like_name_plate(name):
        return False
    if len(name) >= len(body):
        return False
    tall = (lb[3] - lb[1]) >= glyph_h * 1.6
    if not (
        looks_like_spoken_line(body)
        or len(body) >= 28
        or tall
        or "\n" in body
        or str(lower.get("kind") or "") in ("dialogue", "body", "subtitle")
    ):
        return False
    return True


def should_refuse_merge_for_name_gap(
    prev: dict,
    nxt: dict,
    *,
    median_glyph_height: float | None = None,
) -> bool:
    """Do not glue a speaker plate to its body.

    Horizontal wide gap (family A, side by side) or a plate sitting just
    above the body (family A, stacked). Either order of the pair counts.
    """
    pairs = ((prev, nxt), (nxt, prev))
    for left, right in pairs:
        if is_name_dialogue_side_pair(
            left, right, median_glyph_height=median_glyph_height
        ):
            return True
    for upper, lower in pairs:
        if is_name_above_body(
            upper, lower, median_glyph_height=median_glyph_height
        ):
            return True
    return False


def annotate_side_by_side_name_body(lines: list[dict]) -> list[dict]:
    """Mark left-name / right-body pairs that OCR delivered as separate boxes."""
    if len(lines) < 2:
        return lines
    out = [dict(p) for p in lines]
    used: set[int] = set()
    for i, left in enumerate(out):
        if i in used:
            continue
        for j, right in enumerate(out):
            if j == i or j in used:
                continue
            if not is_name_dialogue_side_pair(left, right):
                continue
            out[i]["kind"] = "name"
            out[i]["semantic_role"] = "SPEAKER"
            out[i]["oneline"] = True
            out[i]["wrap"] = False
            if str(out[j].get("kind") or "") not in ("dialogue", "body", "subtitle"):
                out[j]["kind"] = "dialogue"
            out[j]["semantic_role"] = "DIALOGUE"
            used.add(i)
            used.add(j)
            break
    return out


def annotate_stacked_name_body(lines: list[dict]) -> list[dict]:
    """Mark a name plate that sits just above its body as speaker + dialogue."""
    if len(lines) < 2:
        return lines
    out = [dict(p) for p in lines]
    used: set[int] = set()
    for i, upper in enumerate(out):
        if i in used:
            continue
        for j, lower in enumerate(out):
            if j == i or j in used:
                continue
            if not is_name_above_body(upper, lower):
                continue
            out[i]["kind"] = "name"
            out[i]["semantic_role"] = "SPEAKER"
            out[i]["oneline"] = True
            out[i]["wrap"] = False
            if str(out[j].get("kind") or "") not in ("dialogue", "body", "subtitle"):
                out[j]["kind"] = "dialogue"
            out[j]["semantic_role"] = "DIALOGUE"
            out[j]["wrap"] = True
            out[j]["oneline"] = False
            used.add(i)
            used.add(j)
            break
    return out
