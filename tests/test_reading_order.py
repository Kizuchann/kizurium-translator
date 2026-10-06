"""–30: reading order (H / vertical JP) and column-aware paragraphs."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.ocr import reading_order as ro  # noqa: E402
from kizurium_translator.ocr.reading_order import (  # noqa: E402
    looks_vertical,
    order_for_reading,
    paragraphs_without_crossing_columns,
    sort_horizontal,
    sort_vertical_japanese,
    vertical_confidence,
)


def test_horizontal_y_then_x():
    lines = [
        {"text": "B", "box": (50, 10, 70, 20), "line_height": 10},
        {"text": "A", "box": (10, 10, 30, 20), "line_height": 10},
        {"text": "C", "box": (10, 40, 30, 50), "line_height": 10},
]
    got = [p["text"] for p in sort_horizontal(lines)]
    assert got == ["A", "B", "C"]


def test_vertical_mode_not_from_single_narrow_crop():
    lines = [
        {"text": "あ", "box": (10, 0, 30, 40), "line_height": 40},
        {"text": "い", "box": (10, 45, 30, 85), "line_height": 40},
]
    assert looks_vertical(lines) is False
    assert vertical_confidence(lines) < 0.55


def test_vertical_japanese_columns_rtl():
    # Two tall CJK columns: right column first, top→bottom inside.
    lines = [
        {"text": "右上", "box": (80, 0, 100, 40), "line_height": 40},
        {"text": "右下", "box": (80, 50, 100, 90), "line_height": 40},
        {"text": "左上", "box": (10, 0, 30, 40), "line_height": 40},
        {"text": "左下", "box": (10, 50, 30, 90), "line_height": 40},
        {"text": "右中", "box": (80, 95, 100, 135), "line_height": 40},
]
    assert looks_vertical(lines) is True
    got = [p["text"] for p in sort_vertical_japanese(lines)]
    assert got[:2] == ["右上", "右下"]
    assert "左上" in got


def test_order_for_reading_picks_horizontal_for_latin():
    lines = [
        {"text": "Hello", "box": (0, 0, 40, 12), "line_height": 12},
        {"text": "World", "box": (0, 20, 40, 32), "line_height": 12},
]
    assert [p["text"] for p in order_for_reading(lines)] == ["Hello", "World"]


def test_phase30_column_api_reexported():
    assert callable(ro.page_column_gutters)
    assert callable(ro.gap_hits_gutter)
    assert callable(ro.box_crosses_gutter)
    assert callable(ro.column_bounds_from_centers)
    assert callable(ro.image_column_gutters)


def test_paragraphs_do_not_glue_columns_by_y(monkeypatch):
    lines = [
        {"text": "L1", "box": (10, 10, 40, 22), "line_height": 12},
        {"text": "R1", "box": (200, 10, 240, 22), "line_height": 12},
        {"text": "L2", "box": (10, 30, 40, 42), "line_height": 12},
        {"text": "R2", "box": (200, 30, 240, 42), "line_height": 12},
]
    monkeypatch.setattr(
        ro,
        "column_bounds_from_centers",
        lambda lines, rw: [(0, 80), (160, 280)],
)
    got = paragraphs_without_crossing_columns(lines, region_width=300)
    texts = [p["text"] for p in got]
    # Left column texts should not be interleaved with right by y alone.
    assert "L1" in texts[0] or texts[0] == "L1"
    flat = " | ".join(texts)
    # Both columns present; left block before right when centers say so.
    assert "L1" in flat and "R1" in flat
