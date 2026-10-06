"""OCR reading order

Horizontal:

```text
line cluster by y → x sort within line
```

Vertical Japanese (only with confidence evidence — never from one narrow crop):

```text
detect vertical orientation → columns right-to-left → characters top-to-bottom
```

Column helpers live here as the OCR-facing API; implementations remain in
``layout.grouping`` so live layout code keeps working without a mass import rewrite.
"""

from __future__ import annotations

from typing import Any

# re-export column/paragraph primitives under the reading-order home.
from ..layout.grouping import (  # noqa: F401
    box_crosses_gutter,
    column_bounds_from_centers,
    gap_hits_gutter,
    image_column_gutters,
    merge_paragraphs,
    page_column_gutters,
)


def _box(p: dict) -> tuple[int, int, int, int]:
    b = p.get("box") or (0, 0, 0, 0)
    return int(b[0]), int(b[1]), int(b[2]), int(b[3])


def _cjk_ratio(text: str) -> float:
    body = (text or "").replace(" ", "")
    if not body:
        return 0.0
    n = sum(
        1
        for ch in body
        if "\u3040" <= ch <= "\u30ff" or "\u4e00" <= ch <= "\u9fff"
    )
    return n / len(body)


def cluster_lines_by_y(lines: list[dict], *, y_bin: int = 12) -> list[list[dict]]:
    """Group boxes whose tops fall in the same y-band (top→bottom)."""
    if not lines:
        return []
    keyed = sorted(lines, key=lambda p: (_box(p)[1] // max(1, y_bin), _box(p)[0]))
    bands: list[list[dict]] = []
    current: list[dict] = []
    current_bin: int | None = None
    for p in keyed:
        b = _box(p)[1] // max(1, y_bin)
        if current_bin is None or b == current_bin:
            current.append(p)
            current_bin = b
        else:
            bands.append(current)
            current = [p]
            current_bin = b
    if current:
        bands.append(current)
    return bands


def sort_horizontal(lines: list[dict]) -> list[dict[str, Any]]:
    """horizontal: y-clusters, then left→right inside each line."""
    out: list[dict[str, Any]] = []
    for band in cluster_lines_by_y(lines):
        out.extend(sorted(band, key=lambda p: _box(p)[0]))
    return out


def vertical_confidence(lines: list[dict]) -> float:
    """0..1 evidence that the set is vertical Japanese (not a thin accident)."""
    if len(lines) < 3:
        return 0.0
    tall_thin = 0
    cjk = 0
    xs: list[float] = []
    for p in lines:
        x1, y1, x2, y2 = _box(p)
        w, h = max(1, x2 - x1), max(1, y2 - y1)
        if h >= w * 1.4 and w <= 48:
            tall_thin += 1
        if _cjk_ratio(str(p.get("text", ""))) >= 0.4:
            cjk += 1
        xs.append((x1 + x2) / 2.0)
    n = len(lines)
    shape = tall_thin / n
    script = cjk / n
    # Distinct x-columns (vertical text stacks in columns).
    xs_sorted = sorted(xs)
    col_gaps = sum(
        1 for a, b in zip(xs_sorted, xs_sorted[1:]) if (b - a) >= 18
    )
    columns = min(1.0, col_gaps / max(1, n - 1) * 2)
    return min(1.0, 0.45 * shape + 0.35 * script + 0.20 * columns)


def looks_vertical(lines: list[dict]) -> bool:
    """Enable vertical mode only with enough JP evidence."""
    if len(lines) < 3:
        return False
    # Cyrillic / Latin UIs must never take the vertical-JP path.
    cjk_n = sum(1 for p in lines if _cjk_ratio(str(p.get("text", ""))) >= 0.4)
    if cjk_n < max(2, (len(lines) + 1) // 2):
        return False
    widths = [max(1, _box(p)[2] - _box(p)[0]) for p in lines]
    if max(widths) <= 40 and len(lines) < 5:
        return False
    return vertical_confidence(lines) >= 0.55


def sort_vertical_japanese(lines: list[dict]) -> list[dict[str, Any]]:
    """Columns right→left; within a column top→bottom."""
    if not lines:
        return []
    # Bucket by x-center into columns (~24 px).
    items = [(p, (_box(p)[0] + _box(p)[2]) / 2.0) for p in lines]
    items.sort(key=lambda t: t[1], reverse=True)  # rightmost column first
    columns: list[list[dict]] = []
    current: list[dict] = []
    current_x: float | None = None
    for p, cx in items:
        if current_x is None or abs(cx - current_x) <= 24:
            current.append(p)
            current_x = cx if current_x is None else (current_x * 0.7 + cx * 0.3)
        else:
            columns.append(current)
            current = [p]
            current_x = cx
    if current:
        columns.append(current)
    out: list[dict[str, Any]] = []
    for col in columns:
        out.extend(sorted(col, key=lambda p: _box(p)[1]))
    return out


def order_for_reading(lines: list[dict]) -> list[dict[str, Any]]:
    """Pick horizontal or vertical Japanese order."""
    if looks_vertical(lines):
        return sort_vertical_japanese(lines)
    return sort_horizontal(lines)


def merge_soft_wraps(lines: list[dict]) -> list[dict[str, Any]]:
    """Join only mid-sentence wraps for clipboard plain-copy.

    Unlike:func:`merge_paragraphs`, finished sentences (``.!?``) stay on their
    own lines — otherwise a tall chat/IDE selection collapses into one mush.
    """
    if not lines:
        return []
    out: list[dict[str, Any]] = []
    cur = dict(lines[0])
    cur["_n"] = 1
    for nxt in lines[1:]:
        cx1, cy1, cx2, cy2 = _box(cur)
        nx1, ny1, nx2, ny2 = _box(nxt)
        gap = ny1 - cy2
        cur_lh = max(8.0, float(cur.get("line_height") or (cy2 - cy1) or 12))
        nxt_lh = max(8.0, float(nxt.get("line_height") or (ny2 - ny1) or 12))
        lh = max(8.0, (cur_lh + nxt_lh) / 2.0)
        cur_text = str(cur.get("text", ""))
        nxt_text = str(nxt.get("text", ""))
        cur_r = cur_text.rstrip()
        # Soft wrap only: lowercase continuation or hanging comma/dash.
        cont = bool(nxt_text[:1].islower() or cur_r.endswith((",", "—", "-", "/", "…")))
        left_ok = abs(cx1 - nx1) <= max(18, lh)
        height_ok = abs(cur_lh - nxt_lh) <= max(5, lh * 0.45)
        if (
            gap >= 0
            and gap <= lh * 0.55
            and left_ok
            and height_ok
            and cont
            and cur["_n"] < 5
        ):
            cur["text"] = f"{cur_r} {nxt_text.lstrip()}"
            cur["box"] = (min(cx1, nx1), min(cy1, ny1), max(cx2, nx2), max(cy2, ny2))
            n = int(cur["_n"])
            cur["line_height"] = int(round((cur_lh * n + nxt_lh) / (n + 1)))
            cur["_n"] = n + 1
        else:
            out.append({k: v for k, v in cur.items() if k != "_n"})
            cur = dict(nxt)
            cur["_n"] = 1
    out.append({k: v for k, v in cur.items() if k != "_n"})
    return out


def paragraphs_without_crossing_columns(
    lines: list[dict],
    *,
    region_width: int | None = None,
    merge_fn=None,
) -> list[dict[str, Any]]:
    """merge paragraphs but do not glue two columns by shared y.

    When gutters/column bounds are available, each column is merged on its own
    and results are concatenated in reading order (left→right for horizontal).

    ``merge_fn`` defaults to live:func:`merge_paragraphs`. Clipboard postprocess
    passes:func:`merge_soft_wraps` so finished sentences keep their newlines.
    """
    if not lines:
        return []
    merge = merge_fn or merge_paragraphs
    ordered = order_for_reading(lines)
    rw = region_width
    if rw is None:
        rw = max((_box(p)[2] for p in ordered), default=0)
    bounds = column_bounds_from_centers(ordered, int(rw)) if rw else None
    if not bounds or len(bounds) < 2:
        return list(merge([dict(p) for p in ordered]) or ordered)

    # Assign each line to a column by center x; merge per column separately.
    cols: list[list[dict]] = [[] for _ in bounds]
    for p in ordered:
        cx = (_box(p)[0] + _box(p)[2]) / 2.0
        best_i = 0
        best_d = 1e18
        for i, (x1, x2) in enumerate(bounds):
            mid = (x1 + x2) / 2.0
            d = abs(cx - mid)
            if d < best_d:
                best_d = d
                best_i = i
        cols[best_i].append(dict(p))

    out: list[dict[str, Any]] = []
    for col in cols:
        if not col:
            continue
        merged = merge(col) or col
        out.extend(dict(p) for p in merged)
    return out
