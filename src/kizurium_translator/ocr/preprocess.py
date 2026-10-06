"""OCR preprocessing for tiny text

When estimated ink height is below ``TINY_INK_THRESHOLD`` (10 px), upscale
2× before recognition. If the result is still unusable, try 4×. Do **not**
upscale every crop — only when the ink is actually tiny.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageOps, ImageStat

TINY_INK_THRESHOLD = 10


@dataclass(frozen=True)
class UpscalePlan:
    scale: int
    reason: str


def estimate_ink_height(img: Image.Image) -> float:
    """Rough glyph height from the vertical span of dark ink pixels.

    Uses a simple luminance threshold — good enough to decide 1× vs 2×/4×,
    not a full ink morphology pass.
    """
    if img is None:
        return 0.0
    gray = ImageOps.grayscale(img)
    # Adaptive-ish threshold: darker than mean - a bit, or fixed 140 on bright UI.
    mean = float(ImageStat.Stat(gray).mean[0])
    cutoff = min(140.0, max(60.0, mean - 25.0))
    w, h = gray.size
    if w * h == 0:
        return 0.0
    pix = gray.load()
    rows_with_ink: list[int] = []
    for y in range(h):
        for x in range(w):
            if pix[x, y] < cutoff:
                rows_with_ink.append(y)
                break
    if not rows_with_ink:
        # No dark ink — fall back to image height (likely already tiny crop).
        return float(h)
    return float(rows_with_ink[-1] - rows_with_ink[0] + 1)


def choose_upscale(ink_height: float, *, previous_scale: int = 1, still_bad: bool = False) -> UpscalePlan:
    """Decide upscale factor. ``still_bad`` means 2× already ran and OCR failed."""
    if still_bad and previous_scale < 4 and ink_height * previous_scale < TINY_INK_THRESHOLD * 2:
        return UpscalePlan(4, "tiny ink still unusable after 2x")
    if ink_height > 0 and ink_height < TINY_INK_THRESHOLD and previous_scale < 2:
        return UpscalePlan(2, f"ink_height={ink_height:.1f}<{TINY_INK_THRESHOLD}")
    return UpscalePlan(1, "no upscale needed")


def upscale_image(img: Image.Image, scale: int) -> Image.Image:
    if scale <= 1:
        return img
    w, h = img.size
    return img.resize(
        (max(1, w * scale), max(1, h * scale)),
        Image.Resampling.LANCZOS,
    )


def prepare_for_ocr(
    img: Image.Image,
    *,
    still_bad: bool = False,
    previous_scale: int = 1,
) -> tuple[Image.Image, int]:
    """Return ``(image_for_ocr, scale_applied)``.

    ``scale_applied`` is relative to the original ``img`` (1, 2, or 4).
    """
    ink = estimate_ink_height(img)
    # If we already upscaled the source, estimate on the current pixels but
    # decide absolute scale against the original intent via previous_scale.
    plan = choose_upscale(ink / max(1, previous_scale), previous_scale=previous_scale, still_bad=still_bad)
    if plan.scale <= previous_scale:
        return img if previous_scale == 1 else upscale_image(img, previous_scale), max(1, previous_scale)
    # Always upscale from the caller's image; callers pass the original crop.
    return upscale_image(img, plan.scale), plan.scale


def scale_boxes_down(lines: list[dict], scale: int) -> list[dict]:
    """Map OCR boxes from an upscaled image back to original coordinates."""
    if scale <= 1:
        return lines
    out: list[dict] = []
    for p in lines:
        copy = dict(p)
        box = p.get("box")
        if box and len(box) == 4:
            x1, y1, x2, y2 = box
            copy["box"] = (
                int(x1 / scale),
                int(y1 / scale),
                int(x2 / scale),
                int(y2 / scale),
            )
        if "line_height" in p:
            copy["line_height"] = max(1, int(p["line_height"] / scale))
        out.append(copy)
    return out


def result_looks_unusable(lines: list[dict]) -> bool:
    """True when OCR returned nothing useful after a tiny-text pass."""
    texts = [str(p.get("text", "")).strip() for p in lines]
    texts = [t for t in texts if t]
    if not texts:
        return True
    # Single garbage glyph / tiny fragment after upscale still counts as bad.
    joined = "".join(texts)
    return len(joined) < 2
