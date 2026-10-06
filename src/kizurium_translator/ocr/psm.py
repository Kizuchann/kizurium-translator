"""Adaptive Tesseract page segmentation (OCR).

PSM codes (https://tesseract-ocr.github.io/tessdoc/):

```text
8  = single word / character-like crop
7  = single line
6  = uniform block of text
11 = sparse text
```

Choice is driven by crop geometry only — not by language or engine scores.
"""

from __future__ import annotations

from PIL import Image


def choose_tesseract_psm(width: int, height: int) -> str:
    """Pick a PSM mode for a crop of ``width``×``height`` pixels."""
    w = max(1, int(width))
    h = max(1, int(height))
    aspect = w / h

    # Tiny / square-ish → treat as one word (or character).
    if w <= 72 and h <= 72 and aspect < 2.2:
        return "8"

    # Wide short strip → one line.
    if h <= 40 and aspect >= 2.0:
        return "7"
    if h <= 56 and aspect >= 5.0:
        return "7"

    # Large canvas (menus, sparse HUD) → find text without assuming one block.
    # Absolute 900×500 was FHD-tuned; also accept equal-or-larger *area* so a
    # 800×600 laptop menu qualifies without lowering the bar for tiny crops.
    if (w >= 900 and h >= 500) or (w * h >= 900 * 500):
        return "11"

    # Default: uniform text block (sites, dialogue boxes, panels).
    return "6"


def psm_for_image(img: Image.Image) -> str:
    """Convenience wrapper around:func:`choose_tesseract_psm`."""
    w, h = img.size
    return choose_tesseract_psm(w, h)
