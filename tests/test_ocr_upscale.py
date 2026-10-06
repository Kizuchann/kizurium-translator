""": upscale tiny-ink crops (2×, then 4×) before OCR."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.ocr.preprocess import (  # noqa: E402
    TINY_INK_THRESHOLD,
    choose_upscale,
    estimate_ink_height,
    prepare_for_ocr,
    result_looks_unusable,
    scale_boxes_down,
)


def _ink_strip(height: int, width: int = 120) -> Image.Image:
    """White canvas with a dark horizontal bar of the given height."""
    img = Image.new("RGB", (width, max(height + 4, 8)), "white")
    draw = ImageDraw.Draw(img)
    y0 = 2
    draw.rectangle((4, y0, width - 4, y0 + height - 1), fill="black")
    return img


def test_threshold_constant():
    assert TINY_INK_THRESHOLD == 10


def test_estimate_ink_height_on_bar():
    for h in (6, 8, 10, 12, 16, 24):
        got = estimate_ink_height(_ink_strip(h))
        assert abs(got - h) <= 2, f"h={h} got={got}"


def test_choose_upscale_2x_when_tiny():
    assert choose_upscale(6).scale == 2
    assert choose_upscale(9).scale == 2
    assert choose_upscale(10).scale == 1
    assert choose_upscale(24).scale == 1


def test_choose_upscale_4x_after_bad_2x():
    plan = choose_upscale(6, previous_scale=2, still_bad=True)
    assert plan.scale == 4


def test_prepare_for_ocr_does_not_upscale_large_ink():
    img = _ink_strip(24, width=200)
    out, scale = prepare_for_ocr(img)
    assert scale == 1
    assert out.size == img.size


def test_prepare_for_ocr_upscales_tiny():
    img = _ink_strip(6, width=80)
    out, scale = prepare_for_ocr(img)
    assert scale == 2
    assert out.size == (img.width * 2, img.height * 2)


def test_scale_boxes_down():
    lines = [{"text": "a", "box": (10, 20, 30, 40), "line_height": 20}]
    got = scale_boxes_down(lines, 2)
    assert got[0]["box"] == (5, 10, 15, 20)
    assert got[0]["line_height"] == 10


def test_result_looks_unusable():
    assert result_looks_unusable([]) is True
    assert result_looks_unusable([{"text": " "}]) is True
    assert result_looks_unusable([{"text": "Hi"}]) is False
