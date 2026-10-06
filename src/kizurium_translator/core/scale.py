"""Scale FHD-calibrated pixel constants to the current capture size.

Product code must not assume the user's panel is 1920×1080. Thresholds that
were tuned on a full-HD laptop are expressed here as reference values and
scaled by the active region (or probe) size so 1366×768, 1440p, ultrawide and
4K behave the same way.
"""

from __future__ import annotations

# Reference size the absolute constants were tuned against (not a product default).
REF_W = 1920.0
REF_H = 1080.0


def scale_px(px: float, size: float, *, ref: float = REF_W, floor: int = 1) -> int:
    """Map an FHD-tuned pixel length onto ``size`` (usually region width or max side)."""
    s = max(1.0, float(size))
    r = max(1.0, float(ref))
    return max(int(floor), int(round(float(px) * s / r)))


def scale_pair(
    px: float,
    width: float,
    height: float,
    *,
    ref: float = REF_W,
    floor: int = 1,
) -> int:
    """Scale by the longer side — orientation-agnostic for square/tall crops."""
    return scale_px(px, max(float(width), float(height)), ref=ref, floor=floor)


def wide_enough_for_columns(width: int, height: int) -> bool:
    """Whether a capture is wide enough to bother with multi-column OCR.

    Tuned as ``w >= 900`` on FHD; smaller laptops and half-width selections must
    still qualify when the crop is clearly landscape.
    """
    w = int(width)
    h = max(1, int(height))
    return w >= max(640, int(h * 1.15))


def px_per_char(line_height: float, *, mult: float = 0.7) -> int:
    """Estimate glyph pitch from the line's own height (HiDPI-safe)."""
    lh = max(8.0, float(line_height or 0.0))
    return max(8, int(round(lh * mult)))
