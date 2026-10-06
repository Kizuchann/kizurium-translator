"""Capture-size scaling must not assume a 1920×1080 laptop panel."""

from __future__ import annotations

from kizurium_translator.core.scale import (
    px_per_char,
    scale_px,
    wide_enough_for_columns,
)
from kizurium_translator.ocr.psm import choose_tesseract_psm
from kizurium_translator.typography.metrics import merge_subtitle_cluster


def test_scale_px_grows_on_4k_and_shrinks_on_hd():
    assert scale_px(900, 1920) == 900
    assert scale_px(900, 3840) == 1800
    assert scale_px(900, 1366) == round(900 * 1366 / 1920)


def test_columns_ok_on_small_laptop_landscape():
    assert wide_enough_for_columns(1280, 800)
    assert wide_enough_for_columns(1366, 768)
    assert not wide_enough_for_columns(600, 800)  # portrait phone-like


def test_px_per_char_follows_line_height():
    assert px_per_char(20) >= 12
    assert px_per_char(40) > px_per_char(20)


def test_subtitle_merge_does_not_crop_ultrawide_to_1800():
    # Force the absolute-FHD clamp path: one already-wide union box.
    cluster = [
        {
            "text": "However you will always be the most important person to me Doctor",
            "box": (40, 900, 3400, 980),
            "line_height": 48,
            "conf": 90,
        },
    ]
    out = merge_subtitle_cluster(cluster, rw=3440, rh=1440)
    x1, _, x2, _ = out["box"]
    # Old code: span>1800 → clamp to ±900 around center (width 1800).
    assert (x2 - x1) > 2000, (x1, x2, x2 - x1)


def test_psm_large_area_without_requiring_fhd_width():
    assert choose_tesseract_psm(800, 600) == "11"  # area >= 900*500
    assert choose_tesseract_psm(400, 200) == "6"
    assert choose_tesseract_psm(1920, 1080) == "11"
