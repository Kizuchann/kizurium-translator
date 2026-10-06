"""Hierarchical live change detection

Pipeline (no OpenCV — PIL ``ImageChops`` + optional numpy only):

```text
cheap probe
→ tile / region diff
→ mask current/previous overlay cards
→ dirty signal
→ associate with tracked blocks
→ new-text heuristic (outside known blocks)
→ partial OCR (caller)
```

Thresholds for dialogue vs HUD differ because their noise profiles differ.
The detector answers *that* something moved; *what* to re-read is decided by
``dirty_block_ids`` and ``change_outside_blocks_is_text``.
"""
from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageChops, ImageStat

from ..core.models import (  # noqa: F401
    boxes_overlap,
)
from .runtime import (  # noqa: F401
    SETTINGS,
)
from .state import (  # noqa: F401
    TrackedBlock,
)

# Named stages for tests / docs.
PIPELINE_STAGES: tuple[str, ...] = (
    "probe",
    "tile_diff",
    "mask_overlay",
    "dirty_blocks",
    "new_text_heuristic",
    "partial_ocr",
)

# starting points.
PROBE_INTERVAL_S = 0.11
PROBE_MAX_SIDE = 320
PROBE_TILE_SIZE = 16


def probe(img, side: int | None = None) -> Image.Image:
    """Small grayscale thumbnail.

    The change detector only needs to know *that* something moved, so it runs on
    this instead of the full-resolution frame: no full-size RGB copies, no
    full-size diff, and one thumbnail reused for every later comparison.
    """
    target = side or SETTINGS.probe_side or PROBE_MAX_SIDE
    w, h = img.size
    scale = target / float(max(w, h))
    if scale < 1.0:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.BILINEAR)
    return img.convert("L")


def frame_diff_metrics(a, b) -> tuple[float, float, float]:
    """global mean, center mean, max tile mean, on grayscale probes."""
    if a.size != b.size:
        return 999.0, 999.0, 999.0
    diff = ImageChops.difference(a, b)
    nw, nh = diff.size
    global_mean = float(ImageStat.Stat(diff).mean[0])
    cx1, cy1 = nw // 5, nh // 5
    cx2, cy2 = nw - cx1, nh - cy1
    if cx2 > cx1 and cy2 > cy1:
        center = diff.crop((cx1, cy1, cx2, cy2))
        center_mean = float(ImageStat.Stat(center).mean[0])
    else:
        center_mean = global_mean
    tile_max = tile_diff_max_mean(diff, tile_size=PROBE_TILE_SIZE)
    return global_mean, center_mean, tile_max


def tile_diff_metrics(
    a,
    b,
    *,
    tile_size: int = PROBE_TILE_SIZE,
    pixel_threshold: float = 24.0,
) -> tuple[float, float]:
    """per-probe tile stats.

    Returns ``(max_mean_abs, max_fraction_over_threshold)`` across tiles.
    """
    if a.size != b.size:
        return 999.0, 1.0
    diff = ImageChops.difference(a, b)
    return _tile_stats(diff, tile_size=tile_size, pixel_threshold=pixel_threshold)


def tile_diff_max_mean(diff: Image.Image, *, tile_size: int = PROBE_TILE_SIZE) -> float:
    mean, _frac = _tile_stats(diff, tile_size=tile_size, pixel_threshold=24.0)
    return mean


# starting points  — calibrate on real scenes later.
DIRTY_PIXEL_THRESHOLD = 24.0
DIRTY_FRACTION = 0.015
DIRTY_MERGE_GAP_FULL_PX = 40  # mid of 32–48 full-res
DIRTY_REGION_PAD_PROBE = 2


def dirty_tile_coords(
    a,
    b,
    *,
    tile_size: int = PROBE_TILE_SIZE,
    pixel_threshold: float = DIRTY_PIXEL_THRESHOLD,
    dirty_fraction: float = DIRTY_FRACTION,
) -> set[tuple[int, int]]:
    """``(tile_x, tile_y)`` cells whose change fraction exceeds the gate."""
    if a.size != b.size:
        return set()
    diff = ImageChops.difference(a.convert("L"), b.convert("L"))
    nw, nh = diff.size
    ts = max(4, int(tile_size))
    dirty: set[tuple[int, int]] = set()
    for ty, y1 in enumerate(range(0, nh, ts)):
        for tx, x1 in enumerate(range(0, nw, ts)):
            x2 = min(nw, x1 + ts)
            y2 = min(nh, y1 + ts)
            if x2 <= x1 or y2 <= y1:
                continue
            patch = diff.crop((x1, y1, x2, y2))
            mean = float(ImageStat.Stat(patch).mean[0])
            try:
                import numpy as np

                arr = np.asarray(patch, dtype=np.float32)
                frac = float((arr > pixel_threshold).mean()) if arr.size else 0.0
            except Exception:  # noqa: BLE001
                frac = min(1.0, mean / max(1.0, pixel_threshold * 2))
            if frac >= dirty_fraction or mean >= pixel_threshold:
                dirty.add((tx, ty))
    return dirty


def group_dirty_tiles_bfs(tiles: set[tuple[int, int]]) -> list[list[tuple[int, int]]]:
    """8-neighbour connected components over dirty tile coordinates."""
    if not tiles:
        return []
    remaining = set(tiles)
    components: list[list[tuple[int, int]]] = []
    neighbours = (
        (-1, -1),
        (0, -1),
        (1, -1),
        (-1, 0),
        (1, 0),
        (-1, 1),
        (0, 1),
        (1, 1),
    )
    while remaining:
        start = remaining.pop()
        stack = [start]
        comp = [start]
        while stack:
            cx, cy = stack.pop()
            for dx, dy in neighbours:
                n = (cx + dx, cy + dy)
                if n in remaining:
                    remaining.remove(n)
                    stack.append(n)
                    comp.append(n)
        components.append(comp)
    return components


def _tiles_bbox(
    tiles: list[tuple[int, int]],
    *,
    tile_size: int,
    probe_size: tuple[int, int],
) -> tuple[int, int, int, int]:
    ts = max(4, int(tile_size))
    pw, ph = probe_size
    xs = [t[0] for t in tiles]
    ys = [t[1] for t in tiles]
    x1 = max(0, min(xs) * ts)
    y1 = max(0, min(ys) * ts)
    x2 = min(pw, (max(xs) + 1) * ts)
    y2 = min(ph, (max(ys) + 1) * ts)
    return x1, y1, x2, y2


def _expand_rect(
    box: tuple[int, int, int, int],
    pad: int,
    size: tuple[int, int],
) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = box
    w, h = size
    return (
        max(0, x1 - pad),
        max(0, y1 - pad),
        min(w, x2 + pad),
        min(h, y2 + pad),
    )


def _rects_gap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> int:
    """Axis-aligned gap between rects (0 if they touch/overlap)."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    dx = max(0, max(ax1, bx1) - min(ax2, bx2))
    dy = max(0, max(ay1, by1) - min(ay2, by2))
    if ax2 >= bx1 and bx2 >= ax1 and ay2 >= by1 and by2 >= ay1:
        return 0
    if ax2 >= bx1 and bx2 >= ax1:
        return dy
    if ay2 >= by1 and by2 >= ay1:
        return dx
    return max(dx, dy)


def merge_close_rects(
    rects: list[tuple[int, int, int, int]],
    *,
    gap: int,
) -> list[tuple[int, int, int, int]]:
    """Merge rectangles whose edge gap is ≤ ``gap`` pixels."""
    if not rects:
        return []
    out = [tuple(r) for r in rects]
    changed = True
    while changed and len(out) > 1:
        changed = False
        nxt: list[tuple[int, int, int, int]] = []
        used = [False] * len(out)
        for i, a in enumerate(out):
            if used[i]:
                continue
            cur = a
            for j in range(i + 1, len(out)):
                if used[j]:
                    continue
                if _rects_gap(cur, out[j]) <= gap:
                    bx1, by1, bx2, by2 = out[j]
                    cur = (
                        min(cur[0], bx1),
                        min(cur[1], by1),
                        max(cur[2], bx2),
                        max(cur[3], by2),
                    )
                    used[j] = True
                    changed = True
            used[i] = True
            nxt.append(cur)
        out = nxt
    return out


def dirty_regions(
    a,
    b,
    *,
    tile_size: int = PROBE_TILE_SIZE,
    pixel_threshold: float = DIRTY_PIXEL_THRESHOLD,
    dirty_fraction: float = DIRTY_FRACTION,
    pad: int = DIRTY_REGION_PAD_PROBE,
    merge_gap_full_px: int = DIRTY_MERGE_GAP_FULL_PX,
    source_size: tuple[int, int] | None = None,
) -> list[tuple[int, int, int, int]]:
    """dirty tiles → BFS components → padded rects → merge close ones.

    Boxes are in **probe** coordinates unless ``source_size`` is set, in which
    case they are scaled to full-resolution pixels.
    """
    if a.size != b.size:
        return []
    tiles = dirty_tile_coords(
        a,
        b,
        tile_size=tile_size,
        pixel_threshold=pixel_threshold,
        dirty_fraction=dirty_fraction,
    )
    if not tiles:
        return []
    components = group_dirty_tiles_bfs(tiles)
    pw, ph = a.size
    rects = [
        _expand_rect(
            _tiles_bbox(comp, tile_size=tile_size, probe_size=(pw, ph)),
            pad,
            (pw, ph),
        )
        for comp in components
    ]
    # Map full-res merge gap into probe space.
    if source_size:
        scale = max(pw, ph) / float(max(source_size))
        gap = max(1, int(round(merge_gap_full_px * scale)))
    else:
        # No assumed 1920 panel — probe space is already the reference.
        gap = max(1, int(round(merge_gap_full_px * max(pw, ph) / float(max(pw, ph, 1)))))
    merged = merge_close_rects(rects, gap=gap)
    if not source_size:
        return merged
    sx = source_size[0] / float(pw)
    sy = source_size[1] / float(ph)
    return [
        (
            int(x1 * sx),
            int(y1 * sy),
            int(x2 * sx),
            int(y2 * sy),
        )
        for x1, y1, x2, y2 in merged
    ]


def _tile_stats(
    diff: Image.Image,
    *,
    tile_size: int,
    pixel_threshold: float,
) -> tuple[float, float]:
    nw, nh = diff.size
    ts = max(4, int(tile_size))
    max_mean = 0.0
    max_frac = 0.0
    for y1 in range(0, nh, ts):
        for x1 in range(0, nw, ts):
            x2 = min(nw, x1 + ts)
            y2 = min(nh, y1 + ts)
            if x2 <= x1 or y2 <= y1:
                continue
            patch = diff.crop((x1, y1, x2, y2))
            mean = float(ImageStat.Stat(patch).mean[0])
            max_mean = max(max_mean, mean)
            # Fraction of pixels over threshold (luminance delta).
            try:
                import numpy as np

                arr = np.asarray(patch, dtype=np.float32)
                frac = float((arr > pixel_threshold).mean()) if arr.size else 0.0
            except Exception:  # noqa: BLE001
                # Fallback without numpy: mean as a coarse stand-in.
                frac = min(1.0, mean / max(1.0, pixel_threshold * 2))
            max_frac = max(max_frac, frac)
    return max_mean, max_frac


def frame_changed(a, b, threshold: float = SETTINGS.change_mean) -> bool:
    """Catches both large motion and a text swap on an otherwise static UI."""
    if a.size != b.size:
        return True
    g, c, tile = frame_diff_metrics(a, b)
    if g > threshold:
        return True
    return tile > SETTINGS.change_tile or c > SETTINGS.change_center


# mask overlay cards on the probe.
OVERLAY_MASK_PAD_PX = 6  # mid of 4–8 full-res


def _overlay_xywh(bl: dict) -> tuple[int, int, int, int]:
    x = int(bl.get("x", 0))
    y = int(bl.get("y", 0))
    w = int(bl.get("src_w", bl.get("w", 40)))
    h = int(bl.get("src_h", bl.get("h", 16)))
    return x, y, w, h


def union_overlay_boxes(
    current: list[dict] | None,
    previous: list[dict] | None = None,
) -> list[dict]:
    """``union(current overlay boxes, previous overlay boxes)`` for probe masks."""
    out: list[dict] = []
    seen: set[tuple[int, int, int, int]] = set()
    for src in (current or [], previous or []):
        for bl in src:
            key = _overlay_xywh(bl)
            if key in seen:
                continue
            seen.add(key)
            out.append({"x": key[0], "y": key[1], "src_w": key[2], "src_h": key[3]})
    return out


def apply_overlay_mask(
    probe_a,
    probe_b,
    boxes: list[dict] | None,
    source_size: tuple[int, int],
    *,
    pad: int = OVERLAY_MASK_PAD_PX,
):
    """Paint overlay card areas to black on both probes

    Returns ``(masked_a, masked_b)``. Empty ``boxes`` → copies unchanged.
    """
    a = probe_a.copy()
    b = probe_b.copy()
    if not boxes:
        return a, b
    from PIL import ImageDraw

    sx = a.width / max(1, source_size[0])
    sy = a.height / max(1, source_size[1])
    da, db = ImageDraw.Draw(a), ImageDraw.Draw(b)
    for bl in boxes:
        x, y, w, h = _overlay_xywh(bl)
        box = [
            max(0, int((x - pad) * sx)),
            max(0, int((y - pad) * sy)),
            min(a.width, int((x + w + pad) * sx)),
            min(a.height, int((y + h + pad) * sy)),
        ]
        if box[2] > box[0] and box[3] > box[1]:
            da.rectangle(box, fill=0)
            db.rectangle(box, fill=0)
    return a, b


def probe_changed(
    probe_a,
    probe_b,
    blocks: list[dict] | None,
    source_size: tuple[int, int],
    threshold: float = SETTINGS.change_mean,
    *,
    previous_blocks: list[dict] | None = None,
) -> bool:
    """Compare two probes, ignoring the area our own cards occupy.

    Masking happens on the thumbnail, so the cost does not depend on the size of
    the selected region. Without it the overlay always looks like motion.
    """
    if probe_a.size != probe_b.size:
        return True
    boxes = union_overlay_boxes(blocks, previous_blocks)
    if not boxes:
        return frame_changed(probe_a, probe_b, threshold)
    try:
        a, b = apply_overlay_mask(probe_a, probe_b, boxes, source_size)
        return frame_changed(a, b, threshold)
    except Exception:
        return frame_changed(probe_a, probe_b, threshold)


DIRTY_PROBE_THRESHOLD = 26  # probe levels counted as a change


DIRTY_PAD = 6


DIRTY_BLOCK_MEAN = 6.0


DIRTY_GROUPS_MAX = 3


DIRTY_JOIN_PAD = 48


# when to abandon incremental and run full OCR.
REANCHOR_DIRTY_REGIONS_MAX = 8
REANCHOR_MATCH_RATE_MIN = 0.65
REANCHOR_PERIODIC_S = 5.0


def needs_full_reanchor(
    *,
    tracked_count: int,
    strong_scene_change: bool = False,
    dirty_region_count: int = 0,
    match_rate: float | None = None,
    seconds_since_full: float = 0.0,
) -> tuple[bool, str]:
    """Decide whether this frame needs a full OCR re-anchor.

    Returns ``(needed, reason)``. Reasons are stable strings for logs/tests.
    """
    if tracked_count <= 0:
        return True, "no_tracked"
    if strong_scene_change:
        return True, "scene_change"
    if dirty_region_count > REANCHOR_DIRTY_REGIONS_MAX:
        return True, "many_dirty"
    if match_rate is not None and match_rate < REANCHOR_MATCH_RATE_MIN:
        return True, "low_match"
    if seconds_since_full >= REANCHOR_PERIODIC_S:
        return True, "periodic"
    return False, ""


def dirty_block_ids(
    probe_a,
    probe_b,
    blocks: list[TrackedBlock],
    rw: int,
    rh: int,
    threshold: float = DIRTY_BLOCK_MEAN,
    means: list[str] | None = None,
) -> list[int]:
    """Ids of the elements whose own pixels changed.

    Scoring each element separately instead of taking one bounding box of the
    whole diff: a blinking cursor or a progress bar elsewhere in the region
    would otherwise drag the "changed area" across the frame and force a full
    pass every time.
    """
    if not blocks or probe_a.size != probe_b.size:
        return []
    try:
        diff = ImageChops.difference(probe_a, probe_b)
    except Exception:  # noqa: BLE001
        return []
    pw, ph = probe_a.size
    sx, sy = pw / max(1, rw), ph / max(1, rh)
    out: list[int] = []
    for blk in blocks:
        x1 = max(0, int(blk.box[0] * sx))
        y1 = max(0, int(blk.box[1] * sy))
        x2 = min(pw, max(x1 + 1, int(blk.box[2] * sx)))
        y2 = min(ph, max(y1 + 1, int(blk.box[3] * sy)))
        if x2 <= x1 or y2 <= y1:
            continue
        patch = diff.crop((x1, y1, x2, y2))
        if not patch.size[0] or not patch.size[1]:
            continue
        mean = float(ImageStat.Stat(patch).mean[0])
        if means is not None:
            means.append(f"{blk.text[:14]}={mean:.0f}")
        if mean > threshold:
            out.append(blk.id)
    return out


CHANGE_PIXEL = 24


CHANGE_AREA_MIN = 0.004


CHANGE_EDGE_SPAN = 40


CHANGE_RUNS_MIN = 2.5


# text vs motion. UNKNOWN is conservative (do not skip OCR).
TEXT_LIKELY = "TEXT_LIKELY"
MOTION_LIKELY = "MOTION_LIKELY"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ChangeFeatures:
    """Signals the classifier uses to separate ink from motion."""

    changed_area: float = 0.0
    changed_tile_count: int = 0
    component_count: int = 0
    mean_component_size: float = 0.0
    edge_density: float = 0.0
    temporal_persistence: float = 0.0


def classify_change(features: ChangeFeatures) -> str:
    """``TEXT_LIKELY`` / ``MOTION_LIKELY`` / ``UNKNOWN``.

    ``UNKNOWN`` must not skip OCR — it is the conservative path when the
    feature set is ambiguous.
    """
    area = float(features.changed_area)
    num = int(features.component_count)
    mean_size = float(features.mean_component_size)
    tiles = int(features.changed_tile_count)
    edge = float(features.edge_density)
    persist = float(features.temporal_persistence)

    if area <= CHANGE_AREA_MIN and tiles <= 0 and num <= 0:
        return MOTION_LIKELY

    # Changed pixels with no ink structure: cursor-scale or flat fill motion.
    if num <= 0:
        return MOTION_LIKELY

    # Persistent large-area motion without glyph-like components → animation.
    if persist >= 3.0 and area >= 0.02 and num <= 1 and mean_size < 30:
        return MOTION_LIKELY

    # Bar: one huge component (the whole edge)
    if num == 1 and mean_size > 500:
        return MOTION_LIKELY
    # Cursor: single tiny component
    if num == 1 and mean_size < 30:
        return MOTION_LIKELY
    # Gradient / noise: many tiny components
    if num > 15 and mean_size < 50:
        return MOTION_LIKELY
    # Broad motion with almost no edge structure
    if area >= 0.08 and edge < 0.01 and num <= 2:
        return MOTION_LIKELY

    # Text: few components, each substantial
    if num <= 10 and mean_size > 30 and edge >= 0.005:
        return TEXT_LIKELY
    if 2 <= num <= 12 and mean_size > 25 and tiles <= 12:
        return TEXT_LIKELY

    return UNKNOWN


def change_outside_blocks_is_text(
    probe_a,
    probe_b,
    blocks: list,
    rw: int,
    rh: int,
    *,
    temporal_persistence: float = 0.0,
) -> tuple[bool, float, float]:
    """Whether the movement outside every known element looks like new text.

    Returns ``(looks_like_text, changed_area, runs_per_row)``.

    This is the question the change detector kept skipping: when nothing it
    tracks moved, the answer was to read the whole frame again on the reasoning
    that new text must have appeared somewhere, and that is what a video playing
    behind a panel, a health bar or a blinking cursor pays for on every frame.

    So the change is measured and then read for what it is. Motion without ink is
    not text, and text arriving where nothing was is ink. The measure is local
    contrast in the new frame rather than brightness, because the answer has to
    come out the same on a dark screen with pale type and on a pale page with dark
    type - and it counts runs rather than comparing totals, because the edge of a
    bar is exactly as sharp as the stem of a letter.

    When the question cannot be answered - mismatched probe sizes - it answers
    yes: a guess in the direction of reading the screen too little is the one
    that loses text. Wraps the same signals as ``classify_change``, so ``UNKNOWN``
    still returns True.
    """
    features = measure_outside_change(
        probe_a, probe_b, blocks, rw, rh, temporal_persistence=temporal_persistence
    )
    if features is None:
        return True, 1.0, 99.0
    verdict = classify_change(features)
    looks = verdict in (TEXT_LIKELY, UNKNOWN)
    return looks, features.changed_area, features.mean_component_size


def measure_outside_change(
    probe_a,
    probe_b,
    blocks: list,
    rw: int,
    rh: int,
    *,
    temporal_persistence: float = 0.0,
) -> ChangeFeatures | None:
    """Collect change-detection features for pixels outside every known block."""
    try:
        import numpy as np
    except ImportError:  # pragma: no cover - numpy ships with the project
        return None
    try:
        a = np.asarray(probe_a, dtype=np.int16)
        b = np.asarray(probe_b, dtype=np.int16)
    except Exception:  # noqa: BLE001
        return None
    if a.shape != b.shape or a.size == 0:
        return None

    changed = np.abs(a - b) > CHANGE_PIXEL
    area = float(changed.mean())
    if area <= CHANGE_AREA_MIN:
        return ChangeFeatures(
            changed_area=area,
            temporal_persistence=temporal_persistence,
        )

    outside = changed.copy()
    ph, pw = a.shape
    sx, sy = pw / max(1, rw), ph / max(1, rh)
    for blk in blocks or []:
        x1 = max(0, int(blk.box[0] * sx))
        y1 = max(0, int(blk.box[1] * sy))
        x2 = min(pw, max(x1 + 1, int(blk.box[2] * sx) + 1))
        y2 = min(ph, max(y1 + 1, int(blk.box[3] * sy) + 1))
        outside[y1:y2, x1:x2] = False
    if int(outside.sum()) == 0:
        return ChangeFeatures(
            changed_area=area,
            temporal_persistence=temporal_persistence,
        )

    bf = b.astype(np.float32)
    hi = np.full_like(bf, -1e9)
    lo = np.full_like(bf, 1e9)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            shifted = np.roll(np.roll(bf, dy, axis=0), dx, axis=1)
            hi = np.maximum(hi, shifted)
            lo = np.minimum(lo, shifted)
    ink = (hi - lo) > CHANGE_EDGE_SPAN
    edge_density = float((ink & outside).mean()) if outside.size else 0.0

    cur = ink & outside
    ph, pw = cur.shape
    vis = np.zeros_like(cur, dtype=bool)
    components: list[int] = []
    for y in range(ph):
        for x in range(pw):
            if cur[y, x] and not vis[y, x]:
                stack = [(y, x)]
                size = 0
                while stack:
                    cy, cx = stack.pop()
                    if cy < 0 or cy >= ph or cx < 0 or cx >= pw:
                        continue
                    if vis[cy, cx] or not cur[cy, cx]:
                        continue
                    vis[cy, cx] = True
                    size += 1
                    if cy > 0:
                        stack.append((cy - 1, cx))
                    if cy + 1 < ph:
                        stack.append((cy + 1, cx))
                    if cx > 0:
                        stack.append((cy, cx - 1))
                    if cx + 1 < pw:
                        stack.append((cy, cx + 1))
                components.append(size)
    num = len(components)
    mean_size = float(np.mean(components)) if components else 0.0

    # Tile count over the outside change mask (probe grid).
    tile = max(4, int(PROBE_TILE_SIZE))
    tile_count = 0
    for y1 in range(0, ph, tile):
        for x1 in range(0, pw, tile):
            y2 = min(ph, y1 + tile)
            x2 = min(pw, x1 + tile)
            if bool(outside[y1:y2, x1:x2].any()):
                tile_count += 1

    return ChangeFeatures(
        changed_area=area,
        changed_tile_count=tile_count,
        component_count=num,
        mean_component_size=mean_size,
        edge_density=edge_density,
        temporal_persistence=temporal_persistence,
    )


def _boxes_overlap(
    a: tuple[int, int, int, int], b: tuple[int, int, int, int], pad: int = DIRTY_PAD
) -> bool:
    return boxes_overlap(a, b, pad)


def _scene_swap_is_likely(
    before,
    after,
    blocks: list[dict] | None,
    size: tuple[int, int],
) -> bool:
    """Whether the frame underneath changed more than one region being edited.

    A static page changes nothing outside its cards. A scene swap changes
    almost everything, and the cards it is judged against belong to the frame
    that just went away. Comparing the frame with itself, cards covered, leaves
    the new content visible, so this asks how much of what is left over the
    masked cards actually moved.
    """
    if before is None or after is None or before.size != after.size:
        return False
    try:
        boxes = union_overlay_boxes(blocks, None)
        a, b = apply_overlay_mask(before, after, boxes, size)
        g, c, tile = frame_diff_metrics(a, b)
    except Exception:  # noqa: BLE001
        return False
    # A page edit is a few percent of the frame. A swap is most of it, so the
    # bar sits between the two rather than on either of them.
    return g > SCENE_SWAP_MEAN or tile > SCENE_SWAP_TILE


SCENE_SWAP_MEAN = 26.0


SCENE_SWAP_TILE = 60.0
